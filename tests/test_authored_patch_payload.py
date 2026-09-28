"""A binding repair answers only writable fields and keeps the authored performance."""
import copy
import json

from novel_manga.application.planning import requests
from novel_manga.planning import constants, contracts
from novel_manga.planning.binding import AUTHORED_FIELDS
from novel_manga.planning.context import PlannerContext
from novel_manga.story.fields import field_instructions


NAMES = ['席勒', '托尼']
SEGMENTS = [{'segment_id': 'seg_1', 'text': '托尼打了一次响指，一套空甲飞入房间并落地。'}]


def authored_stage(index=9):
    return {
        'segment_id': 'seg_1', 'source_quote': SEGMENTS[0]['text'],
        'start_state': '托尼站在窗边，独立空甲尚未出现。',
        'event': '托尼打一次响指，空甲飞入并落地，他指向空甲。',
        'end_state': '一套无人空甲停在托尼身侧。', 'camera': '固定中景，托尼与窗同框。',
        'light': '日光', 'sfx': '一次响指，金属落地声', 'shot_scale': '中景',
        'turns': [{'speaker_name': '托尼', 'delivery_mode': 'visible_dialogue',
                   'text': '我已经把所有事情解释清楚了，接下来我们一起出发吧。' * 15,
                   'emotion': '平静', 'chat_target': ''}],
        'in_frame': ['托尼'], 'actions': [{'actor': '托尼', 'action': '打响指', 'target': ''}],
        'extras': [], 'props': ['马克2机甲'], 'wears': {'托尼': '马克2机甲'},
        'scene_objects': ['独立无人空甲'], 'duration_seconds': 15,
        'shot_id': str(index), 'purpose': '解释同行方式', 'authored_id': str(index),
        'authored_seconds': 15, 'authored_angle': '平视', 'scene_id': 'scene_01',
    }


def draft(count=2):
    return {'clips': [{'clip_id': 'c1', 'location': '诊室', 'characters': NAMES,
                       'stages': [authored_stage(9 + i) for i in range(count)]}]}


def bound_answer(stage, **changes):
    return {key: value for key, value in {**stage, **changes}.items()
            if key not in AUTHORED_FIELDS}


def test_two_long_authored_lines_are_context_only_and_state_repair_keeps_the_performance(monkeypatch):
    raw = draft()
    before = copy.deepcopy(raw)
    captured = {}

    def ask(parts, schema, **kwargs):
        captured.update(parts=parts, schema=schema, kwargs=kwargs)
        stage_schema = schema['properties']['replacements']['items']['properties']['stage']
        assert not set(AUTHORED_FIELDS) & set(stage_schema['properties'])
        assert not set(AUTHORED_FIELDS) & set(stage_schema['required'])
        assert stage_schema['properties']['wears'] == contracts.build_schema(
            NAMES, ['诊室'], ['seg_1'], ctx=PlannerContext(), prop_names=['马克2机甲']
        )['properties']['clips']['items']['properties']['stages']['items']['properties']['wears']
        return {'insertions': [], 'replacements': [
            {'label': f'c1 stage {i}', 'stage': bound_answer(stage, start_state='托尼坐在窗边。')}
            for i, stage in enumerate(raw['clips'][0]['stages'], 1)]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = requests.patch_plan(raw, [], {'c1 stage 1': ['姿态承接'], 'c1 stage 2': ['姿态承接']},
                                 SEGMENTS, NAMES, ['诊室'], ctx=PlannerContext(authored_storyboard=True))
    prompt = captured['parts'][0]['text']
    assert '作者只读上下文由调用方原样保留' in prompt
    assert 'wears仅填写人物名到穿戴物的关系' in prompt
    blocks = prompt.split('可修改绑定内容：')[1:]
    assert len(blocks) == 2
    for block in blocks:
        current, rest = block.split('\n作者只读上下文（仅供核对，调用方原样保留，不输出）：', 1)
        assert not set(AUTHORED_FIELDS) & set(json.loads(current))
        readonly = json.loads(rest.split('\n\n', 1)[0])
        assert readonly['turns'] == authored_stage()['turns']
        assert readonly['camera'] == authored_stage()['camera']
        assert 'authored_id' not in readonly and 'shot_id' not in readonly
    assert captured['kwargs']['max_tokens'] == 2600
    assert captured['kwargs']['retry_truncated'] is False
    for original, fixed in zip(before['clips'][0]['stages'], result['clips'][0]['stages']):
        assert fixed['start_state'] == '托尼坐在窗边。'
        assert {key: fixed[key] for key in AUTHORED_FIELDS} == {key: original[key] for key in AUTHORED_FIELDS}
        assert fixed['event'].count('响指') == 1 and fixed['event'].count('飞入') == 1
    assert raw == before


def test_optional_fields_are_preserved_when_absent_and_cleared_when_explicit(monkeypatch):
    raw = draft()
    before = copy.deepcopy(raw)
    reply = {'insertions': [], 'replacements': [
        {'label': 'c1 stage 1', 'stage': {'start_state': '托尼坐着。'}},
        {'label': 'c1 stage 2', 'stage': {'in_frame': [], 'actions': [], 'extras': [],
                                         'props': [], 'scene_objects': [], 'wears': {'托尼': None}}},
    ]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', lambda *args, **kwargs: reply)
    result = requests.patch_plan(raw, [], {'c1 stage 1': ['姿态'], 'c1 stage 2': ['绑定']},
                                 SEGMENTS, NAMES, ['诊室'], ctx=PlannerContext(authored_storyboard=True))
    first, cleared = result['clips'][0]['stages']
    for key in ('props', 'wears', 'scene_objects'):
        assert first[key] == before['clips'][0]['stages'][0][key]
    for key in ('in_frame', 'actions', 'extras', 'props', 'scene_objects'):
        assert cleared[key] == []
    assert cleared['wears'] == {'托尼': None}
    assert raw == before
    first['turns'][0]['text'] = '调用方后来修改候选。'
    first['props'].clear()
    assert raw == before


def test_batched_partial_bindings_merge_at_original_slots_and_keep_prior_success(monkeypatch):
    raw = draft(9)
    before = copy.deepcopy(raw)
    calls = []

    def ask(parts, schema, **kwargs):
        labels = schema['properties']['replacements']['items']['properties']['label']['enum']
        calls.append(labels)
        if len(calls) == 2:
            raise ValueError('plan_patch: JSON truncated at 4400 output tokens')
        return {'insertions': [], 'replacements': [
            {'label': label, 'stage': {'start_state': f'修正{label}', 'props': [], 'wears': {'托尼': None}}}
            for label in labels]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    results = list(requests.patch_batches(raw, [], {f'c1 stage {i}': ['姿态'] for i in range(1, 10)},
        SEGMENTS, NAMES, ['诊室'], timeout=120, total_timeout=180, ctx=PlannerContext(authored_storyboard=True)))
    assert len(calls) == 3 and 'truncated' in results[1]['failed']
    final = results[-1]['draft']['clips'][0]['stages']
    for index, stage in enumerate(final, 1):
        if 5 <= index <= 8:
            assert stage == before['clips'][0]['stages'][index - 1]
        else:
            assert stage['start_state'] == f'修正c1 stage {index}'
            assert stage['props'] == [] and stage['wears'] == {'托尼': None}
            assert stage['turns'] == before['clips'][0]['stages'][index - 1]['turns']
    assert raw == before


def test_plain_patch_request_is_the_existing_full_request_and_replaces_whole_stage(monkeypatch):
    raw = {'clips': [{'clip_id': 'c1', 'location': '診室', 'characters': ['席勒'],
                      'stages': [{'segment_id': 'seg_1', 'event': '席勒坐着。', 'turns': []}]}]}
    seen = {}
    fixed = {'segment_id': 'seg_1', 'event': '席勒站起来。'}

    def ask(parts, schema, **kwargs):
        seen.update(parts=parts, schema=schema, kwargs=kwargs)
        return {'insertions': [], 'replacements': [{'label': 'c1 stage 1', 'stage': fixed}]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    ctx = PlannerContext(renderer_moderates=False)
    result = requests.patch_plan(raw, [], {'c1 stage 1': ['姿态']}, SEGMENTS, ['席勒'], ['診室'], ctx=ctx)
    stage_schema = contracts.build_schema(['席勒'], ['診室'], ['seg_1'], ctx=ctx)[
        'properties']['clips']['items']['properties']['stages']['items']
    assert seen['schema'] == {
        'type': 'object', 'additionalProperties': False, 'required': ['insertions', 'replacements'],
        'properties': {
            'insertions': {'type': 'array', 'minItems': 0, 'maxItems': 0, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['clip_id', 'after_stage', 'stage'],
                'properties': {'clip_id': {'type': 'string', 'enum': ['c1']},
                               'after_stage': {'type': 'integer', 'minimum': 0}, 'stage': stage_schema}}},
            'replacements': {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['label', 'stage'],
                'properties': {'label': {'type': 'string', 'enum': ['c1 stage 1']}, 'stage': stage_schema}}},
        },
    }
    expected = [
        '下面是一集短剧的分镜大纲。只输出需要修改的部分，不要改动其他阶段。',
        '另有 1 个阶段没过硬门检查，逐个重写整个阶段（label 原样填回，segment_id 不变），只修错误指出的问题，其余内容尽量保持。',
        field_instructions('planning'),
        '规则：source_quote 从该区段原文逐字复制 8 到 120 字；offscreen_dialogue 和 chat_message 必须写 speaker_name；对白保持原文事实、意图与知识边界，允许等义口语改写；'
        '心理叙述改为本人心声时，用本人视角表达自己的想法，不能保留导致指代变成其他人的第三人称；chat_message仍逐字取自原文；只用给出的人物名，格式和已有阶段一致。',
        '分镜大纲：' + json.dumps([{'clip_id': 'c1', 'location': '診室', 'characters': ['席勒'],
                                 'stages': [{'n': 1, 'segment_id': 'seg_1', 'event': '席勒坐着。'}]}], ensure_ascii=False),
        "可用人物：['席勒']",
        '问题阶段 c1 stage 1\n错误：姿态\n当前内容：' + json.dumps(raw['clips'][0]['stages'][0], ensure_ascii=False),
        '区段 seg_1 原文：\n' + SEGMENTS[0]['text'],
    ]
    assert seen['parts'] == [{'type': 'text', 'text': '\n\n'.join(expected)}]
    assert seen['kwargs'] == {'name': 'plan_patch', 'max_tokens': 1700,
                             'timeout': constants.PATCH_TIMEOUT_SECONDS, 'retry_truncated': False}
    assert result['clips'][0]['stages'] == [fixed]


def test_authored_split_still_returns_full_stages_and_keeps_model_cuts(monkeypatch):
    raw = draft(1)
    before = copy.deepcopy(raw)
    first = {**raw['clips'][0]['stages'][0], 'event': '托尼打响指。'}
    second = {**raw['clips'][0]['stages'][0], 'event': '空甲飞入后落地。', 'turns': []}
    captured = {}

    def ask(parts, schema, **kwargs):
        captured.update(parts=parts, schema=schema)
        return {'insertions': [], 'replacements': [
            {'label': 'c1 stage 1', 'stage': first, 'continuations': [second]}]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = requests.patch_plan(raw, [], {'c1 stage 1': ['拆镜']}, SEGMENTS, NAMES, ['诊室'],
        ctx=PlannerContext(authored_storyboard=True), split_labels=['c1 stage 1'])
    properties = captured['schema']['properties']['replacements']['items']['properties']
    assert {'turns', 'event', 'camera', 'sfx', 'shot_scale'} <= properties['stage']['properties'].keys()
    assert properties['continuations']['items'] == properties['stage']
    assert '当前内容：' in captured['parts'][0]['text']
    assert '作者只读上下文' not in captured['parts'][0]['text']
    assert result['clips'][0]['stages'] == [first, second]
    assert raw == before


def test_authored_binding_repair_receives_actual_adjacent_states_without_guessing(monkeypatch):
    raw = draft(3)
    raw['clips'][0]['stages'][0]['start_state'] = '席勒站在桌边。'
    raw['clips'][0]['stages'][0]['end_state'] = '席勒仍站在桌边。'
    raw['clips'][0]['stages'][1]['start_state'] = '席勒坐在转椅上。'
    raw['clips'][0]['stages'][2]['event'] = '席勒抬手指向报告。'
    before = copy.deepcopy(raw)

    def ask(parts, schema, **kwargs):
        prompt = parts[0]['text']
        assert '同步纠正绑定actions中错误的姿态注记' in prompt
        assert '前镜已经站着时，本镜不能再从椅子上站起' in prompt
        assert '补足一次必要的自然过渡' in prompt
        assert '不把作者明确坐着的后续镜头一律改成站着' in prompt
        context = json.loads(prompt.split('相邻镜头只读衔接', 1)[1].split('：', 1)[1].split('\n\n', 1)[0])
        assert context['前镜']['label'] == 'c1 stage 1'
        assert context['前镜']['end_state'] == '席勒仍站在桌边。'
        assert context['后镜']['label'] == 'c1 stage 3'
        assert context['后镜']['event'] == '席勒抬手指向报告。'
        return {'insertions': [], 'replacements': [{'label': 'c1 stage 2',
                 'stage': {'start_state': '席勒站在桌边。', 'end_state': '席勒保持站姿。'}}]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = requests.patch_plan(raw, [], {'c1 stage 2': ['前镜站，本镜坐，无姿态交接']},
        SEGMENTS, NAMES, ['诊室'], ctx=PlannerContext(authored_storyboard=True))
    assert result['clips'][0]['stages'][1]['start_state'] == '席勒站在桌边。'
    assert result['clips'][0]['stages'][0] == before['clips'][0]['stages'][0]
    assert result['clips'][0]['stages'][2] == before['clips'][0]['stages'][2]
    assert raw == before
