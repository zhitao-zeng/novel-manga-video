"""Per-shot wardrobe metadata survives production boundaries without new inference."""
import copy

from novel_manga.application.packing.service import clip_entry
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.story.scene import SceneContext, resolve_scene, wears_bindings


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
    entry['wears_bindings'][0]['wears']['甲'] = None
    entry['wears_bindings'][0]['in_frame'].clear()
    assert resolved.shots[0]['wears'] == {'甲': '银甲'}
    assert resolved.shots[0]['in_frame'] == ['甲']
    assert script == saved
