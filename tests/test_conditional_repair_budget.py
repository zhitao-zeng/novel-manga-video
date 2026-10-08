import novel_manga.application.rendering.submission as owner_submission
"""A user-approved extra take is usable only after a validated repair and H3 preflight."""
from concurrent.futures import ThreadPoolExecutor
import pytest

from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def budget(tmp_path):
    plan={'clips':[{'clip_id':f'clip_{n:02d}','kind':'video'} for n in range(1,4)]}
    atomic_write_json(tmp_path/'clip_plan.json',plan)
    atomic_write_json(tmp_path/'repair_budget_grants.json',{
        '*':{'limit':2,'input_limit':1,'recheck_limit':1,'reason':'existing limit'},
        **{f'clip_{n:02d}':{'conditional_extra':{'limit':3,'input_limit':2,'recheck_limit':2,
              'reason':'user approved one reviewed extra take','authorization':'test-approval','prepared':False}}
           for n in (1,2)}})
    history.save(tmp_path,{'prior_generation_counts':{'clips':{'clip_01':2,'clip_02':2,'clip_03':2}},'trials':[]})
    return tmp_path


def test_pending_authorization_allows_preparation_but_not_generation(budget):
    assert history.generation_limit(budget,'clip_01')==2
    assert history.preparation_limit(budget,'clip_01')==3
    assert history.preparation_limit(budget,'clip_03')==2
    assert not history.extra_take_ready(budget,'clip_01')
    assert not history.activate_extra_take(budget,'clip_01')
    assert history.generation_limit(budget,'clip_01')==2


def test_only_validated_repair_marks_pending_extra_ready(budget):
    history.mark_extra_prepared(budget,['clip_01','clip_03'])
    assert history.extra_take_ready(budget,'clip_01')
    assert not history.extra_take_ready(budget,'clip_02')
    assert not history.extra_take_ready(budget,'clip_03')
    assert history.generation_limit(budget,'clip_01')==2


def test_concurrent_activation_preserves_other_limits_and_cannot_refill(budget):
    history.mark_extra_prepared(budget,['clip_01','clip_02'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(lambda cid:history.activate_extra_take(budget,cid),['clip_01','clip_02']))
    assert [history.generation_limit(budget,f'clip_{n:02d}') for n in range(1,4)]==[3,3,2]
    saved=read_json(budget/'repair_budget_grants.json')
    assert history.activate_extra_take(budget,'clip_01')
    assert read_json(budget/'repair_budget_grants.json')==saved
    record=history.load(budget);record['prior_generation_counts']['clips']['clip_01']=3;history.save(budget,record)
    assert not history.extra_take_ready(budget,'clip_01')
    assert not history.activate_extra_take(budget,'clip_01')
    assert history.generation_limit(budget,'clip_01')==3


def test_extra_preparation_and_recheck_caps_are_scoped(budget):
    assert history.input_limit(budget,'clip_01',default=2)==2
    assert history.input_limit(budget,'clip_03',default=2)==1
    assert history.recheck_limit(budget,'clip_01')==2
    assert history.recheck_limit(budget,'clip_03')==1
    record=history.load(budget);record['prior_generation_counts']['clips']['clip_01']=3;history.save(budget,record)
    assert history.input_limit(budget,'clip_01',default=2)==1


def test_new_round_preserves_spent_authorization_and_cannot_refill(budget):
    history.mark_extra_prepared(budget, ['clip_01'])
    assert history.activate_extra_take(budget, 'clip_01')
    record = history.load(budget)
    record['prior_generation_counts']['clips']['clip_01'] = 3
    history.save(budget, record)
    options = {'authorization': 'approved-new-round', 'reason': 'one corrected replacement'}
    first = history.grant_conditional_extra(budget, 'clip_01', **options)
    assert first == history.grant_conditional_extra(budget, 'clip_01', **options)
    assert first['limit'] == 4 and not first['prepared'] and not first.get('activated')
    assert history.generation_limit(budget, 'clip_01') == 3
    assert history.preparation_limit(budget, 'clip_01') == 4
    assert history.generation_limit(budget, 'clip_03') == 2
    saved = read_json(budget/'repair_budget_grants.json')['clip_01']
    assert saved['previous_conditional_extras'][0]['authorization'] == 'test-approval'
    history.mark_extra_prepared(budget, ['clip_01'])
    assert history.activate_extra_take(budget, 'clip_01')
    record['prior_generation_counts']['clips']['clip_01'] = 4
    history.save(budget, record)
    assert history.grant_conditional_extra(budget, 'clip_01', **options)['limit'] == 4
    assert not history.extra_take_ready(budget, 'clip_01')


def test_new_round_reuses_unspent_authorization_and_concurrent_calls_do_not_stack(budget):
    before = (budget/'repair_budget_grants.json').read_bytes()
    old = history.grant_conditional_extra(budget, 'clip_02', authorization='new-round', reason='approved')
    assert old['authorization'] == 'test-approval' and old['limit'] == 3
    assert (budget/'repair_budget_grants.json').read_bytes() == before
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: history.grant_conditional_extra(
            budget, 'clip_03', authorization='new-round', reason='approved'), range(2)))
    assert rows[0] == rows[1] and rows[0]['limit'] == 3
    assert history.generation_limit(budget, 'clip_03') == 2


@pytest.mark.parametrize('consistent',[False,True])
def test_renderer_checks_actual_inputs_before_activating_extra(budget,monkeypatch,consistent):
    from types import SimpleNamespace
    from support.render_context import uninitialized_runner
    from novel_manga.media import generation,cache
    from novel_manga.application.preparation import request_check,readiness
    import novel_manga.application.rendering.flow as rendering
    r=uninitialized_runner();ctx=r.context
    ctx.novel_dir=budget;ctx.episode_dir=budget;ctx.work=budget/'work'
    ctx.settings=SimpleNamespace(local_h3_base_url='pool')
    ctx.options.cache_only=False;ctx.options.prescreen=False;ctx.feedback={};ctx.options.inflight=0
    ctx.clip_plan=read_json(budget/'clip_plan.json');ctx.state.blocked_clips={};ctx.state.remaining={'clip_01':0}
    clip={'clip_id':'clip_01','kind':'video','request_seconds':4,'prompt':'current source',
          'prompt_h3':'current request','references':[]}
    history.mark_extra_prepared(budget,['clip_01'])
    events=[]
    monkeypatch.setattr(readiness,'reference_issues',lambda *a:[])
    monkeypatch.setattr(generation,'build_request',lambda *a:({'prompt':'current request'},[],[],''))
    monkeypatch.setattr(cache,'current_video',lambda *a:None)
    monkeypatch.setattr(cache,'other_video',lambda *a:None)
    monkeypatch.setattr(r,'english_correction_for_new_take',lambda *a:False)
    monkeypatch.setattr(generation,'uses_h3_prompt',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.profiles.h3_prompt_outdated',lambda *a,**k:False)
    monkeypatch.setattr('novel_manga.story.h3.request_issues',lambda *a:[])
    monkeypatch.setattr(owner_submission,'wait_for_inflight_redraws',lambda *a:None)
    def check(*args):
        events.append('preflight')
        assert history.generation_limit(budget,'clip_01')==2
        if not consistent:raise ValueError('actual reference mismatch')
    monkeypatch.setattr(request_check,'before_generation',check)
    def submit(*args):
        events.append('provider')
        assert history.generation_limit(budget,'clip_01')==3
        raise RuntimeError('terminal test provider failure')
    monkeypatch.setattr(generation,'submit',submit)
    with pytest.raises((ValueError,RuntimeError),match='reference mismatch|terminal test'):
        r.generate_clip(clip,1)
    assert events==(['preflight','provider'] if consistent else ['preflight'])
    extra=read_json(budget/'repair_budget_grants.json')['clip_01']['conditional_extra']
    assert bool(extra.get('activated')) is consistent
    assert history.generation_limit(budget,'clip_03')==2
