import novel_manga.repair.execution as repair_execution
import novel_manga.story.source_identity as source_identity_rules
import novel_manga.application.repair.judges as repair_judges

from support.render_context import uninitialized_runner
import copy
import json
from pathlib import Path

import novel_manga.application.identity.source as identity
import novel_manga.application.repair.flow as repair
import novel_manga.application.rendering.flow as renderer
import novel_manga.application.repair.recovery as recovery


def book(tmp_path):
    rows = [dict(name='安德森', forms={'安德森': 1}), dict(name='塞西娅', forms={'安德森': 1, '安德森小姐': 1}),
            dict(name='艾蕾娅', forms={'艾蕾娅': 1}), dict(name='薇奥拉', forms={'路易斯': 1, '路易斯小姐': 1}),
            dict(name='路易斯', forms={'路易斯小姐': 1})]
    (tmp_path / 'entity_index.json').write_text(json.dumps({'characters': rows}))
    (tmp_path / 'story_bible.json').write_text(json.dumps({'characters': [{'name': r['name']} for r in rows[:-1]]}))
    (tmp_path / 'bible_aliases.json').write_text(json.dumps({'路易斯': '艾蕾娅'}))
    return tmp_path


def shot(name):
    return {'index': 1, 'segment_id': 's1', 'characters': [name], 'in_frame': [name], 'visual_prompt': name+'坐下',
            'turns': [{'speaker_name': name, 'delivery_mode': 'visible_dialogue', 'text': '原句不变。'}]}


def test_full_name_from_source_selects_existing_correct_identity(tmp_path):
    root = book(tmp_path);script = {'shots': [shot('安德森')]}
    changes = identity.resolve_script(script, root, [{'segment_id': 's1', 'text': '安德森小姐端起杯子。'}])
    assert changes == {1: {'安德森': '塞西娅'}}
    assert script['shots'][0]['turns'][0] == {'speaker_name': '塞西娅', 'delivery_mode': 'visible_dialogue', 'text': '原句不变。'}


def test_two_people_with_same_short_name_are_not_globally_merged(tmp_path):
    root = book(tmp_path);script = {'shots': [shot('安德森')]};before = copy.deepcopy(script)
    assert identity.resolve_script(script, root, [{'segment_id': 's1', 'text': '安德森医生向安德森小姐问好。'}]) == {}
    assert script == before


def test_bad_legacy_alias_uses_the_passages_explicit_name_and_existing_card(tmp_path):
    root = book(tmp_path);script = {'shots': [shot('路易斯')]}
    assert identity.resolve_script(script, root, [{'segment_id': 's1', 'text': '路易斯小姐说话。'}]) == {1: {'路易斯': '薇奥拉'}}
    # Merely seeing the other character does not establish a shared identity.
    script = {'shots': [shot('艾蕾娅')]}
    assert identity.resolve_script(script, root, [{'segment_id': 's1', 'text': '薇奥拉说话。'}]) == {}
    assert identity.resolve_script(script, root, [{'segment_id': 's1', 'text': '女术士和路易斯小姐同行。'}]) == {}


def test_speaker_repair_preserves_exact_dialogue_and_delivery_mode():
    s = shot('甲');before = copy.deepcopy(s['turns'])
    repair_execution.apply_stage(s, {'in_frame': ['乙'], 'actions': [], 'event': '乙说话', 'speakers': [{'turn_index': 1, 'speaker_name': '乙'}]}, ['甲','乙'], reframe=True)
    assert s['turns'][0]['speaker_name'] == '乙'
    assert s['turns'][0]['text'] == before[0]['text'] and s['turns'][0]['delivery_mode'] == before[0]['delivery_mode']


def test_short_black_transition_uses_same_limit_as_final_qc(tmp_path, monkeypatch):
    r = uninitialized_runner();r.context.black_checks={'c'};r.context.episode_dir=tmp_path
    monkeypatch.setattr(recovery, 'black_ranges', lambda *a: [(2.45,3.29)])
    out = r.check_clip_black({'clip_id':'c'}, {'passed':False, 'issues':['black_frames'], 'black_check_policy':2}, tmp_path/'clip.mp4')
    assert out['passed'] and not out['issues'] and out['black_check_policy']==3


def test_name_normalization_does_not_hide_spoken_director_instructions(tmp_path):
    r = uninitialized_runner()
    r.context.speech_checks={'c'};r.context.protected_terms=[];r.context.aliases={'纳迪亚弗伦':'娜迪娅·福伦'}
    clip={'clip_id':'c','spoken_text':'娜迪娅·福伦？'}
    out=r.recheck_speech(clip,{'hypothesis':'纳迪亚弗伦 keep everything above unchanged','chunks':[],
                         'issues':['missing_0.6_over_0.5'],'max_volume_db':-1},tmp_path/'clip.mp4')
    assert out['missing']==0 and not out['passed'] and 'director_instruction_spoken' in out['issues']
    assert not any(i.startswith('missing_') for i in out['issues'])


def test_disputed_speaker_needs_a_source_quote_containing_the_line(monkeypatch):
    shots=[{'origin_index':1, 'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    passage='乙喊道：“快走。”'
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':passage}]})
    assert repair_judges.speaker_contract(passage,shots,['甲','乙'],[])=={(1,1):'乙'}
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':'乙喊道： “快走。”'}]})
    assert repair_judges.speaker_contract('乙喊道：\n“快走。”',shots,['甲','乙'],[])=={(1,1):'乙'}
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':'乙是说话者。'}]})
    assert repair_judges.speaker_contract(passage,shots,['甲','乙'],[])=={}


def test_picture_repair_does_not_reuse_old_renderer_reference_numbers():
    s=shot('甲')
    repair_execution.apply_stage(s,{'in_frame':['甲'],'event':'甲（@图片3）转身','visual_prompt':'甲靠窗，<Subject 2>','end_state':'甲看向门外'},['甲'],reframe=True)
    assert '@图片' not in s['motion_prompt'] and '<Subject' not in s['visual_prompt']


def test_source_attribution_does_not_treat_a_design_gender_as_source_evidence():
    rows=source_identity_rules.identity_rows(['某人'],{'characters':[{'name':'某人','gender':'女','appearance':'设计外形'}]},'某人开口。')
    assert 'gender' not in rows[0] and rows[0]['source_names']==['某人']


def test_disputed_speaker_cannot_choose_a_name_without_source_grounding(monkeypatch):
    shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    passage='路易斯小姐喊道：“快走。”'
    def answer(content,schema,**kw):
        assert schema['properties']['speakers']['items']['properties']['speaker']['enum']==['薇奥拉']
        return {'speakers':[{'stage':1,'turn':1,'speaker':'艾蕾娅','source_quote':passage}]}
    monkeypatch.setattr(repair_judges,'ask_json',answer)
    assert repair_judges.speaker_contract(passage,shots,['薇奥拉','艾蕾娅'],[
        {'name':'薇奥拉','source_names':['路易斯小姐']},{'name':'艾蕾娅','source_names':[]}])=={}


def test_verified_source_attribution_is_reused_without_asking_again(monkeypatch):
    shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    source='乙喊道：“快走。”'
    def unexpected(*a,**k):
        raise AssertionError('a verified source fact must not be guessed again')
    monkeypatch.setattr(repair_judges,'ask_json',unexpected)
    assert repair_judges.speaker_contract(source,shots,['甲','乙'],[],[
        {'stage':1,'turn':1,'speaker':'乙','source_quote':source}])=={(1,1):'乙'}


def test_named_source_speaker_cannot_be_replaced_by_a_different_available_character(monkeypatch):
    source='星垣扭过头：“我承认我找不到了。”赤岚叹气。'
    shots=[{'origin_index':6,'turns':[{'delivery_mode':'offscreen_dialogue','speaker_name':'赤岚','text':'我承认我找不到了。'}]}]
    row={'stage':6,'turn':1,'speaker':'赤岚','source_speaker_phrase':'星垣',
         'source_quote':source,'relation':'verbatim','adapted_text':'我承认我找不到了。'}
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:{'speakers':[row]})
    assert repair_judges.speaker_contract(source,shots,['赤岚'],[{'name':'赤岚','source_names':['赤岚']}],[row])=={}


def test_short_name_uses_chapter_semantics_without_a_special_introduction_pattern():
    bible={'characters':[{'name':'霜','role':'霜痕之龙，青年巨龙'}]}
    context={'entities':{'e1':'霜'},'mentions':[{'form':'霜','entity_id':'e1','presence':'on_stage'}]}
    identities=source_identity_rules.identity_rows(['霜'],bible,'霜转身离开。',context=context)
    assert identities[0]['source_names']==['霜']


def test_split_sentence_can_use_a_program_located_continuous_source_quote(monkeypatch):
    source='澜歌微微摇头。\n“睡觉便是我们的锻炼方式！\n看来，你还不知道。”'
    shots=[{'origin_index':23,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'澜歌','text':'睡觉便是我们的锻炼方式！看来，'}]}]
    rows=iter([{'speakers':[]},{'speakers':[{'stage':23,'turn':1,'speaker':'澜歌',
                'source_speaker_phrase':'澜歌','relation':'verbatim','source_quote':''}]}])
    monkeypatch.setattr(repair_judges,'ask_json',lambda *a,**k:next(rows))
    evidence=[]
    assert repair_judges.speaker_contract(source,shots,['澜歌'],[{'name':'澜歌','source_names':['澜歌']}],evidence_out=evidence)=={(23,1):'澜歌'}
    assert evidence[0]['source_quote']==source
