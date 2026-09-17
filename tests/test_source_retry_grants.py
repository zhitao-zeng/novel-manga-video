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
