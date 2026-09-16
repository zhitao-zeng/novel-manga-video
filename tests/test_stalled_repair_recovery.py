import copy
import json
from types import SimpleNamespace

import pytest

import manage_repair_thin as manager
import repair_flow_thin as repair
import source_recheck_thin as source
from test_managed_repair import fixture_episode


def test_duration_conflict_never_cycles_as_a_seed_retry(tmp_path):
    m = manager.Manager(tmp_path/'book', tmp_path/'legacy')
    d = m.novel/'book_1'; d.mkdir(parents=True)
    (d/'chapter_script.json').write_text('{}')
    m.state['plan_queue'] = {'1': {'a': ['duration: planned 16s exceeds request 15s']}}
    m.info[1] = dict(status='plan_blocked', ready=False, bad=1, held=False,
                     plan_blocked=True, managed_clips=['a'])
    m.schedule_recovery()
    job = m.state['jobs'][0]
    assert job['recovery_kind'] == 'plan'
    job['status'] = 'waiting_plan'
    m.schedule_recovery()
    assert len(m.state['jobs']) == 1


def test_ellipsis_quote_gets_one_corrected_literal_quote(monkeypatch):
    passage = '甲说：“今晚先回家，明天再来。”'
    shots = [{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','text':'明天再来。','speaker_name':'甲'}]}]
    row = dict(stage=1,turn=1,speaker='甲',relation='condensed',source_quote='甲说：“……明天再来。”')
    calls = []
    def ask(*a, **k):
        calls.append(k['name'])
        return {'speakers': [{**row,'source_quote':passage} if len(calls)>1 else row]}
    monkeypatch.setattr(repair,'ask_json',ask)
    assert repair.speaker_contract(passage,shots,['甲'],[]) == {(1,1):'甲'}
    assert len(calls) == 2


def test_narrative_adaptation_cannot_invent_a_new_speaker(monkeypatch):
    passage = '甲和乙都在门边，大家准备出发。'
    shots = [{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','text':'出发了。','speaker_name':'甲'}]}]
    row = dict(stage=1,turn=1,speaker='乙',relation='narrated',source_quote=passage)
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[row]})
    assert not repair.speaker_contract(passage,shots,['甲','乙'],[])
    row['speaker'] = '甲'
    assert repair.speaker_contract(passage,shots,['甲','乙'],[]) == {(1,1):'甲'}


def test_adjacent_prose_keeps_the_speaker_across_a_segment_boundary():
    segments = {'a':'甲走近门口，开口问道：','b':'“怎么了？”','c':'乙回过头。','d':'远处另一个场景。'}
    passage = repair.source_passage(segments,['b'])
    assert segments['a'] in passage and segments['b'] in passage
    assert segments['d'] not in passage


@pytest.mark.parametrize('changed_words',[False,True,'recut'])
def test_source_review_accepts_noop_picture_but_rejects_changed_dialogue(tmp_path,monkeypatch,changed_words):
    d,clips,reviews = fixture_episode(tmp_path)
    (d.parent/'repair_manager').mkdir()
    for clip in clips:
        clip['lines']=[{'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'你好。'}]
    (d/'clip_plan.json').write_text(json.dumps({'clips':clips}))
    plan = copy.deepcopy({'clips':clips})
    if changed_words is True:
        plan['clips'][0]['lines'][0]['text']='再见。'
    structural = {}
    if changed_words == 'recut':
        plan['clips'].insert(1, {**copy.deepcopy(plan['clips'][0]), 'clip_id': 'c'})
        structural = {'groups': [{'old': ['a'], 'new': ['a', 'c']}]}
    import planner_context_thin as planner_context
    import build_h3_prompts as h3
    import thin_profile
    import novel_manga.review.policy as review_policy
    monkeypatch.setattr(planner_context,'load_entity_index',lambda *a,**k:None)
    monkeypatch.setattr('story_identity.resolve_chapter',lambda *a,**k:{'policy':'test','entities':{},'mentions':[]})
    monkeypatch.setattr(planner_context,'ledger_cast',lambda *a:{})
    monkeypatch.setattr(repair,'speaker_contract',lambda *a,**k:{})
    monkeypatch.setattr(repair,'repair_episode',lambda *a,**k:{'changed':['a','c'] if structural else [],
        'proposal':{'plan':plan,'script':{'shots':[]},'notes':{},'changes':{},'structural_repair':structural}})
    monkeypatch.setattr(h3,'convert',lambda *a,**k:False)
    monkeypatch.setattr(thin_profile,'h3_prompt_outdated',lambda *a:False)
    monkeypatch.setattr(review_policy,'verify_to_verdict',lambda *a:{'story_ok':True})
    take=source.current_takes(d,{'clips':clips},{'clips':reviews})['a']
    monkeypatch.setattr(source,'SourceVerifier',lambda *a,**k:SimpleNamespace(verify=lambda *a:{**take,'verdict':'fine'}))
    if changed_words is True:
        with pytest.raises(ValueError,match='dialogue wording'):
            source.prepare_source_recheck(d,['a'])
    elif changed_words == 'recut':
        monkeypatch.setattr(source, 'SourceVerifier', lambda *a, **k: pytest.fail('old cut must not be reviewed as new cut'))
        result = source.prepare_source_recheck(d, ['a'])
        assert result['needs_render'] == ['a', 'c'] and not result['accepted']
    else:
        result = source.prepare_source_recheck(d,['a'])
        assert result['accepted']==['a'] and result['needs_render']==[]


def test_named_silent_lead_does_not_become_a_crowd_reference():
    from h3_request_checks import source_crowds
    clip={'references':[{'role':'character','name':'年轻女仆'},{'role':'character','name':'蒂法'}]}
    bible={'characters':[{'name':'年轻女仆','role':'群演/背景角色'},{'name':'蒂法','role':'贴身女仆'}]}
    result=source_crowds(clip,bible,'她的背后跟着两位同样穿着女仆裙装的年轻女仆。')
    assert list(result)==['年轻女仆'] and result['年轻女仆']['count']==2


def test_source_paragraph_ids_preserve_the_actual_original_typo(monkeypatch):
    passage='“看来是莪赢了。”\n莱恩看着他。'
    shots=[{'origin_index':1,'turns':[{'speaker_name':'莱恩','delivery_mode':'visible_dialogue','text':'看来是我赢了。'}]}]
    row={'stage':1,'turn':1,'speaker':'莱恩','relation':'verbatim','source_quote':'','source_paragraphs':[1,2]}
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[row]})
    facts=[]
    assert repair.speaker_contract(passage,shots,['莱恩'],[],evidence_out=facts)=={(1,1):'莱恩'}
    assert facts[0]['source_quote']==passage


def test_narrated_line_can_follow_an_explicitly_named_source_actor(monkeypatch):
    passage='梅根确定了墙壁上没有侦查陷阱。'
    shots=[{'origin_index':1,'turns':[{'speaker_name':'女术士','delivery_mode':'visible_dialogue','text':'没有陷阱。'}]}]
    row={'stage':1,'turn':1,'speaker':'梅根','relation':'narrated','source_quote':passage,'source_speaker_phrase':'梅根'}
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[row]})
    assert repair.speaker_contract(passage,shots,['梅根','女术士'],[{'name':'梅根','source_names':['梅根']}])=={(1,1):'梅根'}


def test_extra_take_is_scoped_to_the_explicitly_corrected_clip(tmp_path):
    import managed_repair_thin as managed
    import repair_history as history
    d,_,_=fixture_episode(tmp_path)
    takes=[{'video':'v','take':[i,1,2]} for i in range(3)]
    history.save(d,{'trials':[{'managed':True,'renders':[{'clips':{cid:{'generated_takes':takes} for cid in ['a','b']}}]}],'observations':{}})
    (d/'repair_budget_grants.json').write_text(json.dumps({'a':{'limit':4,'reason':'corrected source identity card'}}))
    wanted,blocked=managed.candidates(d)
    assert wanted==['a'] and 'b' in blocked
    assert managed.generation_limit(d,'a')==4 and managed.generation_limit(d,'b')==3
