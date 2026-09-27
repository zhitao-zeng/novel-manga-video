import copy
import pytest
from novel_manga.application.planning.voice_splits import split_authored
from novel_manga.planning.storyboard import authored_sound

ROW = {'镜号': 'A1', '景别': '中景', '摄影角度': '侧面', '场景': '门口', '画面内容 / 动作': '甲开门后停下。',
       '机位 / 运镜 / 连续性': '固定', '叙事目的': '藏住想法', '预算秒': 8,
       '台词 / 声音': '甲（说）：“进来吧。”\n甲（内心独白）：“他怎么来了？”\n声音：开门声'}


def reply(prompt, schema):
    if 'problems' in schema['properties']:
        return {'problems': []}
    assert '甲开门后停下' in prompt and '甲（内心独白）' in prompt
    return {'shots': [
        {**{k: ROW[k] for k in ('景别', '摄影角度', '机位 / 运镜 / 连续性', '叙事目的')},
         '画面内容 / 动作': '甲开门，说话后停在门旁。', '预算秒': 4, '音效': '开门声'},
        {**{k: ROW[k] for k in ('景别', '摄影角度', '机位 / 运镜 / 连续性', '叙事目的')},
         '画面内容 / 动作': '门已打开，甲看向来人，嘴自然闭合。', '预算秒': 4, '音效': ''}]}


def test_director_splits_pictures_but_program_keeps_authored_words_and_delivery():
    authored = {'shots': [ROW, {**ROW, '镜号': 'A2', '台词 / 声音': '甲（说）：“坐。”'}]}
    before = copy.deepcopy(authored)
    result, records = split_authored(authored, '甲开门邀请乙，心里意外。', ask=reply)
    assert authored == before and len(result['shots']) == 3 and len(records) == 1
    assert result['shots'][-1] == authored['shots'][-1]
    assert result['shots'][1]['画面内容 / 动作'].startswith('门已打开')
    parsed = [t for s in result['shots'][:2] for t in authored_sound(s['台词 / 声音']).turns]
    assert parsed == list(authored_sound(ROW['台词 / 声音']).turns)
    assert all(s['场景'] == '门口' for s in result['shots'])


def test_bad_directing_is_bounded_and_never_replaces_input():
    calls = []
    def bad(prompt, schema):
        calls.append(1); return {'shots': []}
    with pytest.raises(ValueError, match='心声拆镜未通过'):
        split_authored({'shots': [ROW]}, '原文', ask=bad)
    assert len(calls) == 2


def test_pure_thought_and_plain_voiceover_need_no_model_call():
    rows = [{**ROW, '台词 / 声音': '甲（内心独白）：“他怎么来了？”'},
            {**ROW, '镜号': 'A2', '台词 / 声音': '甲（画外音）：“进来吧。”'}]
    result, records = split_authored({'shots': rows}, '', ask=lambda *a: pytest.fail('unnecessary model call'))
    assert result == {'shots': rows} and not records


def test_long_thought_is_partitioned_without_losing_words():
    from novel_manga.application.planning.voice_splits import groups
    line = '他根本不知道我在想什么。' * 8
    row = {**ROW, '台词 / 声音': '甲（说）：“进来吧。”\n甲（内心独白）：“' + line + '”'}
    parts = groups(row, 15)
    assert len(parts) > 2 and all(p['seconds'] <= 15 for p in parts)
    assert ''.join(t['text'] for p in parts[1:] for t in p['turns']) == line
    assert all(t['inner_monologue'] for p in parts[1:] for t in p['turns'])


def test_a_director_cannot_make_somebody_speak_the_thought_on_camera():
    generated = []
    def ask(prompt, schema):
        if 'problems' in schema['properties']:
            return {'problems': ['第二组是心声，画面却写人物按台词开口。']}
        generated.append(1)
        return reply(prompt, schema)
    with pytest.raises(ValueError, match='拆镜语义检查'):
        split_authored({'shots': [ROW]}, '原文', ask=ask)
    assert len(generated) == 2
