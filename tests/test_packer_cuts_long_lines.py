"""A long stage is cut by the packer between its lines, and nothing is written for the new parts.

ch12 part one (2026-09-25): handed to the writer, the cut failed all 17 drafts of three runs - the
90-character speech stayed whole, or came back rewritten or repeated, or another beat fell out.  The
program's old cut passed at the first draft but replaced the later parts' picture with invented prose."""
import pytest

from novel_manga.models.bible import Character, StoryBible
from novel_manga.planning import constants as pc_constants, prompts as pc_prompts, text as pc_text
from novel_manga.planning.budget import validate_duration
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode
from novel_manga.planning.normalization import normalize_turns
from novel_manga.story.compilation import ClipCompiler, CompilerOptions

SPEECH = ('我承认这里环境的确不好，毕竟我不像你，是个亿万富翁，不过话说回来，当年霍华德先生应该也是在这样的一个'
          '破旧屋子里白手起家，这恐怕的确是你无法体会的艰辛，所以你被绑架之后才会让佩珀小姐担心了那么久……')


def context(seconds=15):
    ctx = PlannerContext()
    ctx.max_clip_seconds = seconds
    return ctx


def compiler():
    from novel_manga.application.profiles import frame_spec
    return ClipCompiler(CompilerOptions(15, 9, 3, 'execution', 8, frame=frame_spec({'frame': '16:9'})))


def long_stage():
    turns = [{'delivery_mode': 'visible_dialogue', 'speaker_name': '席勒', 'text': piece, 'emotion': '平稳'}
             for piece in pc_text.split_turn_text(SPEECH)]
    return {'label': 'clip_2 stage 2', 'index': 4, 'origin_index': 4, 'clip_hint': 'clip_2', 'location': '诊所',
            'segment_id': 'seg_3', 'shot_scale': '近景', 'characters': ['席勒', '托尼·斯塔克'],
            'in_frame': ['席勒', '托尼·斯塔克'], 'listeners': ['托尼·斯塔克'], 'extras': [], 'actions': [],
            'visual_prompt': '席勒身体前倾，手指点桌面，托尼眉头紧锁。', 'motion_prompt': '席勒靠回椅背。',
            'end_state': '席勒靠回椅背，托尼站在原地，眼神变得危险。', 'camera': '从托尼的视角平视', 'light': '窗外路灯的冷光',
            'sfx': '', 'avoid': '', 'turns': turns}


def test_a_long_speech_is_the_packers_cut_not_the_writers_issue():
    errors, warnings = [], []
    validate_duration([long_stage()], context(), errors, warnings)
    assert not [e for e in errors if e.code == PlanningCode.STAGE_ABOVE_MAXIMUM]
    assert any('打包时在台词之间切成' in w for w in warnings)


def test_the_packer_cuts_between_lines_and_writes_nothing_for_the_new_parts():
    stage = long_stage()
    parts = compiler().split_long_shot(stage)
    assert len(parts) > 1
    assert [t['text'] for p in parts for t in p['turns']] == [t['text'] for t in stage['turns']]
    *early, last = parts
    for part in early:
        assert part['visual_prompt'] == stage['visual_prompt'] and part['end_state'] == ''
        assert part['motion_prompt'] == '' and part['actions'] == []
    assert (last['visual_prompt'], last['motion_prompt'], last['end_state']) == \
        (stage['visual_prompt'], stage['motion_prompt'], stage['end_state'])
    assert all(part['in_frame'] == stage['in_frame'] for part in parts)
    written = ' '.join(str(p[k]) for p in parts for k in ('visual_prompt', 'motion_prompt', 'end_state'))
    assert '承接上一段' not in written and '正在发生' not in written
    bible = StoryBible(novel_title='t', genre='g', visual_style='2d', palette='c', style_fingerprint='f',
                       characters=[Character(name='席勒', role='主角', appearance='a', wardrobe='w'),
                                   Character(name='托尼·斯塔克', role='配角', appearance='b', wardrobe='x')],
                       locations=['诊所：房间'])
    clip = {'clip_id': 'clip_03', 'kind': 'video', 'request_seconds': 15, 'references': [], 'lines': [], 'shots': [early[0]]}
    prompt = compiler().compile_prompt(clip, bible, ['席勒', '托尼·斯塔克'], [], '诊所')
    stage_line = next(line for line in prompt.splitlines() if '【阶段' in line)
    assert '主要事件' not in stage_line and '从“”' not in prompt
    # no end of its own: the opening is not held as the part's end (ch12 clip_12, 2026-09-25)
    assert '结束时' not in stage_line and '到“”' not in prompt
    assert stage['visual_prompt'].rstrip('。') in stage_line


def test_the_packer_uses_the_cut_validation_allowed():
    clips = compiler().pack([long_stage()])
    assert len(clips) > 1 and all(clip['seconds'] <= 15 for clip in clips)


def test_a_length_no_line_can_cut_still_goes_back_to_the_writer():
    stage = {**long_stage(), 'duration_seconds': 20}
    errors = []
    validate_duration([stage], context(), errors, [])
    assert [e for e in errors if e.code == PlanningCode.STAGE_ABOVE_MAXIMUM]
    with pytest.raises(ValueError, match='切不到上限以内'):
        compiler().pack([stage])


def test_the_brief_says_the_packer_cuts_long_lines():
    brief = pc_prompts.render_brief(pc_constants.DEFAULT_SYSTEM_PROMPT, ctx=context())
    assert '超长台词交给后续打包在句读处切到相邻片段' in brief and '由编剧明确拆成多个合规阶段' not in brief


def test_a_line_with_nothing_to_say_is_not_a_line():
    shot = {'turns': [
        {'speaker_name': '席勒', 'delivery_mode': 'visible_dialogue', 'text': '（无对白，动作描述）'},
        {'speaker_name': '席勒', 'delivery_mode': 'visible_dialogue', 'text': '……'},
        {'speaker_name': '席勒', 'delivery_mode': 'visible_dialogue', 'text': '嗯……好吧。'}]}
    errors, warnings = [], []
    turns, visible = normalize_turns(shot, ['席勒'], ['席勒'], 'c1 stage 2', PlannerContext(), errors, warnings)
    assert [t['text'] for t in turns] == ['嗯……好吧。'] and not errors
    assert sum('没有可说出口的字' in w for w in warnings) == 2
