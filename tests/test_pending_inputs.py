import copy
from pathlib import Path
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.repair import history,pending_inputs,agent
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.repair.proposal import RepairProposal


def test_pending_scope_is_atomic_when_one_clip_has_no_video_permission(tmp_path):
    d=tmp_path;plan={'clips':[{'clip_id':'a','prompt':'old'},{'clip_id':'b','prompt':'old'}]}
    atomic_write_json(d/'clip_plan.json',plan)
    proposal=RepairProposal({'changed':['a','b']},script={'shots':[]},plan={'clips':[]},notes={})
    result=pending_inputs.prepare_publication(d,proposal,'agent_input',counts={'a':0,'b':3})
    assert not result['published'] and read_json(d/'clip_plan.json')==plan
    assert history.load(d)['trials']==[] and not (d/'repair_budget_grants.json').exists()


def legacy_case(d,monkeypatch):
    prior={'index':1,'camera':'old approved camera','source_quote':'approved'}
    script={'shots':[{**prior,'camera':'new unrenderable camera'},{'index':2,'camera':'other'}]}
    old={'clip_id':'a','shot_indexes':[1],'prompt_h3':'real rendered request'}
    plan={'clips':[{**old,'prompt_h3':'new unrenderable request'},{'clip_id':'b','prompt_h3':'other'}]}
    atomic_write_json(d/'clip_plan.json',plan);atomic_write_json(d/'chapter_script.json',script)
    atomic_write_json(d/'review_feedback.json',{})
    atomic_write_json(d/'sandbox/run-1/input/primary.json',{'shots_and_neighbours':[prior]})
    monkeypatch.setattr(agent,'load_config',lambda:{'runs_root':str(d/'sandbox')})
    record={'trials':[{'id':1,'method':'agent_input','clips':['a'],'before':{'a':{'plan':old,'note':''}},
         'expected_plan':plan_fingerprint(plan),'expected_notes':{},'renders':[],
         'changes':{'a':{'run':'run-1'}}}], 'prior_generation_counts':{'clips':{'a':3}}}
    history.save(d,record)
    return plan,script,record,old,prior


def test_whole_candidate_respects_shared_source_balance(tmp_path,monkeypatch):
    plan={'clips':[{'clip_id':'a','prompt':'old'},{'clip_id':'b','prompt':'old'}]}
    atomic_write_json(tmp_path/'clip_plan.json',plan)
    monkeypatch.setattr(history,'source_budget',lambda *args,**kwargs:{
        'roots':['original'],'constraints':{'original':{'remaining':1}},'remaining':1})
    monkeypatch.setattr(history,'preparation_limit',lambda *args,**kwargs:3)
    proposal=RepairProposal({'changed':['a','b']},script={'shots':[]},plan={'clips':[]},notes={})
    result=pending_inputs.prepare_publication(tmp_path,proposal,'agent_input',counts={'a':0,'b':0})
    assert not result['published'] and read_json(tmp_path/'clip_plan.json')==plan
    assert history.load(tmp_path)['trials']==[]


def test_legacy_unrenderable_transaction_restores_only_its_source_and_stages_candidate(tmp_path,monkeypatch):
    plan,script,record,old,prior=legacy_case(tmp_path,monkeypatch)
    result=pending_inputs.recover_unrenderable(tmp_path)
    assert result['restored_trial']==1
    assert read_json(tmp_path/'clip_plan.json')['clips']==[old,plan['clips'][1]]
    assert read_json(tmp_path/'chapter_script.json')['shots']==[prior,script['shots'][1]]
    assert read_json(Path(result['staged_candidate']))['proposal']['plan']==plan
    assert history.load(tmp_path)==record and history.generation_counts(tmp_path)=={'a':3}
    assert pending_inputs.recover_unrenderable(tmp_path) is None


def test_recovery_does_not_override_later_edit_or_completed_render(tmp_path,monkeypatch):
    plan,script,record,old,prior=legacy_case(tmp_path,monkeypatch)
    plan['clips'][1]['prompt']='later unrelated work';atomic_write_json(tmp_path/'clip_plan.json',plan)
    assert pending_inputs.recover_unrenderable(tmp_path) is None
    record['trials'][0]['expected_plan']=plan_fingerprint(plan)
    record['trials'][0]['renders']=[{'clips':{}}];history.save(tmp_path,record)
    assert pending_inputs.recover_unrenderable(tmp_path) is None
    assert read_json(tmp_path/'clip_plan.json')==plan and read_json(tmp_path/'chapter_script.json')==script
