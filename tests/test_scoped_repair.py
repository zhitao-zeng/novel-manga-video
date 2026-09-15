import copy
import json
from pathlib import Path

import manage_repair_thin as manager
from single_card_plan import single_card_plan


def test_manager_does_not_inspect_or_write_outside_scope(tmp_path,monkeypatch):
    novel=tmp_path/'book';state=novel/'repair_manager';state.mkdir(parents=True)
    (state/'state.json').write_text(json.dumps({'scope':{'episodes':[1]},'jobs':[],'passes':{},'phase':2}))
    for n in [1,2]:(novel/f'book_{n}').mkdir()
    touched=[]
    monkeypatch.setattr(manager,'episode_status',lambda d,*a:touched.append(d.name) or 'no_plan')
    monkeypatch.setattr(manager,'reconcile',lambda *a,**k:({},{}))
    m=manager.Manager(novel,tmp_path/'legacy');m.refresh(write=False)
    assert touched==['book_1'] and set(m.info)=={1}
    assert m.state['summary']['total']==1
    assert not (novel/'book_2/episode_review.json').exists()


def test_only_targeted_clip_loses_its_expression_reference():
    def clip(cid):
        return {'clip_id':cid,'prompt':'【人物】甲对应@图片1和@图片2。',
                'prompt_h3':'cached','prompt_h3_of':'cached',
                'references':[{'tag':'@图片1','role':'character','name':'甲','asset_id':'a','path':'a/turnaround.jpeg'},
                              {'tag':'@图片2','role':'character','name':'甲','asset_id':'a','path':'a/expressions.jpeg'}]}
    p={'clips':[clip('good'),clip('bad')]};kept=copy.deepcopy(p['clips'][0])
    assert single_card_plan(p,{'bad'})==['bad']
    assert p['clips'][0]==kept and len(p['clips'][1]['references'])==1


def test_scoped_speech_observation_does_not_change_sd_gate(tmp_path):
    from thin_profile import speech_gate_result
    novel=tmp_path/'book';state=novel/'repair_manager';state.mkdir(parents=True)
    p=state/'state.json';p.write_text(json.dumps({'scope':{'episodes':[1],'speech_gate':'observe'}}))
    raw={'passed':False,'issues':['missing_1.0_over_0.5']}
    assert speech_gate_result(novel,raw,novel/'book_1')['passed']
    assert not speech_gate_result(novel,raw,novel/'book_2')['passed']
    assert not speech_gate_result(novel,raw)['passed']
    p.write_text(json.dumps({'scope':{'episodes':[1],'speech_gate':'enforce'}}))
    assert not speech_gate_result(novel,raw,novel/'book_1')['passed']


def test_scoped_silence_policy_keeps_black_and_frozen_video_blocked(tmp_path):
    from thin_profile import assembly_gate_passed
    novel=tmp_path/'book';state=novel/'repair_manager';state.mkdir(parents=True)
    (state/'state.json').write_text(json.dumps({'scope':{'episodes':[1],'speech_gate':'observe'}}))
    a={'thin_passed':False,'max_hold_seconds':0,'media_qc':{'checks':{'silence_ratio':{'passed':False}}}}
    assert assembly_gate_passed(novel,a,novel/'book_1')
    assert not assembly_gate_passed(novel,a,novel/'book_2')
    assert not assembly_gate_passed(novel,{**a,'max_hold_seconds':20},novel/'book_1')
    a['media_qc']['checks']['black_frames']={'passed':False}
    assert not assembly_gate_passed(novel,a,novel/'book_1')
