import copy
import json
import sys

import httpx

from novel_manga.application.planning import cli, requests, presence, posture
from novel_manga.application.identity import flow as identity
from novel_manga.planning import validation
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode, PlanningIssue, ValidationResult


def draft(count=24):
    return {'video_title': '庭院', 'hook': '见面', 'summary': '主角来到庭院', 'clips': [
        {'clip_id': 'c1', 'location': '庭院', 'characters': ['主角'], 'stages': [
            {'segment_id': 's1', 'event': f'原事件{i}', 'turns': []} for i in range(1, count + 1)]}]}


SEGMENTS = [{'segment_id': 's1', 'text': '主角走入庭院，看见院门紧闭。'}]


def response(raw, schema):
    labels = schema['properties']['replacements']['items']['properties']['label']['enum']
    slots = validation.stage_slots(raw)
    return {'insertions': [], 'replacements': [
        {'label': label, 'stage': {**raw['clips'][slots[label][0]]['stages'][slots[label][1]],
                                  'event': '修正' + label}} for label in labels if label != '-']}


def test_large_presence_patch_keeps_other_batches_after_truncation(monkeypatch):
    raw = draft()
    before = copy.deepcopy(raw)
    calls = []

    def ask(parts, schema, **kwargs):
        calls.append(schema)
        assert schema['properties']['replacements']['maxItems'] == 4
        if len(calls) == 3:
            raise ValueError('plan_patch: JSON truncated at 4400 output tokens')
        return response(raw, schema)

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    results = list(requests.patch_batches(raw, [], {f'c1 stage {i}': ['在场分歧'] for i in range(1, 25)},
        SEGMENTS, ['主角'], ['庭院'], timeout=120, total_timeout=180, ctx=PlannerContext()))
    assert len(calls) == 6
    assert results[2]['failed'].startswith('ValueError: plan_patch: JSON truncated')
    last = results[-1]['draft']['clips'][0]['stages']
    assert all(last[i]['event'].startswith('修正') for i in range(24) if i not in range(8, 12))
    assert last[8:12] == before['clips'][0]['stages'][8:12]
    assert raw == before


def test_batched_splits_and_insertions_still_address_original_positions(monkeypatch):
    raw = draft(7)
    fixed = {f'c1 stage {i}': ['需修正'] for i in range(1, 8)}

    def ask(parts, schema, **kwargs):
        reply = response(raw, schema)
        if schema['properties']['insertions']['maxItems']:
            reply['insertions'] = [{'clip_id': 'c1', 'after_stage': 2,
                                    'stage': {'segment_id': 's2', 'event': '补遗漏'}}]
        for item in reply['replacements']:
            if item['label'] == 'c1 stage 1':
                item['continuations'] = [{'segment_id': 's1', 'event': '后续镜头', 'turns': []}]
        return reply

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    results = list(requests.patch_batches(raw, ['s2'], fixed, [*SEGMENTS, {'segment_id': 's2', 'text': '遗漏原文'}],
        ['主角'], ['庭院'], timeout=120, total_timeout=180, ctx=PlannerContext(), split_labels=['c1 stage 1']))
    assert [s['event'] for s in results[-1]['draft']['clips'][0]['stages']] == [
        '修正c1 stage 1', '后续镜头', '修正c1 stage 2', '补遗漏',
        *[f'修正c1 stage {i}' for i in range(3, 8)]]
    assert len(raw['clips'][0]['stages']) == 7


def test_patch_batches_share_time_and_stop_before_another_request(monkeypatch):
    raw = draft(12)
    clock, timeouts = [0.0], []
    monkeypatch.setattr(requests.time, 'monotonic', lambda: clock[0])

    def ask(parts, schema, **kwargs):
        timeouts.append(kwargs['timeout'])
        clock[0] += kwargs['timeout']
        return response(raw, schema)

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    results = list(requests.patch_batches(raw, [], {f'c1 stage {i}': ['需修正'] for i in range(1, 13)},
        SEGMENTS, ['主角'], ['庭院'], timeout=2, total_timeout=3, ctx=PlannerContext()))
    assert timeouts == [2, 1]
    assert results[-1]['failed'] == 'TimeoutError: shared patch time budget exhausted'
    assert results[-2]['draft']['clips'][0]['stages'][8:] == raw['clips'][0]['stages'][8:]


def test_incomplete_batch_does_not_publish_its_partial_replacements(monkeypatch):
    raw = draft(8)
    calls = []

    def ask(parts, schema, **kwargs):
        calls.append(schema)
        reply = response(raw, schema)
        if len(calls) == 1:
            reply['replacements'].pop()
        return reply

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    results = list(requests.patch_batches(raw, [], {f'c1 stage {i}': ['需修正'] for i in range(1, 9)},
        SEGMENTS, ['主角'], ['庭院'], timeout=120, total_timeout=180, ctx=PlannerContext()))
    assert 'exactly its requested' in results[0]['failed']
    assert results[-1]['draft']['clips'][0]['stages'][:4] == raw['clips'][0]['stages'][:4]
    assert all(s['event'].startswith('修正') for s in results[-1]['draft']['clips'][0]['stages'][4:])


def test_planning_flow_checkpoints_completed_batches_and_retries_only_remaining(tmp_path, monkeypatch):
    source = tmp_path / 'novel.txt'
    source.write_text('第一章 庭院\n' + SEGMENTS[0]['text'] * 20)
    bible = tmp_path / 'bible.json'
    bible.write_text(json.dumps({'novel_title': '测试', 'genre': '通用', 'visual_style': '国漫',
        'palette': '青', 'style_fingerprint': 'test', 'characters': [
            {'name': '主角', 'role': '主角', 'appearance': '黑发', 'wardrobe': '青衣'}],
        'locations': ['庭院：空旷院落']}))
    raw = draft(12)
    raw['clips'][0]['stages'] = [{**s, 'source_quote': SEGMENTS[0]['text']} for s in raw['clips'][0]['stages']]
    episode = tmp_path / 'out/book/book_1'
    calls, drafts = [], []

    def call_model(**kwargs):
        drafts.append(kwargs)
        return json.dumps(raw), {}

    def ask(parts, schema, **kwargs):
        calls.append(schema['properties']['replacements']['items']['properties']['label']['enum'])
        if len(calls) == 2:
            saved = json.loads((episode / 'patch_attempt_01_01.json').read_text())
            assert all(s['event'].startswith('修正') for s in saved['clips'][0]['stages'][:4])
            raise ValueError('plan_patch: JSON truncated at 4400 output tokens')
        return response(raw, schema)

    def checked(value, *args, **kwargs):
        stages = value['clips'][0]['stages']
        issues = [PlanningIssue(PlanningCode.PRESENCE_DISAGREEMENT, '在场分歧', stage=f'c1 stage {i}', field='in_frame')
                  for i, s in enumerate(stages, 1) if not s['event'].startswith('修正')]
        shots = [{'label': f'c1 stage {i}', 'origin_index': i, 'clip_hint': 'c1', 'segment_id': 's1',
            'source_quote': SEGMENTS[0]['text'], 'location': '庭院', 'characters': ['主角'],
            'in_frame': ['主角'], 'visual_prompt': '主角站在院内。', 'motion_prompt': s['event'],
            'end_state': '主角留在原位。', 'camera': '平视', 'light': '日光', 'shot_scale': '中景',
            'actions': [], 'turns': []} for i, s in enumerate(stages, 1)]
        return ValidationResult(issues, [], shots)

    monkeypatch.setattr(requests, 'call_model', call_model)
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    monkeypatch.setattr(validation, 'validate_and_normalize', checked)
    monkeypatch.setattr(identity, 'resolve_chapter', lambda *a, **kw: {})
    monkeypatch.setattr(posture, 'check_draft', lambda *a, **kw: (None, []))
    monkeypatch.setattr(presence, 'grade_presence', lambda *a, **kw: {})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no HTTP')))
    monkeypatch.setattr(sys, 'argv', ['plan_chapter_thin.py', str(source), '--novel-id', 'book',
        '--bible', str(bible), '--output-root', str(tmp_path / 'out'), '--max-redo', '0',
        '--planning-backend', 'local', '--model', 'frozen', '--base-url', 'http://model.invalid/v1'])
    assert cli.main(context=PlannerContext()) == 0
    assert len(drafts) == 1
    assert calls == [[f'c1 stage {i}' for i in range(start, start + 4)] for start in (1, 5, 9, 5)]
    first_round = json.loads((episode / 'patch_attempt_01_01.json').read_text())
    assert first_round['clips'][0]['stages'][4:8] == raw['clips'][0]['stages'][4:8]
    assert all(s['event'].startswith('修正') for s in first_round['clips'][0]['stages'][8:])
    report = json.loads((episode / 'chapter_script_report.json').read_text())
    assert report['attempts'][0]['patches'][0]['errors_after'] == 4
    assert report['attempts'][0]['patches'][1]['errors_after'] == 0
    assert not report['attempts'][0].get('presence_waived')
