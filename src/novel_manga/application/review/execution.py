"""Review existing takes and record one shared episode execution result; never generate."""
import copy
from pathlib import Path

from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.review.episode import review_episode
from novel_manga.application.review.store import current_takes
from novel_manga.review.acceptance import evaluate_acceptance
from novel_manga.util import atomic_write_json, read_json


def media_matches(plan, media, *, notes=None):
    """Whether the existing report can enter repair without first rendering the current plan."""
    return bool(media.get('clips')
                and media.get('clip_plan_fingerprint') == plan_fingerprint(plan)
                and (not media.get('prompt_h3_fingerprint')
                     or media['prompt_h3_fingerprint'] == h3_prompt_fingerprint(plan))
                and (notes is None or (media.get('review_feedback') or {}) == notes))


def review_saved(directory: Path, *, fresh=None, video_name='clip.mp4'):
    if video_name == 'clip.mp4':
        refresh_speech_evidence(directory)
    review = review_episode(directory, video_name=video_name, fresh=fresh)
    if video_name == 'clip.mp4':
        plan = read_json(directory / 'clip_plan.json', {})
        history.observe(directory, review, current_takes(directory, plan, review))
    return review


def refresh_speech_evidence(directory):
    """A policy refresh uses existing audio evidence and cannot submit video."""
    from novel_manga.media import speech
    directory = Path(directory)
    plan = read_json(directory/'clip_plan.json', {})
    media = read_json(directory/'thin_media_report.json', {})
    if not media_matches(plan, media, notes=read_json(directory/'review_feedback.json', {})):
        return False
    clips = {c['clip_id']: c for c in plan.get('clips', [])}
    bible = read_json(directory.parent/'story_bible.json', {})
    terms = [c['name'] for c in bible.get('characters') or [] if c.get('name')]
    aliases = read_json(directory.parent/'asr_aliases.json', {})
    changed = False
    for row in media.get('clips') or []:
        selected = row.get('selected') or {}
        clip = clips.get(row.get('clip_id'))
        if (not clip or not selected.get('video') or 'reference' not in selected
                or selected.get('speech_evaluation_policy') == speech.EVALUATION_POLICY):
            continue
        row['selected'] = speech.refresh(clip.get('spoken_text', ''), selected, terms, aliases,
                                        vocalizations=speech.planned_vocalizations(clip))
        if media.get('assembly')and any(c.get('subtitle')=='rejected_unplanned_speech'for c in selected.get('chunks',[])):
            from novel_manga.media.subtitles import align_chunks
            aligned=align_chunks([clip.get('spoken_text','')],row['selected'].get('chunks',[]))
            if any(c.get('subtitle')=='rejected_unplanned_speech'and text for c,text,score,pieces in aligned):
                media['assembly']['needs_subtitle_refresh']=True
        changed = True
    if changed:
        selected = {r['clip_id']: r.get('selected') or {} for r in media['clips']}
        media['gate_failed_clips'] = [cid for cid in media.get('gate_failed_clips') or []
                                      if selected.get(cid, {}).get('passed') is not True]
        atomic_write_json(directory/'thin_media_report.json', media)
    return changed


def recheck_current_clip(directory: Path, cid: str) -> dict:
    """Review a keep proposal against unchanged inputs; publish only its current-take verdict."""
    from novel_manga.application.review import cast_video, confirmed, judges
    from novel_manga.application.profiles import speech_gate_result
    from novel_manga.media.speech_repair import correction, merge_note
    from novel_manga.models.bible import StoryBible
    from novel_manga.review.policy import compose_feedback, flag_line
    from novel_manga.application.review.evidence import review_mode
    from novel_manga.review.contracts import POLICY, current_review

    directory = Path(directory)
    refresh_speech_evidence(directory)
    plan = read_json(directory / 'clip_plan.json', {})
    script = read_json(directory / 'chapter_script.json', {})
    notes = read_json(directory / 'review_feedback.json', {})
    previous = read_json(directory / 'episode_review.json', {})
    mode = 'verify' if review_mode(directory/'work/keep_review'/cid) == 'verify' else 'classic'
    clip = next((c for c in plan.get('clips', []) if c.get('clip_id') == cid and c.get('kind') == 'video'), None)
    current = current_takes(directory, plan, previous).get(cid)
    if clip is None or current is None:
        raise ValueError(f'{cid}: keep recheck requires a current clip and selected video')
    media = read_json(directory / 'thin_media_report.json', {})
    selected = next((r.get('selected') or {} for r in media.get('clips', []) if r.get('clip_id') == cid), {})
    bible = StoryBible.model_validate(read_json(directory.parent / 'story_bible.json', {}))
    times = read_json(directory.parent / 'visual_grammar.json', {}).get('location_time', {})
    checked = copy.deepcopy(clip)
    checked['_review_video_model'] = cast_video.recorded_model(Path(selected.get('video') or current['video']))
    try:
        verdict = judges.judge_clip(checked, Path(current['video']), bible, times,
                                   selected.get('hypothesis', ''), directory / 'work/keep_review' / cid,
                                   source_script=copy.deepcopy(script))
        if verdict.get('severity') not in {'pass', 'minor', 'fail'} or verdict.get('error'):
            raise ValueError('shared picture review incomplete: ' + str(verdict.get('error') or verdict.get('severity')))
        scoped = confirmed.enforce(directory, {'clips': {cid: {**verdict, **current}}, 'feedback': {}},
                                   previous, verify=True)
    except Exception as error:
        raise ValueError(f'{cid}: keep recheck failed: {error}') from error
    if (read_json(directory / 'clip_plan.json', {}) != plan
            or read_json(directory / 'chapter_script.json', {}) != script
            or read_json(directory / 'review_feedback.json', {}) != notes):
        raise ValueError(f'{cid}: episode inputs changed during keep recheck')
    if current_takes(directory, plan, read_json(directory / 'episode_review.json', {})).get(cid) != current:
        raise ValueError(f'{cid}: selected take changed during keep recheck')

    row = scoped['clips'][cid]
    row.update(policy=POLICY, review_mode=mode)
    speech = speech_gate_result(directory.parent, selected, directory)
    row['speech_gate'] = speech
    if speech.get('passed') is not True:
        row.update(severity='fail', tier='must_fix', feedback=merge_note(row.get('feedback', ''),
                   correction(clip, selected, include_missing=True) or '当前语音门未通过'))
    accepted = row.get('severity') in {'pass', 'minor'} and speech.get('passed') is True
    row['tier'] = 'optional' if accepted else 'must_fix'
    row['source_segment_ids'] = list(clip.get('segment_ids') or [])
    review = copy.deepcopy(previous)
    if not current_review(previous, mode):
        review.pop('review_mode', None)  # one rechecked clip cannot certify the other clips' mode
    review.setdefault('clips', {})[cid] = row
    review.setdefault('feedback', {}).pop(cid, None)
    review['flags'] = [flag for flag in review.get('flags', []) if not str(flag).startswith(f'{cid}:')]
    if not accepted:
        note = compose_feedback(row, clip) or previous.get('feedback', {}).get(cid) or '当前片段复查未通过'
        review['feedback'][cid] = note
        review['flags'].append(flag_line(cid, row, 'must_fix'))
    atomic_write_json(directory / 'episode_review.json', review)
    history.observe(directory, review, {cid: current})
    return {'changed': [], 'accepted': [cid] if accepted else [], 'skip_render': True, 'review': review}


def save_result(directory, media, review, *, plan=None, rounds=(), corrections=(), blocked=None,
                write_media=True):
    """The same technical/content acceptance for a single run and a batch review."""
    plan = plan if plan is not None else read_json(directory / 'clip_plan.json', {})
    from novel_manga.application.review import final_cut
    final_required = final_cut.required(directory)
    if not final_required:
        review = final_cut.apply_feedback(directory, review, None)
        atomic_write_json(directory/'episode_review.json', review)
    final=media.get('final_cut_review') or read_json(directory/'final_cut_review.json',None)
    if final is not None:media['final_cut_review']=final
    final_current=final_cut.current(directory,media,final) if final is not None else False
    if final_current:
        review=final_cut.apply_feedback(directory,review,final)
        atomic_write_json(directory/'episode_review.json',review)
    planned = {c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'}
    # Keep the established handling of historical unstamped media. A known stale
    # report, however, cannot certify a newly compiled plan by merely re-reading it.
    stale = ((media.get('clip_plan_fingerprint') is not None
              and media['clip_plan_fingerprint'] != plan_fingerprint(plan))
             or (bool(media.get('prompt_h3_fingerprint'))
                 and media['prompt_h3_fingerprint'] != h3_prompt_fingerprint(plan))
             or (bool(media) and (media.get('review_feedback') or {})
                 != read_json(directory / 'review_feedback.json', {})))
    acceptance = evaluate_acceptance(planned, media, review, stale=stale)
    result = acceptance.quality_report(
        rounds=rounds, corrections=corrections, blocked=blocked,
        generated_counts=managed.generated_counts(history.load(directory)),
    )
    native=final_cut.apply_feedback(directory,review,None)
    result['native_content_passed']=evaluate_acceptance(planned,media,native,stale=stale).content_passed
    if not final_required:
        decision = read_json(directory/'work/review/final_movie_policy.json', {})
        result.update(final_cut_required=False,
                      final_cut_status='skipped_by_user' if decision.get('authorization') else 'not_required',
                      final_cut_passed=None, final_cut_complete=False, final_cut_errors=[])
        if decision.get('authorization'):
            result['final_cut_authorization'] = decision['authorization']
        if (media.get('assembly') or {}).get('final_video') and not final_cut.assembly_current(directory, media):
            result.update(passed=False, reason='assemble current selected takes before delivery')
    elif final is not None:
        final_passed=final_current and final.get('complete') is True and final.get('passed') is True and not final.get('issues') and not final.get('errors')
        result.update(final_cut_passed=bool(final_passed),
                      final_cut_complete=final_current and final.get('complete') is True,
                      final_cut_errors=final.get('errors',[]))
        if not final_passed:
            result.update(passed=False,content_passed=False,
                          reason='final movie review failed or is incomplete/stale')
    else:
        result.update(passed=False, final_cut_required=True, final_cut_passed=False,
                      final_cut_complete=False, final_cut_errors=[],
                      reason='required final movie review is incomplete')
    media['quality_review'] = result
    if write_media:
        atomic_write_json(directory / 'thin_media_report.json', media)
    atomic_write_json(directory / 'episode_execution.json', result)
    return media


def review_only(directory: Path, *, fresh=None, video_name='clip.mp4'):
    """Audit material already on disk; no renderer, preparation, trial or retake is started."""
    if video_name == 'clip.mp4':
        history.adopt_reviewed_run(directory)
    review = review_saved(directory, fresh=fresh, video_name=video_name)
    if video_name == 'clip.mp4':
        path = directory / 'thin_media_report.json'
        media = read_json(path, {})
        previous = media.get('quality_review') or {}
        save_result(directory, media, review, rounds=previous.get('repair_rounds', []),
                    corrections=previous.get('automatic_corrections', []),
                    blocked=previous.get('blocked', {}), write_media=path.is_file())
    return review
