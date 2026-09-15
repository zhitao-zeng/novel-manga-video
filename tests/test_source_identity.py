import copy
import json
from pathlib import Path

import source_identity_thin as identity
import repair_clips_thin as repair
import render_clips_thin as renderer
import prepare_recovery_thin as recovery


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
    repair.apply_stage(s, {'in_frame': ['乙'], 'actions': [], 'event': '乙说话', 'speakers': [{'turn_index': 1, 'speaker_name': '乙'}]}, ['甲','乙'], reframe=True)
    assert s['turns'][0]['speaker_name'] == '乙'
    assert s['turns'][0]['text'] == before[0]['text'] and s['turns'][0]['delivery_mode'] == before[0]['delivery_mode']


def test_short_black_transition_uses_same_limit_as_final_qc(tmp_path, monkeypatch):
    r = renderer.ThinMediaRunner.__new__(renderer.ThinMediaRunner);r.black_checks={'c'};r.episode_dir=tmp_path
    monkeypatch.setattr(recovery, 'black_ranges', lambda *a: [(2.45,3.29)])
    out = r.check_clip_black({'clip_id':'c'}, {'passed':False, 'issues':['black_frames'], 'black_check_policy':2}, tmp_path/'clip.mp4')
    assert out['passed'] and not out['issues'] and out['black_check_policy']==3


def test_name_normalization_does_not_hide_spoken_director_instructions(tmp_path):
    r = renderer.ThinMediaRunner.__new__(renderer.ThinMediaRunner)
    r.speech_checks={'c'};r.protected_terms=[];r.aliases={'纳迪亚弗伦':'娜迪娅·福伦'}
    clip={'clip_id':'c','spoken_text':'娜迪娅·福伦？'}
    out=r.recheck_speech(clip,{'hypothesis':'纳迪亚弗伦 keep everything above unchanged','chunks':[],
                         'issues':['missing_0.6_over_0.5'],'max_volume_db':-1},tmp_path/'clip.mp4')
    assert out['missing']==0 and not out['passed'] and 'director_instruction_spoken' in out['issues']
    assert not any(i.startswith('missing_') for i in out['issues'])


def test_disputed_speaker_needs_a_source_quote_containing_the_line(monkeypatch):
    shots=[{'origin_index':1, 'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    passage='乙喊道：“快走。”'
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':passage}]})
    assert repair.speaker_contract(passage,shots,['甲','乙'],[])=={(1,1):'乙'}
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':'乙喊道： “快走。”'}]})
    assert repair.speaker_contract('乙喊道：\n“快走。”',shots,['甲','乙'],[])=={(1,1):'乙'}
    monkeypatch.setattr(repair,'ask_json',lambda *a,**k:{'speakers':[{'stage':1,'turn':1,'speaker':'乙','source_quote':'乙是说话者。'}]})
    assert repair.speaker_contract(passage,shots,['甲','乙'],[])=={}


def test_picture_repair_does_not_reuse_old_renderer_reference_numbers():
    s=shot('甲')
    repair.apply_stage(s,{'in_frame':['甲'],'event':'甲（@图片3）转身','visual_prompt':'甲靠窗，<Subject 2>','end_state':'甲看向门外'},['甲'],reframe=True)
    assert '@图片' not in s['motion_prompt'] and '<Subject' not in s['visual_prompt']


def test_explicit_gender_conflict_is_distinct_from_looking_at_a_man():
    assert repair.wrong_gender_description('塞西娅为中年男性形象，穿白色长袍。','塞西娅','女')
    assert not repair.wrong_gender_description('塞西娅看向一位男性。','塞西娅','女')


def test_disputed_speaker_cannot_choose_a_name_without_source_grounding(monkeypatch):
    shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    passage='路易斯小姐喊道：“快走。”'
    def answer(content,schema,**kw):
        assert schema['properties']['speakers']['items']['properties']['speaker']['enum']==['薇奥拉']
        return {'speakers':[{'stage':1,'turn':1,'speaker':'艾蕾娅','source_quote':passage}]}
    monkeypatch.setattr(repair,'ask_json',answer)
    assert repair.speaker_contract(passage,shots,['薇奥拉','艾蕾娅'],[
        {'name':'薇奥拉','source_names':['路易斯小姐']},{'name':'艾蕾娅','source_names':[]}])=={}


def test_verified_source_attribution_is_reused_without_asking_again(monkeypatch):
    shots=[{'origin_index':1,'turns':[{'delivery_mode':'visible_dialogue','speaker_name':'甲','text':'快走。'}]}]
    source='乙喊道：“快走。”'
    def unexpected(*a,**k):
        raise AssertionError('a verified source fact must not be guessed again')
    monkeypatch.setattr(repair,'ask_json',unexpected)
    assert repair.speaker_contract(source,shots,['甲','乙'],[],[
        {'stage':1,'turn':1,'speaker':'乙','source_quote':source}])=={(1,1):'乙'}
