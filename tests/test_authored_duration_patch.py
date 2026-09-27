import copy
import pytest

from novel_manga.application.planning.requests import patch_plan
from novel_manga.planning.budget import validate_duration
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.decisions import patch_targets
from novel_manga.planning.issues import PlanningCode
from novel_manga.story.compilation import ClipCompiler, CompilerOptions


def test_long_stage_is_a_local_writer_issue_and_cannot_be_silently_packed():
    shot = {'label': 'c1 stage 1', 'index': 1, 'origin_index': 1, 'clip_hint': 'c1',
            'location': '窗边', 'characters': ['甲'], 'visual_prompt': '甲站在窗边。',
            'motion_prompt': '甲说完后飞出窗户。', 'end_state': '屋里已没有甲。',
            'turns': [{'delivery_mode': 'visible_dialogue', 'speaker_name': '甲', 'text': '我把事情说完再走。' * 12}],
            'duration_seconds': 20}  # a planned length: no line cut can shorten it, so it is the writer's
    ctx = PlannerContext(); ctx.max_clip_seconds = 15
    errors = []; validate_duration([shot], ctx, errors, [])
    issues = [e for e in errors if e.code == PlanningCode.STAGE_ABOVE_MAXIMUM]
    assert len(issues) == 1 and 'c1 stage 1' in patch_targets(issues)[1]
    before = copy.deepcopy(shot)
    with pytest.raises(ValueError, match='规划补丁明确拆镜'):
        ClipCompiler(CompilerOptions(15, 9, 3, 'execution', 8)).pack([shot])
    assert shot == before


def test_model_supplied_split_does_not_shift_other_replacements_or_insertions(monkeypatch):
    from novel_manga.llm import client
    from novel_manga.application.planning import requests
    stages = [{'segment_id': 's1', 'event': f'事件{i}', 'turns': []} for i in range(1, 4)]
    raw = {'clips': [{'clip_id': 'c1', 'location': '屋内', 'characters': ['甲'], 'stages': stages}]}
    before = copy.deepcopy(raw)
    first = {'segment_id': 's1', 'event': '甲解释原因。', 'start_state': '甲站在窗边。', 'end_state': '甲仍在窗边。'}
    last = {'segment_id': 's1', 'event': '甲告别并飞离。', 'start_state': '甲仍在窗边。', 'end_state': '窗边已无人。'}
    fixed_third = {'segment_id': 's1', 'event': '第三镜修正'}
    inserted = {'segment_id': 's2', 'event': '补上缺漏的事件'}
    def ask(parts, schema, **kwargs):
        assert 'continuations' in schema['properties']['replacements']['items']['properties']
        assert '不重复进入、推门或飞离' in parts[0]['text']
        return {'replacements': [{'label': 'c1 stage 1', 'stage': first, 'continuations': [last]},
                                 {'label': 'c1 stage 3', 'stage': fixed_third}],
                'insertions': [{'clip_id': 'c1', 'after_stage': 2, 'stage': inserted}]}
    monkeypatch.setattr(client, 'ask_json', ask)
    ctx = PlannerContext(); ctx.max_clip_seconds = 15
    result = patch_plan(raw, ['s2'], {'c1 stage 1': ['超长'], 'c1 stage 3': ['机位错误']},
                        [{'segment_id': 's1', 'text': '原文'}, {'segment_id': 's2', 'text': '漏掉的原文'}],
                        ['甲'], ['屋内'], ctx=ctx, split_labels=['c1 stage 1'])
    assert result['clips'][0]['stages'] == [first, last, stages[1], inserted, fixed_third]
    assert raw == before


def test_voice_patch_cannot_erase_thought_marker_to_avoid_splitting(monkeypatch):
    from novel_manga.llm import client
    turn = {'speaker_name': '甲', 'delivery_mode': 'offscreen_dialogue', 'text': '我知道了。', 'inner_monologue': True}
    stage = {'segment_id': 's1', 'event': '甲思考', 'turns': [turn]}
    raw = {'clips': [{'clip_id': 'c1', 'location': '屋内', 'characters': ['甲'], 'stages': [stage]}]}
    def ask(*args, **kwargs):
        changed = {**stage, 'turns': [{**turn, 'inner_monologue': False}]}
        return {'insertions': [], 'replacements': [{'label': 'c1 stage 1', 'stage': changed}]}
    monkeypatch.setattr(client, 'ask_json', ask)
    with pytest.raises(ValueError, match='inner_monologue'):
        patch_plan(raw, [], {'c1 stage 1': ['心声拆镜']}, [{'segment_id': 's1', 'text': '原文'}],
                   ['甲'], ['屋内'], ctx=PlannerContext(), split_labels=['c1 stage 1'])
