"""Only frame visibility and prop appearances share a reading; plot has its own evidence."""
import copy,json
import pytest
from novel_manga.application.planning import retention,presence,prop_marks
from novel_manga.models.bible import Prop


def sample():
    return [{'index':1,'label':'c1 stage1','segment_id':'seg_1','location':'诊室',
             'characters':['医生'],'in_frame':['医生'],'camera':'医生近景',
             'visual_prompt':'医生手握金色玉简，屏幕未亮。','motion_prompt':'医生回答。',
             'turns':[{'speaker_name':'医生','delivery_mode':'offscreen_dialogue','text':'我不会同行。'}]}]


def visible_answer():
    return {'grades':{'1':{'医生':'on_camera'}},'marks':[{'shot':1,'props':['玉简']}]}


def test_visibility_uses_frames_and_roster_without_full_plot_or_spoken_text(monkeypatch):
    seen=[]
    monkeypatch.setattr(presence,'ask_json',lambda parts,schema,**kw:seen.append(parts[0]['text'])or visible_answer())
    props=[Prop(name='玉简',category='法器',appearance='金色')]
    v=presence.review_visibility(sample(),['医生'],roster={'医生':'实体','无关人':'新资料'},props=props)
    assert len(seen)==1 and '我不会同行'not in seen[0]and'无关人'not in seen[0]
    payload=json.loads(seen[0].split('\n')[-1]);assert payload['shots'][0]['in_frame']==['医生']
    assert prop_marks.interpret(sample(),props,v)=={1:['玉简']}


def test_visibility_reuses_same_bodies_for_dialogue_and_unrelated_roster_changes(monkeypatch):
    calls=[];monkeypatch.setattr(presence,'ask_json',lambda *a,**kw:calls.append(True)or visible_answer())
    saved=presence.review_visibility(sample(),['医生'],roster={'医生':'实体'})
    changed=copy.deepcopy(sample());changed[0]['turns'][0]['text']='我愿意同行。';changed[0]['turns'][0]['inner_monologue']=True
    presence.review_visibility(changed,['医生'],roster={'医生':'实体','无关人':'新增'},previous=json.loads(json.dumps(saved)))
    assert len(calls)==1
    changed[0]['camera']='门口全景'
    presence.review_visibility(changed,['医生'],roster={'医生':'实体'},previous=saved)
    assert len(calls)==2


def test_plot_is_rechecked_for_changed_meaning_but_same_input_is_reused(monkeypatch):
    calls=[]
    def judge(*a,**kw):
        calls.append(kw['name'])
        if len(calls)==2:return {'verdict':'pass','errors':[],'note':''}
        return {'verdict':'fail','errors':[{'shot':1,'field':'speech','source_quote':'医生答应同行','draft_quote':'我不会同行','viewer_error':'实际对白否定了同行'}],'note':''}
    monkeypatch.setattr(retention,'ask_json',judge)
    saved,issues=retention.review('医生答应同行',sample(),'原文：医生答应同行。')
    assert issues and '否定' in issues[0].message
    retention.review('医生答应同行',sample(),'原文：医生答应同行。',previous=json.loads(json.dumps(saved)))
    changed=copy.deepcopy(sample());changed[0]['turns'][0]['text']='我愿意同行。'
    retention.review('医生答应同行',changed,'原文：医生答应同行。',previous=saved)
    assert len(calls)==2


@pytest.mark.parametrize('field,value',[('grades',{}),('marks',None)])
def test_missing_visibility_judgment_is_not_reusable(monkeypatch,field,value):
    v=visible_answer();v[field]=value
    monkeypatch.setattr(presence,'ask_json',lambda *a,**kw:v)
    with pytest.raises(ValueError):presence.review_visibility(sample(),['医生'])


def test_outline_is_adaptation_intent_not_a_mandatory_quote_checklist(monkeypatch):
    section='选材说明：1.相隔1.5米（seg_1-2）。2.医生答应同行（seg_3）。'
    def judge(parts,schema,**kw):
        p=json.loads(parts[0]['text'].split('\n')[-1])
        assert p['adaptation_intent']==section
        assert 'verdicts' not in schema['properties']
        return {'verdict':'pass','errors':[],'note':'允许合理取舍'}
    monkeypatch.setattr(retention,'ask_json',judge)
    assert retention.review(section,sample(),'原文')[1]==[]
