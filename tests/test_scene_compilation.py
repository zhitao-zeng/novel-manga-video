import copy
from dataclasses import replace

from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.models.bible import Character, StoryBible
from novel_manga.application.profiles import frame_spec


def options(seconds):
    return CompilerOptions(seconds, seconds * .6, 3 if seconds <= 15 else 6, 'execution', 8,
                           chat_screen={'render': 'card'}, frame=frame_spec({'frame': '16:9'}))


def stage():
    return {'index': 1, 'origin_index': 1, 'segment_id': 's1', 'location': '庭院',
            'characters': ['甲'], 'visual_prompt': '甲站在木门前', 'motion_prompt': '甲推开木门',
            'end_state': '木门打开', 'shot_scale': '中景', 'camera': '门外平视', 'light': '左侧日光',
            'turns': [{'speaker_name': '甲', 'delivery_mode': 'visible_dialogue', 'text': '甲缓缓推开木门。' * 9}]}


def test_alternating_clip_limits_do_not_leak_or_mutate_stages():
    short, long = ClipCompiler(options(15)), ClipCompiler(options(30))
    shots = [stage()]; original = copy.deepcopy(shots)
    a = short.pack(shots); b = long.pack(shots)
    assert len(a) > len(b)
    assert all(c['seconds'] <= 15 for c in a)
    assert all(c['seconds'] <= 30 for c in b)
    assert short.pack(shots) == a and long.pack(shots) == b
    assert shots == original
    assert ''.join(t['text'] for c in a for s in c['shots'] for t in s['turns']) == shots[0]['turns'][0]['text']


def test_prompt_compilation_is_repeatable_and_does_not_modify_scene_or_options():
    opts = replace(options(15), genre_rejects=['禁止文字（手机屏幕上剧本指定的聊天消息除外）'])
    compiler = ClipCompiler(opts)
    shot = stage(); shot['turns'][0]['text'] = '阿嚏！'
    clip = {'shots': [shot], 'request_seconds': 5}
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='国漫', palette='青',
                       characters=[Character(name='甲', appearance='黑发', wardrobe='青衣')], locations=['庭院'], style_fingerprint='test')
    before = copy.deepcopy(clip); options_before = copy.deepcopy(opts)
    first = compiler.compile_prompt(clip, bible, ['甲'], [], '庭院')
    assert compiler.compile_prompt(clip, bible, ['甲'], [], '庭院') == first
    assert clip == before and opts == options_before and compiler.options == options_before
    assert '木门' in first and '打喷嚏' in first


def test_reference_cast_uses_the_supplied_book_mentions():
    a = ClipCompiler(replace(options(15), aliases={'掌柜': '甲'}))
    b = ClipCompiler(replace(options(15), aliases={'掌柜': '乙'}))
    assert a.mentioned_characters('掌柜端来茶。', ['甲', '乙']) == []  # existing one-character-name policy
    a = ClipCompiler(replace(options(15), aliases={'掌柜': '甲先生'}))
    b = ClipCompiler(replace(options(15), aliases={'掌柜': '乙先生'}))
    assert a.mentioned_characters('掌柜端来茶。', ['甲先生', '乙先生']) == ['甲先生']
    assert b.mentioned_characters('掌柜端来茶。', ['甲先生', '乙先生']) == ['乙先生']
    assert a.mentioned_characters('掌柜端来茶。', ['甲先生', '乙先生']) == ['甲先生']
