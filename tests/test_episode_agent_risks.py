import pytest

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.preparation import readiness
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


@pytest.fixture
def blocked(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    for clip in plan['clips'][:2]:
        clip['wears_bindings'] = [{'stage': 1, 'in_frame': ['甲'], 'wears': {'甲': '银甲'}}]
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    risks = readiness.render_risk_report(ctx.episode_dir, plan)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'blocked_clips': risks, 'clips': [{'clip_id': 'clip_01', 'selected': None}]})
    return ctx, plan, risks


def test_agent_can_accept_one_existing_pre_render_risk_without_changing_plan_or_budget(blocked):
    ctx, plan, _ = blocked
    before = (ctx.episode_dir/'clip_plan.json').read_bytes()
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [
        {'clip_id': 'clip_01', 'action': 'accept_risk', 'reason': 'Try the authored armor with the current references; inspect the actual take.',
         'risks': {'wearer_card': ['甲']}},
        {'clip_id': 'clip_02', 'action': 'stop', 'reason': 'Other clip remains unresolved.'}]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['risk_accepted'] == result['input_repaired'] == ['clip_01']
    assert result['changed'] == []
    assert (ctx.episode_dir/'clip_plan.json').read_bytes() == before
    assert history.generation_counts(ctx.episode_dir) == {}
    blocks = readiness.render_risk_report(ctx.episode_dir, plan)
    assert 'clip_01' not in blocks and 'clip_02' in blocks
    assert not (ctx.novel_dir/readiness.ACCEPTED).exists()
    assert not (ctx.source_novel_dir/readiness.ACCEPTED).exists()
    assert EpisodeTools(ctx).dispatch('finish').get('passed') is not True
    again = EpisodeTools(ctx).dispatch('apply')
    assert again.get('risk_accepted') == []


def test_check_returns_exact_risk_choices_before_apply_without_mutation(blocked):
    ctx,plan,_=blocked
    tools=EpisodeTools(ctx)
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[{
        'clip_id':'clip_01','action':'accept_risk','reason':'inspect after first take',
        'risks':{'wearer_card':['甲穿着银甲']}}]})
    before=(ctx.episode_dir/'clip_plan.json').read_bytes()
    result=tools.check({'file':'output/repair.json'})
    assert not result['valid']
    assert 'available' in result['argument_errors']['clip_01']
    assert tools.status({})['risk_choices']['clip_01']['wearer_card']==['甲']
    assert 'accept_risk' in tools.status({})['repair_actions']['clip_01']
    assert not (ctx.episode_dir/readiness.ACCEPTED).exists()
    assert (ctx.episode_dir/'clip_plan.json').read_bytes()==before


def test_risk_acceptance_invalidates_the_cached_input_block_and_keeps_other_episodes_blocked(blocked):
    ctx, plan, blocks = blocked
    before = readiness.input_state(ctx.episode_dir, plan)
    readiness.save_check(ctx.episode_dir, plan, blocks)
    assert readiness.current_blocks(ctx.episode_dir)
    readiness.accept_render_risks(ctx.episode_dir, plan['clips'][0], {'wearer_card': ['甲']}, 'Limited input uncertainty')
    assert readiness.input_state(ctx.episode_dir, plan) != before
    assert readiness.current_blocks(ctx.episode_dir) == {}
    other = ctx.novel_dir/'test-book_8'; other.mkdir()
    atomic_write_json(other/'chapter_script.json', read_json(ctx.episode_dir/'chapter_script.json'))
    assert 'clip_01' in readiness.render_risk_report(other, plan)


def test_risk_action_cannot_accept_an_unrelated_reference_or_spent_video_budget(blocked, monkeypatch):
    ctx, plan, _ = blocked
    with pytest.raises(ValueError, match='current'):
        readiness.accept_render_risks(ctx.episode_dir, plan['clips'][0], {'wearer_card': ['乙']}, 'Wrong target')
    with pytest.raises(ValueError, match='risk'):
        readiness.accept_render_risks(ctx.episode_dir, plan['clips'][0], {'source': ['all']}, 'Not a reference risk')
    monkeypatch.setattr(history, 'generation_counts', lambda *a: {'clip_01': 2})
    monkeypatch.setattr(history, 'generation_limit', lambda *a, **k: 2)
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [
        {'clip_id': 'clip_01', 'action': 'accept_risk', 'reason': 'Budget spent', 'risks': {'wearer_card': ['甲']}}]})
    checked=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert not checked['valid'] and 'budget used' in checked['argument_errors']['clip_01']
    result = EpisodeTools(ctx).dispatch('apply')
    assert 'budget used' in result['blocked']['clip_01']
    assert not (ctx.episode_dir/readiness.ACCEPTED).exists()


def test_keep_without_a_video_does_not_spend_an_independent_video_recheck(blocked, monkeypatch):
    ctx, _, _ = blocked
    monkeypatch.setattr('novel_manga.application.review.execution.recheck_current_clip',
                        lambda *a: pytest.fail('There is no video to review'))
    result = EpisodeTools(ctx).repairs.apply_decisions({'decisions': [
        {'clip_id': 'clip_01', 'action': 'keep', 'reason': 'Mistaken input-risk request'}]}, {'clip_01'})
    assert 'no current video' in result['blocked']['clip_01']
    assert not result.get('keep_checks')


def test_finish_retires_old_empty_keep_block_after_risk_handling_and_actual_gate_success(blocked):
    from novel_manga.review.contracts import REVIEW_POLICY
    ctx, plan, _ = blocked
    tools = EpisodeTools(ctx)
    for decision in [
        {'clip_id': 'clip_01', 'action': 'keep', 'reason': 'Old mistaken input-risk choice'},
        {'clip_id': 'clip_01', 'action': 'accept_risk', 'reason': 'Try only this reference uncertainty',
         'risks': {'wearer_card': ['甲']}}]:
        atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [decision]})
        tools.dispatch('apply')
    # Simulate later renderer and independent-review results, not a model waiver.
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'assembly': {'thin_passed': True}, 'failed_clips': [], 'gate_failed_clips': [], 'clips': []})
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'policy': REVIEW_POLICY, 'review_mode':'classic',
        'clips': {c['clip_id']: {'severity': 'pass'} for c in plan['clips']}, 'feedback': {}})
    assert tools.dispatch('finish')['passed'] is True
    final = read_json(ctx.run_dir/'result.json')['quality_review']
    assert 'clip_01' not in final['blocked']
    assert 'no current video' in final['repair_rounds'][0]['blocked']['clip_01']
