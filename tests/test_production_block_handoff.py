"""Blocked first-pass clips reach the repair Agent with their current reasons."""
from types import SimpleNamespace

from novel_manga.application.agents.episode import recovery
from novel_manga.util import atomic_write_json, read_json


def context(tmp_path):
    return SimpleNamespace(
        run_dir=tmp_path/'run', work_dir=tmp_path/'run/sandbox',
        episode_dir=tmp_path/'run/book/book_1', source_novel_dir=tmp_path/'book',
        source_episode_dir=tmp_path/'book/book_1')


def plan(ctx, ids=('clip_01', 'clip_02', 'clip_03')):
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [
        {'clip_id': cid, 'kind': 'video', 'request_seconds': 5} for cid in ids]})


def producer(ctx, blocks, status='blocked'):
    atomic_write_json(ctx.episode_dir/'batch-production.json', {
        'status': status, 'policy': 'clip-preflight-v1', 'at': 1000.0, 'blocked': blocks})


def status(ctx):
    return {'adopted': True, 'first_pass_remaining': ['clip_01', 'clip_02', 'clip_03'],
            'repair_actions': {'clip_01': ['input'], 'clip_02': ['input'], 'clip_03': ['input']},
            'job': None, 'media': {}}


def test_producer_blocks_reach_diagnosis_without_a_review(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {
        'clip_01': ['ValueError: asset: missing required image series_assets/locations/location_076/establishing.jpeg'],
        'clip_02': ['ValueError: H3 request/reference mismatch: 图2的面具与请求中的狐面不能同时成立。'],
        'clip_03': ['H3 conversion did not produce a valid current request']})
    result = recovery.diagnose(ctx, status(ctx))
    kinds = {row['clips'][0]: row['kind'] for row in result['issues']}
    assert kinds == {'clip_01': 'missing_asset', 'clip_02': 'request_conflict', 'clip_03': 'conversion'}
    assert result['actionable'] is True
    assert 'location_076' in result['issues'][0]['detail']


def test_conversion_service_failure_is_not_a_content_conflict():
    assert recovery.block_kind('RuntimeError: clip_08: H3 lane cannot submit a new request from stale words '
                               '(no English prompt yet); run scripts/build_h3_prompts.py for this chapter first') == 'conversion'
    assert recovery.block_kind('RequestCheckIncomplete: request conflict scope review is incomplete') == 'input'
    assert recovery.block_kind('ValueError: risk: 齐夏穿着道：实际参考图与穿戴要求不同') == 'wear_risk'


def test_blocks_for_already_filmed_clips_are_not_handed_off(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    video = ctx.episode_dir/'work/clips/clip_02/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'video')
    producer(ctx, {'clip_01': ['ValueError: asset: missing required image a.jpeg'],
                   'clip_02': ['ValueError: asset: missing required image b.jpeg']})
    result = recovery.diagnose(ctx, status(ctx))
    assert [row['clips'] for row in result['issues']] == [['clip_01']]


def test_completed_producer_record_is_not_a_current_block(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {'clip_01': ['ValueError: asset: missing required image a.jpeg']}, status='complete')
    assert recovery.diagnose(ctx, status(ctx))['issues'] == []


def test_render_block_is_used_when_the_producer_has_no_blocked_receipt(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'status': 'clips_failed',
        'blocked_clips': {'clip_01': ['request: 图2的羊角面具与请求的狐面不兼容']}})
    result = recovery.diagnose(ctx, status(ctx))
    assert [row['kind'] for row in result['issues']] == ['request_conflict']


def test_current_task_keeps_the_block_for_its_target_clip(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {'clip_01': ['ValueError: H3 request/reference mismatch: 图2与请求冲突']})
    state = status(ctx)
    diagnosis = recovery.diagnose(ctx, state)
    task = prepare(ctx, state, diagnosis, [])
    assert task['issues'][0]['clips'] == ['clip_01']
    assert '图2与请求冲突' in task['issues'][0]['detail']


def test_handoff_recovery_reopens_once_with_original_time(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {'clip_01': ['ValueError: asset: missing required image a.jpeg']})
    saved = {'status': 'blocked', 'passed': False, 'pipeline_phase': 'input_repair',
             'reason': 'episode agent ended twice without operation or output progress',
             'remaining_seconds': 321, 'no_progress': 2, 'attempts': ['old'],
             'agent_seconds': 40, 'job': {'id': 'job-1', 'action': 'render', 'status': 'completed'}}
    atomic_write_json(ctx.run_dir/'agent-run.json', saved)
    resumed = recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, saved)
    assert resumed['status'] == 'deferred' and resumed['remaining_seconds'] == 321
    assert resumed['attempts'] == ['old'] and resumed['agent_seconds'] == 40
    assert resumed['no_progress'] == 0 and resumed.get('job') is None
    assert resumed['handoff_recovery']['clips'] == ['clip_01']
    assert read_json(ctx.run_dir/'handoff-recovery.json')['previous'] == saved
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, {**resumed, 'status': 'blocked'}) is None


def test_handoff_recovery_skips_finished_exhausted_or_running_stops(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {'clip_01': ['ValueError: asset: missing required image a.jpeg']})
    base = {'status': 'blocked', 'pipeline_phase': 'input_repair',
            'reason': 'episode agent ended twice without operation or output progress',
            'remaining_seconds': 300, 'no_progress': 2}
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, {**base, 'remaining_seconds': 0}) is None
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, {**base, 'passed': True}) is None
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir,
                                   {**base, 'job': {'id': 'j', 'status': 'running'}}) is None
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir,
                                   {**base, 'reason': 'episode operation timeout exhausted'}) is None
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir,
                                   {**base, 'pipeline_phase': 'review'}) is None


def test_handoff_recovery_requires_current_unfilmed_blocks(tmp_path):
    ctx = context(tmp_path)
    plan(ctx)
    producer(ctx, {'clip_01': ['ValueError: asset: missing required image a.jpeg']})
    saved = {'status': 'blocked', 'pipeline_phase': 'input_repair',
             'reason': 'episode agent ended twice without operation or output progress',
             'remaining_seconds': 300, 'no_progress': 2}
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'video')
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, saved) is None
    video.unlink()
    producer(ctx, {})
    assert recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, saved) is None


def test_semantic_prompt_cites_only_existing_preparation_evidence(tmp_path):
    from novel_manga.application.agents.episode.pipeline_preparation import semantic_prompt
    ctx = context(tmp_path)
    assert 'latest-preparation.json' not in semantic_prompt(ctx)
    assert 'current-task.json' in semantic_prompt(ctx)
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json', {'blocked': {}})
    assert 'latest-preparation.json' in semantic_prompt(ctx)
