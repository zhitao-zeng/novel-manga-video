import copy
import json

import novel_manga.application.repair.managed as managed
import novel_manga.application.repair.recovery as recovery
import novel_manga.application.repair.history as history


def test_seed_only_change_cannot_extend_an_exhausted_budget(tmp_path, monkeypatch):
    before={'clips':[{'clip_id':'clip_01','kind':'video','prompt':'原请求','repair_take':3}]}
    after=copy.deepcopy(before)
    after['clips'][0]['repair_take']=4
    (tmp_path/'clip_plan.json').write_text(json.dumps(after))
    monkeypatch.setattr(history,'load',lambda d:{})
    monkeypatch.setattr(managed,'generated_counts',lambda r:{'clip_01':3})
    assert recovery.grant_changed_source_retry(tmp_path,before,{}, {'changed':['clip_01']},1)==[]
    assert not (tmp_path/'repair_budget_grants.json').exists()


def test_source_rewrite_grants_one_take_and_keeps_the_used_count(tmp_path, monkeypatch):
    before={'clips':[{'clip_id':'clip_01','kind':'video','prompt':'赤岚发言','repair_take':3}]}
    after=copy.deepcopy(before)
    after['clips'][0]['prompt']='星垣发言，赤岚在画外倾听'
    (tmp_path/'clip_plan.json').write_text(json.dumps(after))
    monkeypatch.setattr(history,'load',lambda d:{})
    monkeypatch.setattr(managed,'generated_counts',lambda r:{'clip_01':3})
    assert recovery.grant_changed_source_retry(tmp_path,before,{}, {'changed':['clip_01']},1)==['clip_01']
    assert json.loads((tmp_path/'repair_budget_grants.json').read_text())['clip_01']['limit']==4


def test_conditional_extra_is_scoped_to_allowed_clip(tmp_path, monkeypatch):
    (tmp_path/'clip_plan.json').write_text(json.dumps({'clips': [
        {'clip_id': 'clip_01', 'kind': 'video'}, {'clip_id': 'clip_11', 'kind': 'video'}]}))
    (tmp_path/'repair_budget_grants.json').write_text(json.dumps({'clip_01': {'conditional_extra': {
        'authorization': 'a', 'reason': 'r', 'limit': 4, 'allowed_clips': ['clip_11']}}}))
    monkeypatch.setattr(history, 'source_budget', lambda d, c, **kw: {'roots': ['clip_01'], 'constraints': {}, 'remaining': 1})
    monkeypatch.setattr(history, 'generation_counts', lambda d: {})
    monkeypatch.setattr('novel_manga.application.repair.repack_budget.archived_unknown_count', lambda d, c: 0)
    assert history.conditional_extra(tmp_path, 'clip_11')['authorization'] == 'a'
    assert history.conditional_extra(tmp_path, 'clip_01') == {}


def test_grant_accounts_for_an_archived_unknown(tmp_path, monkeypatch):
    (tmp_path/'clip_plan.json').write_text(json.dumps({'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]}))
    monkeypatch.setattr(history, 'source_budget', lambda d, c, **kw: {'roots': []})
    monkeypatch.setattr(history, 'generation_counts', lambda d: {'clip_01': 2})
    monkeypatch.setattr(history, 'generation_limit', lambda d, c, **kw: 3)
    monkeypatch.setattr(history, 'preparation_limit', lambda d, c, **kw: 3)
    monkeypatch.setattr('novel_manga.application.repair.repack_budget.archived_unknown_count', lambda d, c: 1)
    got = history.grant_conditional_extra(tmp_path, 'clip_01', authorization='a', reason='r')
    assert got['limit'] == 4 and got['unknown_held'] == 1 and got['allowed_clips'] == ['clip_01']


def test_balances_does_not_apply_a_root_extra_to_siblings(tmp_path, monkeypatch):
    from novel_manga.application.repair import repack_budget
    group = {'source_budget': {'roots': {'clip_01': {'limit': 3, 'used': 3}}, 'children': {
        'clip_01': {'roots': ['clip_01'], 'baseline_count': 3},
        'clip_11': {'roots': ['clip_01'], 'baseline_count': 0}}}}
    monkeypatch.setattr(repack_budget, 'group_for', lambda d, c: group)
    monkeypatch.setattr(repack_budget, 'retry_ledger', lambda d: {})
    grants = {'clip_01': {'conditional_extra': {'authorization': 'a', 'reason': 'r', 'limit': 4,
                                                'allowed_clips': ['clip_11']}}}
    named = repack_budget.balances(tmp_path, 'clip_11', {}, grants, {'clip_01': 3}, preparation=True)
    sibling = repack_budget.balances(tmp_path, 'clip_01', {}, grants, {'clip_01': 3}, preparation=True)
    assert named['remaining'] == 1 and sibling['remaining'] == 0
