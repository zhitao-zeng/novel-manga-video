import importlib
import json
import pytest
from types import SimpleNamespace


def merger():
    return importlib.import_module('novel_manga.application.planning.reading_merge')


def person(name):
    return {'name': name, 'name_source': '原文', 'aliases': [], 'tier': '次要', 'gender': '未写明',
        'age': '未写明', 'identity': '', 'appearance': '', 'wardrobe': '', 'first_chapter': 1,
        'chapters': [1], 'evidence': [{'chapter': 1, 'quote': name}], 'unresolved': []}


def candidates():
    return {'forms': [{'form': n, 'kind': {'专名': 1}, 'refers_to': {}, 'listed': [1],
        'on_stage': [1], 'speaks': [], 'gender': {}, 'evidence': [{'chapter': 1, 'quote': n}]}
        for n in ['甲', '乙']], 'locations': {}, 'looks': {}, 'changes': [], 'summaries': {1: '二人进门'}}


def test_group_merge_reuses_completed_jobs_and_keeps_input_changes_visible(tmp_path, monkeypatch):
    module = merger();monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    calls = []
    def ask(parts, schema, **kwargs):
        payload = json.loads(parts[0]['text'].split('\n')[-1])
        calls.append(kwargs['name'])
        return {'characters': [person(payload['assigned_forms'][0]['form'])], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    args = (tmp_path, candidates(), {1: '甲和乙走进门。'})
    first = module.merge_reading(*args, settings=SimpleNamespace(), workers=2)
    assert {p['name'] for p in first['characters']} == {'甲', '乙'}
    assert len(calls) == 2
    assert module.merge_reading(*args, settings=SimpleNamespace(), workers=2) == first
    assert len(calls) == 2
    module.merge_reading(tmp_path, candidates(), {1: '甲和乙走出门。'}, settings=SimpleNamespace(), workers=2)
    assert len(calls) == 4


def test_cross_group_overlap_gets_a_source_backed_merge_not_last_writer_wins(tmp_path, monkeypatch):
    module = merger();monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if 'overlap' in kwargs['name']:
            value = person('甲');value['aliases'] = ['小甲']
            return {'characters': [value], 'look_bindings': []}
        payload = json.loads(parts[0]['text'].split('\n')[-1])
        value = person('甲');value['aliases'] = [payload['assigned_forms'][0]['form']]
        return {'characters': [value], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    data = candidates();data['forms'][1]['form'] = '小甲'
    result = module.merge_reading(tmp_path, data, {1: '甲就是小甲。'}, settings=SimpleNamespace(), workers=2)
    assert len(result['characters']) == 1 and '小甲' in result['characters'][0]['aliases']
    assert len(calls) == 3 and 'overlap' in calls[-1]


def test_merge_restart_keeps_the_original_request_window(tmp_path, monkeypatch):
    module = merger();clock = [1000.0];calls = []
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        raise TimeoutError('interrupted')
    monkeypatch.setattr(module, 'ask_json', ask)
    with pytest.raises(TimeoutError, match='interrupted'):
        module.merge_reading(tmp_path, candidates(), {1: '甲和乙。'}, settings=SimpleNamespace(), workers=1)
    clock[0] += 7201
    with pytest.raises(TimeoutError, match='window exhausted'):
        module.merge_reading(tmp_path, candidates(), {1: '甲和乙。'}, settings=SimpleNamespace(), workers=1)
    assert len(calls) == 1


def test_invalid_model_field_types_are_not_saved_as_completed_merge_jobs(tmp_path, monkeypatch):
    module = merger()
    value = person('甲');value['aliases'] = '错误的字符串列表'
    monkeypatch.setattr(module, 'ask_json', lambda *a, **k: {'characters': [value], 'look_bindings': []})
    with pytest.raises(ValueError, match='aliases'):
        module.merge_reading(tmp_path, candidates(), {1: '甲和乙。'}, settings=SimpleNamespace(), workers=1)
    assert not (tmp_path/'lean/merge/merge_people_0000.json').exists()


def test_unrelated_new_chapter_keeps_finished_group_and_original_deadline(tmp_path, monkeypatch):
    module = merger();monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    clock = [1000.0];monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    calls = []
    def ask(parts, schema, **kwargs):
        payload = json.loads(parts[0]['text'].split('\n')[-1]);calls.append(kwargs['name'])
        return {'characters': [person(payload['assigned_forms'][0]['form'])], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    data = candidates()
    module.merge_reading(tmp_path, data, {1: '甲和乙走进门。'}, settings=SimpleNamespace(), workers=1)
    saved = json.loads((tmp_path/'lean/merge/run.json').read_text())
    added = dict(data['forms'][1], form='丙', listed=[2], on_stage=[2],
                 evidence=[{'chapter': 2, 'quote': '丙'}])
    data['forms'].append(added);clock[0] += 30
    module.merge_reading(tmp_path, data, {1: '甲和乙走进门。', 2: '丙独自在山顶。'},
                         settings=SimpleNamespace(), workers=1)
    assert calls == ['merge_people_0000', 'merge_people_0001', 'merge_people_0002']
    assert json.loads((tmp_path/'lean/merge/run.json').read_text())['deadline'] == saved['deadline']


def test_related_index_retains_ambiguous_evidence_and_look_context(tmp_path, monkeypatch):
    module = merger();monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    data = candidates()
    data['forms'][0]['refers_to'] = {'乙': 2, '远处人': 1}
    data['looks'] = {'甲': [{'chapter': 1, 'text': '白发', 'lasting': '持久',
                           'refers_to': '', 'context': '甲回头看着白发的远处人。'}]}
    data['forms'].extend([dict(data['forms'][1], form=n) for n in ['远处人', '无关者']])
    seen = []
    def ask(parts, schema, **kwargs):
        payload = json.loads(parts[0]['text'].split('\n')[-1]);seen.append(payload)
        return {'characters': [], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    module.merge_reading(tmp_path, data, {1: '甲和乙走进门。'}, settings=SimpleNamespace(), workers=1)
    first = seen[0]
    assert first['global_name_index'] == ['甲', '乙', '远处人', '无关者']
    assert module._cache_inputs({'input': first})['input']['global_name_index'] == ['乙', '甲', '远处人']
    assert first['looks'] == data['looks']
    assert first['assigned_forms'][0]['refers_to'] == {'乙': 2, '远处人': 1}


def test_new_canonical_name_in_old_quote_invalidates_the_affected_group(tmp_path, monkeypatch):
    module = merger();monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    data = candidates();calls = []
    data['forms'][0]['evidence'] = [{'chapter': 1, 'quote': '甲就是小甲'}]
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        return {'characters': [], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    texts = {1: '甲就是小甲。乙独自在远处。'}
    module.merge_reading(tmp_path, data, texts, settings=SimpleNamespace(), workers=1)
    data['forms'].append(dict(data['forms'][0], form='小甲'))
    module.merge_reading(tmp_path, data, texts, settings=SimpleNamespace(), workers=1)
    # Both groups saw 小甲 in their actual excerpt, so both have a changed index.
    assert calls == ['merge_people_0000', 'merge_people_0001',
                     'merge_people_0000', 'merge_people_0001', 'merge_people_0002']


def test_changed_scope_cannot_renew_an_expired_merge_window(tmp_path, monkeypatch):
    module = merger();clock = [1000.0];calls = []
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    def ask(*args, **kwargs):
        calls.append(kwargs['name']);raise TimeoutError('interrupted')
    monkeypatch.setattr(module, 'ask_json', ask)
    with pytest.raises(TimeoutError, match='interrupted'):
        module.merge_reading(tmp_path, candidates(), {1: '甲和乙。'}, settings=SimpleNamespace(), workers=1)
    clock[0] += 7201
    with pytest.raises(TimeoutError, match='window exhausted'):
        module.merge_reading(tmp_path, candidates(), {1: '甲和乙。', 2: '全新的无关资料。'},
                             settings=SimpleNamespace(), workers=1)
    assert len(calls) == 1


def test_frequency_reordering_and_unrelated_append_preserve_original_groups(tmp_path, monkeypatch):
    module = merger();calls = []
    monkeypatch.setattr(module, 'PEOPLE_PER_JOB', 1)
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        return {'characters': [], 'look_bindings': []}
    monkeypatch.setattr(module, 'ask_json', ask)
    data = candidates();texts = {1: '甲与乙。'}
    module.merge_reading(tmp_path, data, texts, settings=SimpleNamespace(), workers=1)
    data['forms'].reverse()
    module.merge_reading(tmp_path, data, texts, settings=SimpleNamespace(), workers=1)
    assert calls == ['merge_people_0000', 'merge_people_0001']
    data['forms'].insert(0, dict(data['forms'][0], form='丙', listed=[2], on_stage=[2],
                               evidence=[{'chapter': 2, 'quote': '丙'}]))
    module.merge_reading(tmp_path, data, {**texts, 2: '丙在远处。'}, settings=SimpleNamespace(), workers=1)
    assert calls[-1] == 'merge_people_0002' and len(calls) == 3
