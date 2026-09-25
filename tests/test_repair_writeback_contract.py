"""Saved summoning failure: correct repair survives the real writeback/compile path."""
import copy
from pathlib import Path

import pytest

from novel_manga.models.bible import Character, StoryBible
from novel_manga.repair.contracts import schema_for
from novel_manga.repair.execution import apply_stage
from novel_manga.story.actions import action_text
from novel_manga.story.scene import SceneContext, resolve_scene
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.application.rendering import h3
from novel_manga.util import atomic_write_json


def corrected_reply():
    # Event/actions from the saved reply; object/light fields explicitly completed
    # as an offline correct-answer fixture, not claimed as a live model response.
    return {'origin_index': 16, 'in_frame': ['托尼', '席勒'],
            'actions': [{'actor': '托尼', 'action': '打响指', 'target': ''},
                        {'actor': '空机甲', 'action': '飞入屋内并停稳', 'target': ''},
                        {'actor': '席勒', 'action': '指着空机甲说话', 'target': '空机甲'}],
            'event': '托尼打响指，一套无人空机甲飞入屋内，席勒指着它说话。',
            'extras': [], 'scene_objects': ['无人空机甲'], 'props': ['银白装甲'],
            'wears': {'托尼': '银白装甲'}, 'light': '台灯照亮桌面和人物',
            'visual_prompt': '席勒在左、穿甲托尼在右，空机甲尚未出现。',
            'end_state': '一套无人空机甲停在两人之间，两人仍在原位。',
            'camera': '固定全景，三者均站在地面', 'shot_scale': '全景', 'speakers': []}


def old_stage():
    return {'index': 1, 'origin_index': 16, 'segment_id': 's1', 'source_quote': '托尼打响指，又一台机甲飞进来。',
            'location': '诊室', 'characters': ['托尼', '席勒'], 'listeners': ['席勒'],
            'extras': ['空机甲'], 'actions': [], 'props': ['银白装甲'], 'wears': {'托尼':'银白装甲'},
            'visual_prompt': '空机甲已经在房间里', 'motion_prompt': '旧的重复动作',
            'end_state': '托尼在画外', 'camera': '旧机位', 'shot_scale': '近景', 'light': '机甲反光',
            'sfx': '喷气声', 'turns': [{'speaker_name':'席勒','delivery_mode':'visible_dialogue','text':'让我坐这个？','emotion':'平静'}]}


def test_repair_can_express_object_wearing_and_light_changes():
    schema=schema_for(['托尼','席勒'],[16],reframe=True,bible={'locations':['诊室'],'props':[{'name':'银白装甲'}]})
    fields=schema['properties']['stages']['items']['properties']
    assert {'scene_objects','props','wears','light'} <= fields.keys()


def test_saved_correct_event_is_not_duplicated_or_moved_before_its_trigger():
    shot=old_stage();fix=corrected_reply();before=copy.deepcopy(fix)
    apply_stage(shot,fix,['托尼','席勒'],reframe=True)
    assert shot['motion_prompt']==fix['event']
    assert shot['extras']==[]
    assert shot['scene_objects']==['无人空机甲']
    assert shot['light']=='台灯照亮桌面和人物'
    assert fix==before


def test_target_already_inside_the_verb_is_not_added_twice():
    assert action_text([{'actor':'席勒','action':'指着空机甲说话','target':'空机甲'}])=='席勒指着空机甲说话'


def test_explicit_empty_object_lists_and_null_wearing_clear_old_state():
    shot=old_stage();shot['scene_objects']=['旧物件']
    fix=corrected_reply();fix.update(extras=[],scene_objects=[],props=[],wears={'托尼':None})
    apply_stage(shot,fix,['托尼','席勒'],reframe=True)
    assert shot['extras']==shot['props']==shot['scene_objects']==[]
    assert shot['wears']=={'托尼':None}


def test_correct_reply_reaches_final_h3_without_old_state_or_extra_actor(tmp_path,monkeypatch):
    import json
    shot=old_stage();fix=corrected_reply();apply_stage(shot,fix,['托尼','席勒'],reframe=True)
    path=tmp_path/'chapter_script.json';atomic_write_json(path,{'shots':[shot]})
    resolved=resolve_scene(json.loads(path.read_text()),SceneContext()).shots
    assert resolved[0]['light']==fix['light'] and resolved[0]['extras']==[]
    bible=StoryBible(novel_title='试片',genre='generic',visual_style='2d',palette='灰',style_fingerprint='test',
                    characters=[Character(name=n,appearance='黑发',wardrobe='常服') for n in ['托尼','席勒']],locations=['诊室'])
    compiler=ClipCompiler(compiler_options())
    cn=compiler.compile_prompt({'shots':resolved,'request_seconds':10},bible,['托尼','席勒'],[],'诊室')
    # The clip header summarizes the event; count the chronological stage,
    # not that non-temporal summary plus its detailed description.
    stage_text=h3.stages_of(cn)[0][0]
    assert stage_text.count('打响指')==1 and stage_text.count('飞入')==1
    assert '指着空机甲说话空机甲' not in cn and '机甲反光' not in cn
    assert '本阶段无参考图的配角：空机甲' not in cn
    seen=[]
    def translate(parts,schema,**kwargs):
        seen.append(parts[0]['text'])
        return {'shots':['<Subject 1> wearing <Subject 3> snaps once. One separate empty suit flies in and settles between the two people. <Subject 2> points at it.'],
                'soundscape':'A brief jet sound and a landing thud.'}
    monkeypatch.setattr(h3,'ask_json',translate)
    clip={'clip_id':'c','prompt':cn,'request_seconds':10,'render_family':'2d','shot_timing':[{'seconds':10}],
          'references':[{'role':'character','name':'托尼','path':'tony.jpeg'},
                        {'role':'character','name':'席勒','path':'doctor.jpeg'},
                        {'role':'prop','name':'银白装甲','path':'armor.jpeg','wearers':['托尼']}],
          'dialogue_bindings':[{'stage':1,'speaker_name':'席勒','delivery_mode':'visible_dialogue','text':'让我坐这个？','emotion':'平静'}]}
    assert h3.convert(clip)
    assert clip['prompt_h3'].count('snaps once')==1
    assert clip['prompt_h3'].count('empty suit flies in')==1
    assert '本阶段无参考图的配角：空机甲' not in seen[0]
