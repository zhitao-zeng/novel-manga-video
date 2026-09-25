"""Program transforms preserve the scene an author actually specified."""
import copy

from novel_manga.application.planning import presence
from novel_manga.application.packing.context import compiler_options
from novel_manga.models.bible import Character, StoryBible
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.repair.execution import apply_stage
from novel_manga.story.compilation import ClipCompiler
from novel_manga.story.framing import visible_speaker_shots


SOURCE = '林凡站在窗边向屋外的顾宁挥手，顾宁隔着窗户回答，随后林凡转身看向桌面。'


def bible():
    return StoryBible(novel_title='test', genre='generic', visual_style='2d', palette='',
                      style_fingerprint='test', characters=[
                          Character(name=n, appearance='黑发', wardrobe='外套') for n in ['林凡', '顾宁']],
                      locations=['房间：窗边有桌子'])


def stage(**fields):
    return {'segment_id': 's1', 'source_quote': SOURCE, 'start_state': '林凡站在窗边，顾宁在画外。',
            'event': '林凡向窗外挥手。', 'end_state': '林凡收回手，仍在窗边。',
            'camera': '侧面固定机位，林凡在右侧', 'light': '窗外日光', 'sfx': '', 'shot_scale': '中景',
            'in_frame': ['林凡'], 'extras': [], 'actions': [], 'turns': [], **fields}


def normalized(item):
    raw = {'clips': [{'clip_id': 'c1', 'location': '房间', 'characters': ['林凡', '顾宁'],
                      'avoid': '', 'stages': [item]}], 'skipped_segments': []}
    ctx = PlannerContext(); ctx.episode_seconds_min = 0
    for name in ['林凡', '顾宁']:
        ctx.entity_forms[name] = {name}; ctx.entity_generic[name] = False
    result = validate_and_normalize(raw, [{'segment_id': 's1', 'text': SOURCE}], bible(),
                                    {'房间': '房间：窗边有桌子'}, SOURCE, ctx=ctx)
    return result.shots


def test_event_and_explicit_offscreen_target_survive_normalization():
    authored = stage(actions=[{'actor': '林凡', 'action': '向窗外挥手示意', 'target': '顾宁'}])
    before = copy.deepcopy(authored)
    shot, = normalized(authored)
    assert shot['motion_prompt'] == authored['event']
    assert shot['characters'] == shot['in_frame'] == ['林凡']
    assert shot['actions'] == authored['actions']
    assert authored == before


def test_empty_frame_is_not_filled_from_clip_roster():
    shot, = normalized(stage(in_frame=[], start_state='桌上的空杯', event='杯子静置。',
                             end_state='杯子仍在桌上。'))
    assert shot['characters'] == shot['in_frame'] == []


def test_visible_listener_is_not_removed_or_turned_away():
    item = stage(in_frame=['林凡', '顾宁'], start_state='林凡在右侧站着，顾宁在左侧坐着看他。',
                 turns=[{'speaker_name': '林凡', 'delivery_mode': 'visible_dialogue',
                         'text': '过来吧。', 'emotion': '平静', 'chat_target': ''}])
    shot, = normalized(item)
    assert shot['characters'] == shot['in_frame'] == ['林凡', '顾宁']
    assert shot['camera'] == item['camera']
    assert shot['visual_prompt'] == item['start_state']


def test_action_participants_are_candidates_not_proof_of_visibility(monkeypatch):
    shot = {'characters': ['林凡', '顾宁'], 'in_frame': ['林凡', '顾宁'], 'listeners': ['顾宁'],
            'visual_prompt': '林凡向画外的顾宁挥手', 'motion_prompt': '林凡挥手', 'end_state': '',
            'actions': [{'actor': '林凡', 'action': '挥手', 'target': '顾宁'}], 'turns': []}
    prompts = []
    def judge(parts, *args, **kwargs):
        prompts.append(parts[0]['text'])
        return {'grades': [{'shot': 1, 'name': '顾宁', 'grade': 'absent'}]}
    monkeypatch.setattr(presence, 'ask_json', judge)
    grades = presence.grade_presence([shot], ['林凡', '顾宁'])
    assert prompts and '顾宁' in prompts[0]
    presence.apply_presence_grades(shot, grades[1], ['林凡', '顾宁'])
    assert shot['characters'] == shot['in_frame'] == ['林凡']
    assert shot['listeners'] == []


def test_repair_writes_complete_event_without_prefixing_actions():
    shot = {'characters': ['林凡'], 'turns': [], 'actions': [], 'motion_prompt': '旧事件'}
    fix = {'in_frame': ['林凡'], 'extras': [], 'event': '装甲飞入室内，落在桌前。',
           'actions': [{'actor': '林凡', 'action': '驾驶装甲飞入并落地', 'target': '顾宁'}]}
    apply_stage(shot, fix, ['林凡', '顾宁'])
    assert shot['motion_prompt'] == fix['event']
    assert shot['characters'] == shot['in_frame'] == ['林凡']


def test_compiler_does_not_invent_a_pose_when_no_explicit_frame_field_exists():
    shot = {'index': 1, 'characters': ['林凡', '顾宁'], 'listeners': [], 'extras': [],
            'actions': [{'actor': '林凡', 'action': '挥手', 'target': '顾宁'}],
            'visual_prompt': '林凡在右边，顾宁在左边，二人都背对镜头。', 'motion_prompt': '林凡挥手。',
            'end_state': '林凡放下手。', 'camera': '后方固定机位', 'light': '日光',
            'shot_scale': '中景', 'turns': []}
    prompt = ClipCompiler(compiler_options()).compile_prompt(
        {'shots': [shot], 'request_seconds': 5}, bible(), ['林凡', '顾宁'], [], '房间')
    assert shot['visual_prompt'] in prompt and shot['camera'] in prompt
    assert '林凡在画面左侧前景' not in prompt
    assert '两人侧面相对' not in prompt


def test_speaker_split_keeps_authored_event_and_uses_its_end_state():
    base = {'characters': ['林凡', '顾宁'], 'in_frame': ['林凡', '顾宁'],
            'actions': [{'actor': '林凡', 'action': '拉开门', 'target': ''}],
            'visual_prompt': '门还关着，两人在门边。', 'motion_prompt': '林凡推门，顾宁后退。',
            'end_state': '门已打开，两人站在门边。'}
    turns = [{'speaker_name': n, 'delivery_mode': 'visible_dialogue', 'text': '来了。'}
             for n in ['林凡', '顾宁']]
    pieces, _ = visible_speaker_shots(base, turns, ['林凡', '顾宁'], 's1')
    assert len(pieces) == 2
    assert pieces[0]['motion_prompt'] == base['motion_prompt']
    assert pieces[1]['visual_prompt'] == base['end_state']
    assert pieces[1]['motion_prompt'] == '' and pieces[1]['actions'] == []
    assert all(p['characters'] == base['in_frame'] for p in pieces)


def test_compiler_does_not_prepend_a_different_start_to_an_authored_start():
    first, = normalized(stage(end_state='林凡站在窗边。'))
    second = {**first, 'index': 2, 'visual_prompt': '林凡已经坐回桌边。',
              'motion_prompt': '林凡摊开信纸。', 'end_state': '信纸放在桌上。'}
    from novel_manga.story.h3 import stages_of
    prompt = ClipCompiler(compiler_options()).compile_prompt(
        {'shots': [first, second], 'request_seconds': 10}, bible(), ['林凡'], [], '房间')
    second_text = stages_of(prompt)[1][0]
    assert second['visual_prompt'] in second_text
    assert first['end_state'] not in second_text


def test_shorthand_light_is_a_patchable_input_issue_not_guessed_fire_state():
    """A new draft's 同上 goes back to its writer.  An authored sheet's is reported, since a patch
    cannot change the author's column.  And a 同上 that reaches the packer - an old or authored
    draft's - is executed as written: the stage before's light, whole, as those episodes were
    rendered.  Refusing it left ~7,000 existing episodes unable to be repaired or repacked, and no
    keyword guesses which part of the light still holds."""
    from novel_manga.planning.normalization import end_state_and_visual_checks
    from novel_manga.story.scene import SceneContext, resolve_scene
    shot = stage(light='同上')
    errors = []
    end_state_and_visual_checks(shot, [], 'c1 stage 2', PlannerContext(), errors, [])
    assert any(error.field == 'light' for error in errors)
    authored = PlannerContext()
    authored.authored_storyboard = True
    errors, warnings = [], []
    end_state_and_visual_checks(stage(light='同上'), [], 'c1 stage 2', authored, errors, warnings)
    assert not errors and any(w.startswith('report only') and '同上' in w for w in warnings)
    result = resolve_scene({'shots': [
        {'characters': ['林凡'], 'light': '灯光照亮房间，喷口短暂发光', 'wears': {'林凡': '装甲'}},
        {'characters': ['林凡'], 'light': '同上'}]}, SceneContext())
    assert not result.issues
    assert result.shots[1]['light'] == '灯光照亮房间，喷口短暂发光'
    assert result.shots[1]['wears'] == {'林凡': '装甲'}


def test_presence_disagreement_returns_to_writer_without_mutating_the_draft():
    shot = {'label': 'clip_2 stage 1', 'characters': ['林凡'], 'in_frame': ['林凡'],
            'listeners': [], 'actions': [], 'turns': []}
    before = copy.deepcopy(shot)
    issues = presence.issues_for_grades([shot], {1: {'顾宁': 'on_camera'}}, ['林凡', '顾宁'])
    assert shot == before
    assert len(issues) == 1 and issues[0].stage == 'clip_2 stage 1'
    from novel_manga.planning.decisions import patch_targets
    # The existing local patch router must recognize the original model stage.
    assert issues[0].field == 'in_frame'
    assert set(patch_targets(issues)[1]) == {'clip_2 stage 1'}
