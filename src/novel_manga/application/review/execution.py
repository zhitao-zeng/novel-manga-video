"""Review existing takes and record one shared episode execution result; never generate."""
import copy
from pathlib import Path

from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.review.episode import review_episode
from novel_manga.application.review.store import current_takes
from novel_manga.util import atomic_write_json, read_json


def media_matches(plan, media, *, notes=None):
    """Whether the existing report can enter repair without first rendering the current plan."""
    return bool(media.get('clips')
                and media.get('clip_plan_fingerprint') == plan_fingerprint(plan)
                and (not media.get('prompt_h3_fingerprint')
                     or media['prompt_h3_fingerprint'] == h3_prompt_fingerprint(plan))
                and (notes is None or (media.get('review_feedback') or {}) == notes))


def review_saved(directory: Path, *, fresh=None, video_name='clip.mp4'):
    review = review_episode(directory, video_name=video_name, fresh=fresh)
    if video_name == 'clip.mp4':
        plan = read_json(directory / 'clip_plan.json', {})
        history.observe(directory, review, current_takes(directory, plan, review))
    return review


def recheck_current_clip(directory: Path, cid: str) -> dict:
    """Review a keep proposal against unchanged inputs; publish only its current-take verdict."""
    from novel_manga.application.review import cast_video, confirmed, judges
    from novel_manga.application.profiles import speech_gate_result
    from novel_manga.media.speech_repair import correction, merge_note
    from novel_manga.models.bible import StoryBible
    from novel_manga.review.policy import compose_feedback, flag_line

    directory = Path(directory)
    plan = read_json(directory / 'clip_plan.json', {})
    script = read_json(directory / 'chapter_script.json', {})
    notes = read_json(directory / 'review_feedback.json', {})
    previous = read_json(directory / 'episode_review.json', {})
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
    speech = speech_gate_result(directory.parent, selected, directory)
    row['speech_gate'] = speech
    if speech.get('passed') is not True:
        row.update(severity='fail', tier='must_fix', feedback=merge_note(row.get('feedback', ''),
                   correction(clip, selected, include_missing=True) or '当前语音门未通过'))
    accepted = row.get('severity') in {'pass', 'minor'} and speech.get('passed') is True
    row['tier'] = 'optional' if accepted else 'must_fix'
    row['source_segment_ids'] = list(clip.get('segment_ids') or [])
    review = copy.deepcopy(previous)
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
    planned = {c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'}
    rows = review.get('clips') or {}
    missing = sorted(planned - rows.keys())
    errors = sorted(cid for cid, row in rows.items() if row.get('severity') == 'review_error')
    # Keep the established handling of historical unstamped media. A known stale
    # report, however, cannot certify a newly compiled plan by merely re-reading it.
    stale = ((media.get('clip_plan_fingerprint') is not None
              and media['clip_plan_fingerprint'] != plan_fingerprint(plan))
             or (bool(media.get('prompt_h3_fingerprint'))
                 and media['prompt_h3_fingerprint'] != h3_prompt_fingerprint(plan))
             or (bool(media) and (media.get('review_feedback') or {})
                 != read_json(directory / 'review_feedback.json', {})))
    accepted = (bool((media.get('assembly') or {}).get('thin_passed'))
                and not media.get('failed_clips') and not media.get('gate_failed_clips')
                and not review.get('feedback') and not missing and not errors and not stale)
    result = {'passed': bool(accepted), 'repair_rounds': list(rounds),
              'automatic_corrections': sorted(set(corrections)), 'blocked': blocked or {},
              'missing_reviews': missing, 'review_errors': errors,
              'remaining': sorted(review.get('feedback') or {}),
              'generated_counts': managed.generated_counts(history.load(directory))}
    if stale:
        result['reason'] = 'media predates the current plan, H3 request or corrections'
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
