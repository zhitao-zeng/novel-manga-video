"""Keep participation ranges distinct from literal form locations; simulated owner replies only."""
from copy import deepcopy
import json

import pytest

from novel_manga.application.identity import flow, semantic_review
from novel_manga.llm import client
from novel_manga.util import atomic_write_json


REAL_BINDINGS = semantic_review.bindings
SOURCE_RULE = ('actors.paragraphs记录有原文依据的指称与参与范围，包含已确定成员的群体指称、连续同行及离场段；'
               'forms.paragraphs只记录该名字或称谓逐字出现处，不为补参与范围扩大姓名引用。'
               '原文明示成员才承接群体范围，不凭背景名单补姓名，不把仅提及或已离场者扩成全章在场。')
REVIEW_RULE = ('分别核对actors和bindings的paragraphs指称与参与范围与forms的paragraphs字面位置。'
               '原文已能确定的群体成员、连续同行范围若遗漏，应在correction中补齐参与段落，不能只在reason叙述。'
               '原文群体称谓可作为成员的局部contextual指称，不能因此合并成员编号；姓名未出现的段落不得补进姓名forms。')


def actor(sid, name, paragraphs, name_paragraphs, *, presence='on_stage', group_forms=False):
    forms = [{'form': name, 'kind': 'proper', 'paragraphs': name_paragraphs}]
    if group_forms:
        forms.extend([{'form': '三人', 'kind': 'contextual', 'paragraphs': [1]},
                      {'form': '几人', 'kind': 'contextual', 'paragraphs': [3]}])
    return {'source_id': sid, 'name': name, 'kind': 'individual', 'count': 1,
            'presence': presence, 'appearance': '', 'paragraphs': paragraphs, 'forms': forms}


@pytest.mark.parametrize('source_already_complete', [False, True])
def test_existing_fields_keep_confirmed_group_ranges_without_fake_name_quotes(
        tmp_path, monkeypatch, source_already_complete):
    book = tmp_path / 'book'
    episode = book / 'book_67'
    originals = {
        'story_bible.json': {'characters': [{'name': n, 'appearance': '原设计'+n} for n in ['甲', '乙', '丙', '丁']]},
        'entity/entities.json': [
            *[{'id': f'e{71+i:03d}', 'canonical': n, 'asset_id': f'character_{23+i:03d}'} for i, n in enumerate(['甲', '乙', '丙', '丁'])],
            {'id': 'e099', 'canonical': '旧甲', 'merged_into': 'e071'}],
        'entity/claims.json': [{'type': 'same_as', 'subject': 'e099', 'object': 'e071', 'status': 'accepted', 'chapter': 1}],
        'entity/corrections.json': [{'subject': 'e099', 'object': 'e071', 'decision': 'same'}],
        'entity/merges.json': [{'from': 'e099', 'into': 'e071'}],
        'reading_cast.json': {'characters': ['甲', '乙', '丙', '丁'], 'aliases': {}},
        'series_assets/phases.json': {'characters': {'甲': {'asset_id': 'original_phase'}}},
        'series_assets/manifest.json': {'characters': {'甲': {'asset_id': 'character_023'}}},
    }
    for rel, value in originals.items():
        atomic_write_json(book / rel, value)
    segments = [{'segment_id': f's{i}', 'text': text} for i, text in enumerate([
        '三人来到门口。', '甲招呼乙和丙一起走。甲提起丁，丁在别处。',
        '几人走进药店。', '乙和丙出了门，甲留在屋内。', '甲独自在屋里说话。'], 1)]
    atomic_write_json(episode / 'segments.json', segments)
    protected = {p: p.read_bytes() for p in [*[book / rel for rel in originals], episode / 'segments.json']}
    complete = [actor(1, '甲', [1, 2, 3, 4, 5], [2, 4, 5], group_forms=True),
                actor(2, '乙', [1, 2, 3, 4], [2, 4], group_forms=True),
                actor(3, '丙', [1, 2, 3, 4], [2, 4], group_forms=True),
                actor(4, '丁', [2], [2], presence='mentioned')]
    initial = deepcopy(complete)
    if not source_already_complete:
        initial[1] = actor(2, '乙', [2, 4], [2, 4])
        initial[2] = actor(3, '丙', [2, 4], [2, 4])
    before = deepcopy(initial)
    calls = []

    def ask(parts, *args, **kwargs):
        name = kwargs['name']; calls.append(name); prompt = parts[0]['text']
        if name == 'chapter_identity_source':
            assert SOURCE_RULE in prompt
            return {'source_readable': True, 'source_problem': '', 'actors': deepcopy(initial)}
        assert name == 'chapter_binding_review' and REVIEW_RULE in prompt
        payload = json.loads(prompt.split('\n', 1)[1])
        if not source_already_complete and calls.count(name) == 1:
            assert payload['candidate']['actors'] == initial
            return {'approved': False, 'reason': '补回原文明示同行成员的参与范围', 'correction': {
                'actors': deepcopy(complete[1:3]),
                'bindings': [{'source_id': sid, 'entity_id': eid, 'entity_kind': None, 'decision': 'catalogue',
                              'paragraphs': [1, 2, 3, 4], 'reason': '明确同行，随后离场'}
                             for sid, eid in [(2, 'e072'), (3, 'e073')]]}}
        assert payload['candidate']['actors'] == complete
        return {'approved': True, 'reason': '本地接口测试回复', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)
    result = flow.resolve_chapter(episode)
    assert calls == ['chapter_identity_source'] + ['chapter_binding_review'] * (1 if source_already_complete else 2)
    assert initial == before and result['source_actors'] == complete
    assert {m['source_actor']: m['entity_id'] for m in result['mentions']} == {1: 'e071', 2: 'e072', 3: 'e073', 4: 'e074'}
    for sid, name in [(2, '乙'), (3, '丙')]:
        assert next(a for a in result['source_actors'] if a['source_id'] == sid)['paragraphs'] == [1, 2, 3, 4]
        assert next(m for m in result['mentions'] if m['source_actor'] == sid and m['form'] == name)['paragraphs'] == [2, 4]
        assert {m['form'] for m in result['mentions'] if m['source_actor'] == sid} == {name, '三人', '几人'}
    assert all(m['presence'] == 'mentioned' for m in result['mentions'] if m['source_actor'] == 4)
    assert result['semantic_new_names'] == result['semantic_extras'] == []
    assert protected == {p: p.read_bytes() for p in protected}
    saved = (episode / 'identity_context.json').read_bytes()
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: pytest.fail('An unchanged adopted chapter was reopened'))
    assert flow.resolve_chapter(episode) == result
    assert (episode / 'identity_context.json').read_bytes() == saved
    assert protected == {p: p.read_bytes() for p in protected}
