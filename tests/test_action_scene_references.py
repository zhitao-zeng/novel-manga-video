from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
import novel_manga.application.packing.service as packing_service
import novel_manga.repair.execution as repair_execution
import copy

import pytest

from novel_manga.models.bible import Character, StoryBible
import novel_manga.planning.validation as pc_validation
from novel_manga.planning.context import PlannerContext
import novel_manga.application.repair.flow as repair
from novel_manga.story import framing


@pytest.mark.parametrize('actor, target, verb', [
    ('沈行舟', '灰色野山羊', '挥铲砍'),
    ('灰色野山羊', '沈行舟', '扑向'),
    ('沈行舟', '', '跃起'),
    ('沈行舟', '沈行舟', '给自己包扎'),
    ('沈行舟', '木门', '推开'),
])
def test_scene_action_survives_normalization_and_packing(monkeypatch, actor, target, verb):
    planner_ctx = PlannerContext.from_env()
    monkeypatch.setattr(planner_ctx, 'episode_seconds_min', 0)
    source = '沈行舟在溪边遇到灰色野山羊，挥起铲子迎击，山羊随后摔进溪水里。'
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='国漫', palette='青', style_fingerprint='test',
                       characters=[Character(name='沈行舟', appearance='黑发', wardrobe='长衫')], locations=['溪边：草地溪水'])
    stage = {'segment_id': 'seg_1', 'source_quote': source, 'start_state': '沈行舟站在溪边',
             'event': '沈行舟本能地挥铲砍向山羊，山羊摔进溪水里。', 'end_state': '沈行舟看向溪水',
             'camera': '溪边平视', 'light': '日光从左侧照入', 'sfx': '', 'shot_scale': '中景',
             'in_frame': ['沈行舟'], 'extras': ['灰色野山羊'],
             'actions': [{'actor': actor, 'action': verb, 'target': target}],
             'turns': [{'speaker_name': '', 'delivery_mode': 'silent_action', 'text': '挥铲迎击', 'emotion': '惊恐', 'chat_target': ''}]}
    raw = {'clips': [{'clip_id': 'c', 'location': '溪边', 'characters': ['沈行舟'], 'avoid': '', 'stages': [stage]}],
           'skipped_segments': []}
    validation = pc_validation.validate_and_normalize(raw, [{'segment_id': 'seg_1', 'text': source}],
                                                      bible, {'溪边': bible.locations[0]}, source, ctx=planner_ctx)
    errors, _, shots = validation.errors, validation.warnings, validation.shots
    assert not errors
    assert shots[0]['actions'] == stage['actions']
    assert shots[0]['characters'] == ['沈行舟']
    clip = ClipCompiler(compiler_options()).pack(copy.deepcopy(shots))[0]
    clip['request_seconds'] = int(clip['seconds'])
    prompt = ClipCompiler(compiler_options()).compile_prompt(clip, bible, ['沈行舟'], [], '溪边')
    assert actor + verb + target in prompt
    if target == '灰色野山羊':
        assert '沈行舟挥铲砍沈行舟' not in prompt


def test_repair_removes_old_generated_prefix_and_keeps_extra_target():
    shot = {'characters': ['沈行舟'], 'turns': [], 'extras': ['灰色野山羊'],
            'actions': [{'actor': '沈行舟', 'action': '挥铲砍', 'target': '沈行舟'}],
            'motion_prompt': '沈行舟挥铲砍沈行舟。沈行舟本能地挥铲砍向山羊。'}
    repair_execution.apply_stage(shot, {'in_frame': ['沈行舟'], 'extras': ['灰色野山羊'],
                             'actions': [{'actor': '沈行舟', 'action': '挥铲砍', 'target': '灰色野山羊'}]}, ['沈行舟'])
    assert shot['actions'][0]['target'] == '灰色野山羊'
    assert shot['characters'] == ['沈行舟']
    assert '沈行舟挥铲砍沈行舟' not in shot['motion_prompt']
    assert '沈行舟挥铲砍灰色野山羊' in shot['motion_prompt']


def test_extra_actor_does_not_turn_into_a_named_bystander_in_blocking():
    shot = {'characters': ['甲', '乙'], 'extras': ['灰色野山羊'],
            'actions': [{'actor': '灰色野山羊', 'action': '扑向', 'target': '甲'}]}
    text = framing.blocking_note(shot)
    assert '灰色野山羊在画面左侧前景，甲在右侧前景' in text
    assert '乙只在后景' in text
    shot['actions'][0]['actor'] = ''
    assert framing.blocking_note(shot) == ''


def test_extra_only_scene_does_not_keep_an_old_named_character():
    shot = {'characters': ['沈行舟'], 'turns': [], 'motion_prompt': '山羊低头喝水'}
    repair_execution.apply_stage(shot, {'in_frame': [], 'extras': ['灰色野山羊'],
                             'actions': [{'actor': '灰色野山羊', 'action': '喝水', 'target': ''}],
                             'event': '山羊低头喝水'}, ['沈行舟'])
    assert shot['characters'] == [] and shot['actions'][0]['actor'] == '灰色野山羊'


def test_visible_speaker_still_has_a_character_binding():
    shot = {'characters': ['沈行舟'], 'turns': [{'speaker_name': '沈行舟', 'delivery_mode': 'visible_dialogue', 'text': '过来。'}]}
    repair_execution.apply_stage(shot, {'in_frame': [], 'extras': ['灰色野山羊'], 'actions': [], 'event': '沈行舟开口'}, ['沈行舟'])
    assert shot['characters'] == ['沈行舟']


def test_named_offscreen_target_is_not_forced_into_the_picture():
    shot = {'characters': ['甲'], 'turns': [], 'motion_prompt': '甲朝门外的乙挥手'}
    repair_execution.apply_stage(shot, {'in_frame': ['甲'], 'extras': [],
                             'actions': [{'actor': '甲', 'action': '向门外挥手示意', 'target': '乙'}],
                             'event': '甲朝门外的乙挥手'}, ['甲', '乙'])
    assert shot['characters'] == ['甲'] and shot['actions'][0]['target'] == '乙'
