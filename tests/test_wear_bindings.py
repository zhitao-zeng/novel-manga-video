"""Per-shot wardrobe metadata survives production boundaries without new inference."""
import copy

from novel_manga.application.packing.service import clip_entry
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.story.scene import SceneContext, resolve_scene, wears_bindings


def test_worn_accessory_does_not_replace_the_characters_entire_outfit():
    from novel_manga.story.h3 import compose,request_issues
    clip={'request_seconds':5,'references':[
        {'role':'character','name':'甲','path':'actor.jpeg'},
        {'role':'prop','name':'钱袋','path':'pouch.jpeg','wearers':['甲']}],
        'dialogue_bindings':[{'stage':1,'speaker_name':'甲','text':'钱袋还在。','delivery_mode':'visible_dialogue'}]}
    request=compose(clip,['<Subject 1> touches the pouch attached to the belt. <DIALOGUE_1>'],[('',[])])
    assert 'armor or garment design' not in request
    assert 'Use its garment design' not in request
    assert 'Other clothing follows' in request
    assert 'item worn by <Subject 1>' in request
    assert '<d>[Chinese] 钱袋还在.</d>' in request
    assert request_issues({**clip,'prompt_h3':request})==[]


def test_wears_bindings_keep_empty_states_visibility_addresses_and_independent_copies():
    shots = [
        {'index': 7, 'characters': ['甲', '乙'], 'in_frame': ['甲'],
         'wears': {'甲': '银甲', '乙': None}},
        {'index': 7, 'characters': ['甲'], 'in_frame': [], 'wears': {}},
        {'index': 9, 'characters': ['乙'], 'props': ['银甲'], 'independent_props': ['银甲']},
    ]
    saved = copy.deepcopy(shots)
    bindings = wears_bindings(shots)
    assert bindings == [
        {'stage': 1, 'source_stage': 7, 'wears': {'甲': '银甲', '乙': None}, 'in_frame': ['甲']},
        {'stage': 2, 'source_stage': 7, 'wears': {}, 'in_frame': []},
        {'stage': 3, 'source_stage': 9, 'wears': {}, 'in_frame': ['乙']},
    ]
    bindings[0]['wears']['甲'] = None
    bindings[0]['in_frame'].append('乙')
    bindings[2]['in_frame'].clear()
    assert shots == saved


def test_packing_preserves_resolved_wears_for_each_stage(tmp_path):
    base = {'characters': ['甲'], 'in_frame': ['甲'], 'location': '房间', 'turns': [],
            'visual_prompt': '甲站在门边', 'motion_prompt': '甲抬眼', 'end_state': '甲望向门口',
            'shot_scale': '中景', 'camera': '平视固定', 'light': '室内灯', 'duration_seconds': 4}
    script = {'shots': [{**copy.deepcopy(base), 'index': 7, 'wears': {'甲': '银甲'}},
                        {**copy.deepcopy(base), 'index': 8},
                        {**copy.deepcopy(base), 'index': 9, 'wears': {'甲': None}}]}
    saved = copy.deepcopy(script)
    resolved = resolve_scene(script, SceneContext())
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝',
                       style_fingerprint='test', characters=[Character(name='甲', appearance='黑发', wardrobe='便装')],
                       locations=['房间：木门'], props=[Prop(name='银甲', category='战甲', appearance='银白色')])
    context = {'episode_dir': tmp_path / 'book' / 'book_1', 'bible': bible,
               'location_map': {'房间': '房间：木门'}, 'overrides': {}, 'grammar': None, 'frame': None}
    raw = {'kind': 'video', 'location': '房间', 'seconds': 12, 'shots': resolved.shots}
    entry = clip_entry(raw, 'clip_01', context)
    assert entry['wears_bindings'] == [
        {'stage': 1, 'source_stage': 7, 'wears': {'甲': '银甲'}, 'in_frame': ['甲']},
        {'stage': 2, 'source_stage': 8, 'wears': {'甲': '银甲'}, 'in_frame': ['甲']},
        {'stage': 3, 'source_stage': 9, 'wears': {'甲': None}, 'in_frame': ['甲']},
    ]
    assert '脱下穿戴物' not in entry['prompt']  # a state is not an authored action
    entry['wears_bindings'][0]['wears']['甲'] = None
    entry['wears_bindings'][0]['in_frame'].clear()
    assert resolved.shots[0]['wears'] == {'甲': '银甲'}
    assert resolved.shots[0]['in_frame'] == ['甲']
    assert script == saved


def test_bare_state_keeps_an_explicitly_authored_removal_action(tmp_path):
    shot = {'index': 1, 'characters': ['甲'], 'in_frame': ['甲'], 'location': '房间', 'turns': [],
            'visual_prompt': '甲站在门边', 'motion_prompt': '甲解开扣带，脱下银甲并放在地上',
            'end_state': '甲望向门口', 'shot_scale': '中景', 'camera': '平视固定', 'light': '室内灯',
            'wears': {'甲': None}}
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝',
                       style_fingerprint='test', characters=[Character(name='甲', appearance='黑发', wardrobe='便装')],
                       locations=['房间：木门'])
    context = {'episode_dir': tmp_path/'book/book_1', 'bible': bible,
               'location_map': {'房间': '房间：木门'}, 'overrides': {}, 'grammar': None, 'frame': None}
    entry = clip_entry({'kind': 'video', 'location': '房间', 'seconds': 5, 'shots': [shot]}, 'clip_01', context)
    assert '脱下银甲并放在地上' in entry['prompt']
    assert '未额外穿戴物件' in entry['prompt'] and '脱下穿戴物' not in entry['prompt']


def test_agent_adoption_keeps_authored_wears_without_carrying_or_prop_inference():
    from novel_manga.application.agents.episode.contracts import adopt
    from support.episode_agent import CATALOG, SOURCES, episode, prompt

    draft = episode(turns=[], silent=True, voice=False)
    shots = draft['chapter_script']['shots']
    shots[0].update(wears={'甲': '银甲', '乙': None}, in_frame=['甲'], duration_seconds=5)
    shots[1].update(wears={}, in_frame=[], duration_seconds=5,
                    props=['银甲'], independent_props=['银甲'])
    shots[2].update(wears={'甲': None}, in_frame=['乙'], duration_seconds=5)
    draft['clips'][0].update(shot_indexes=[1, 2, 3], prompt_h3=prompt(shots[:3], silent=True, voice=False))
    del draft['clips'][1:3]
    saved = copy.deepcopy(draft)
    script, plan = adopt(draft, catalog=CATALOG, source_segments=SOURCES, profile={}, target_seconds=(90, 90))
    assert plan['clips'][0]['wears_bindings'] == [
        {'stage': 1, 'source_stage': 1, 'wears': {'甲': '银甲', '乙': None}, 'in_frame': ['甲']},
        {'stage': 2, 'source_stage': 2, 'wears': {}, 'in_frame': []},
        {'stage': 3, 'source_stage': 3, 'wears': {'甲': None}, 'in_frame': ['乙']},
    ]
    assert plan['clips'][1]['wears_bindings'] == [
        {'stage': 1, 'source_stage': 4, 'wears': {}, 'in_frame': ['甲']},
    ]
    plan['clips'][0]['wears_bindings'][0]['wears']['甲'] = None
    plan['clips'][0]['wears_bindings'][0]['in_frame'].clear()
    assert script['shots'][0]['wears'] == {'甲': '银甲', '乙': None}
    assert script['shots'][0]['in_frame'] == ['甲']
    assert draft == saved
