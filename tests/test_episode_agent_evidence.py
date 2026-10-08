from novel_manga.media.context import RenderContext, RenderOptions, RenderState
from novel_manga.application.agents.episode.media_operations import MediaOperations
"""Automatic repair context stays focused without hiding unresolved media."""
import copy
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history
from novel_manga.review.contracts import REVIEW_POLICY
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


@pytest.fixture
def evidence_case(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.run_dir/'prepared.json', {'ready': True})
    rows, verdicts, frame_calls = [], {}, []
    for clip in plan['clips']:
        cid = clip['clip_id']
        video = ctx.episode_dir/'work/clips'/cid/'attempt_01/clip.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'current take')
        rows.append({'clip_id': cid, 'selected': {'video': str(video), 'passed': True,
                     'reference': '你来啦。', 'hypothesis': '你来啦。'}})
        verdicts[cid] = {'severity': 'pass', 'video': str(video), 'take': take_identity(video)}
    verdicts['clip_02']['severity'] = 'fail'
    review = {'policy': REVIEW_POLICY, 'review_mode':'classic', 'clips': verdicts, 'feedback': {'clip_02': 'wrong action'}}
    report = {'status': 'clips_ready', 'clips': rows, 'failed_clips': [], 'gate_failed_clips': [],
              'blocked_clips': {}, 'clip_plan_fingerprint': plan_fingerprint(plan),
              'prompt_h3_fingerprint': h3_prompt_fingerprint(plan), 'review_feedback': {}}

    class Runner:
        def __init__(self):
            self.context = RenderContext(episode_dir=ctx.episode_dir)

        def run(self, *, assemble=True):
            assert assemble is False
            return copy.deepcopy(report)

    def independent_review(directory, **kwargs):
        atomic_write_json(directory/'episode_review.json', review)
        return copy.deepcopy(review)

    def frames(directory, cid, video, destination):
        frame_calls.append(cid)
        destination.mkdir(parents=True, exist_ok=True)
        frame = destination/'frame_01.jpg'
        frame.write_bytes(b'frame')
        atomic_write_json(destination/'frame-evidence.json', {'frames': [{'path': str(frame)}]})
        return [frame], 'test current frame'

    monkeypatch.setattr(MediaOperations, 'runner', lambda self: Runner())
    monkeypatch.setattr('novel_manga.application.review.execution.review_saved', independent_review)
    monkeypatch.setattr('novel_manga.application.repair.agent.provided_frames', frames)
    return ctx, plan, report, review, frame_calls


def test_render_focuses_repairs_and_passed_clips_need_no_keep_decision(evidence_case):
    ctx, plan, _, _, frame_calls = evidence_case
    tools = EpisodeTools(ctx)
    result = tools.dispatch('render')
    assert result['ok'] and result['quality_passed'] is False
    evidence = read_json(ctx.work_dir/result['review']['evidence'])
    assert [r['clip']['clip_id'] for r in evidence['targets']] == ['clip_02']
    assert result['review']['attention_clips'] == ['clip_02']
    assert len(result['review']['retained_clips']) == 7
    assert len(result['review']['clips']) == len(plan['clips'])
    assert evidence['chapter_script'] == read_json(ctx.episode_dir/'chapter_script.json')
    assert frame_calls == ['clip_02']
    counts = history.generation_counts(ctx.episode_dir)
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [
        {'clip_id': 'clip_02', 'action': 'stop', 'reason': 'No supported correction'}]})
    applied = tools.dispatch('apply')
    assert applied['blocked'] == {'clip_02': 'No supported correction'}
    assert not applied['changed']
    assert history.generation_counts(ctx.episode_dir) == counts


def test_failed_final_technical_gate_keeps_its_inspection_evidence(evidence_case):
    ctx,plan,report,review,frames=evidence_case
    report['status']='assembled'
    report['assembly']={'thin_passed':False,'media_qc':{'checks':{
        'silence_ratio':{'passed':False,'detail':{'ratio':0.42}}}}}
    evidence=EpisodeTools(ctx).evidence.review_evidence(review,report,pending_only=True)
    saved=read_json(ctx.work_dir/evidence['evidence'])
    assert saved['episode_technical_issues']['silence_ratio']['detail']['ratio']==0.42
    assert set(evidence['attention_clips'])=={c['clip_id'] for c in plan['clips'] if c['kind']=='video'}
    assert set(frames)==set(evidence['attention_clips'])
    assert 'do not require keep decisions' in saved['technical_repair_guidance']


def test_explicit_review_still_exports_complete_episode_context(evidence_case):
    ctx, plan, _, _, frame_calls = evidence_case
    tools = EpisodeTools(ctx)
    tools.dispatch('render')
    frame_calls.clear()
    counts = history.generation_counts(ctx.episode_dir)
    result = tools.dispatch('review')
    evidence = read_json(ctx.work_dir/result['evidence'])
    assert len(evidence['targets']) == len(plan['clips'])
    assert frame_calls == [c['clip_id'] for c in plan['clips']]
    assert result['attention_clips'] == ['clip_02']
    assert history.generation_counts(ctx.episode_dir) == counts


def test_title_card_stays_in_context_without_a_video_repair_decision(evidence_case):
    ctx, plan, report, _, _ = evidence_case
    title = {**plan['clips'][0], 'clip_id': 'title_01', 'kind': 'title_card'}
    plan['clips'].insert(0, title)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    report.update(clip_plan_fingerprint=plan_fingerprint(plan),
                  prompt_h3_fingerprint=h3_prompt_fingerprint(plan))
    result = EpisodeTools(ctx).dispatch('render')
    assert result['ok']
    review = result['review']
    assert review['attention_clips'] == ['clip_02']
    assert 'title_01' not in review['retained_clips']
    assert next(c for c in review['clips'] if c['clip_id'] == 'title_01')['needs_attention'] is False
    evidence = read_json(ctx.work_dir/review['evidence'])
    assert [t['clip']['clip_id'] for t in evidence['targets']] == ['clip_02']
    complete = EpisodeTools(ctx).dispatch('review')
    assert any(t['clip']['kind'] == 'title_card' for t in read_json(ctx.work_dir/complete['evidence'])['targets'])


@pytest.mark.parametrize('problem', ['speech', 'missing_video', 'missing_review', 'wrong_video',
                                   'old_take', 'review_error', 'blocked_input', 'media_gate'])
def test_uncertain_or_failed_clips_keep_detailed_evidence(evidence_case, problem):
    ctx, _, report, review, _ = evidence_case
    selected = report['clips'][0]['selected']
    verdict = review['clips']['clip_01']
    if problem == 'speech': selected['passed'] = False
    elif problem == 'missing_video': selected.pop('video')
    elif problem == 'missing_review': review['clips'].pop('clip_01')
    elif problem == 'wrong_video': verdict['video'] = '/old/take.mp4'
    elif problem == 'old_take': verdict['take'] = [0, 0, 0]
    elif problem == 'review_error': verdict['severity'] = 'review_error'
    elif problem == 'blocked_input': report['blocked_clips']['clip_01'] = 'wrong reference'
    elif problem == 'media_gate': report['gate_failed_clips'] = ['clip_01']
    result = EpisodeTools(ctx).dispatch('render')
    assert result['ok']
    evidence = read_json(ctx.work_dir/result['review']['evidence'])
    assert [r['clip']['clip_id'] for r in evidence['targets']] == ['clip_01', 'clip_02']
    assert 'clip_01' not in result['review']['retained_clips']


@pytest.mark.parametrize('stale', ['policy', 'plan', 'prompt', 'notes', 'mode', 'missing_mode'])
def test_stale_input_or_policy_cannot_hide_detailed_evidence(evidence_case, stale):
    ctx, plan, report, review, _ = evidence_case
    if stale == 'policy': review['policy'] = 'old-policy'
    elif stale == 'mode': review['review_mode'] = 'verify'
    elif stale == 'missing_mode': review.pop('review_mode')
    elif stale == 'plan': report['clip_plan_fingerprint'] = 'old-plan'
    elif stale == 'prompt': report['prompt_h3_fingerprint'] = 'old-prompt'
    elif stale == 'notes': report['review_feedback'] = {'clip_01': 'old correction'}
    result = EpisodeTools(ctx).dispatch('render')
    assert result['ok']
    evidence = read_json(ctx.work_dir/result['review']['evidence'])
    assert len(evidence['targets']) == len(plan['clips'])
    assert not result['review']['retained_clips']


@pytest.mark.parametrize('review_matches_mix', [True, False])
def test_retained_postmix_clip_requires_review_for_the_selected_mix(evidence_case, review_matches_mix):
    ctx, _, report, review, _ = evidence_case
    mix = ctx.episode_dir/'mixed.mp4'
    mix.write_bytes(b'mixed current take')
    report['clips'][0]['selected']['postmix_video'] = str(mix)
    if review_matches_mix:
        review['clips']['clip_01'].update(video=str(mix), take=take_identity(mix))
    result = EpisodeTools(ctx).dispatch('render')
    assert result['ok']
    assert ('clip_01' in result['review']['retained_clips']) == review_matches_mix
    assert ('clip_01' in result['review']['attention_clips']) != review_matches_mix
