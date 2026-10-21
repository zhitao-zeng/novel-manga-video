"""Identity protocol interruptions retain the original candidate and Agent budget."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from novel_manga.application.identity import flow, ledger_store, ledger_resolution, semantic_review
from novel_manga.application.planning import repair
from novel_manga.llm import client
from novel_manga.llm.transport import IncompleteResponse
from novel_manga.util import atomic_write_json, read_json

REAL_BINDINGS = semantic_review.bindings
REAL_BOOK_REVIEW = semantic_review.book


def actor(name='甲', presence='on_stage'):
    return {'source_id': 1, 'name': name, 'kind': 'individual', 'count': 1,
            'presence': presence, 'appearance': '', 'paragraphs': [1],
            'forms': [{'form': name, 'kind': 'proper', 'paragraphs': [1]}]}


@pytest.fixture
def case(tmp_path, monkeypatch, isolated_identity_semantic_service):
    book = tmp_path/'book'
    atomic_write_json(book/'story_bible.json',
                      {'characters': [{'name': '甲', 'role': '人物'}], 'locations': []})
    ledger_store.Ledger(book).save()
    directory = book/'book_1'
    atomic_write_json(directory/'segments.json', [{'text': '甲走进屋里。'}])
    atomic_write_json(directory/'planning_attempt.json', {'candidate': {'shots': []}, 'outline': {}})
    atomic_write_json(directory/'repair_budget_grants.json', {'original': {'limit': 2, 'used': 2}})
    batch = SimpleNamespace(novel_id='book', novel_dir=book, episode_dir=lambda n: directory,
                            rows={1: {'plan': 'failed', 'note': 'identity semantic review unresolved'}},
                            planning_stop=Event(), args=SimpleNamespace(), notes={})
    config = {'runs_root': str(tmp_path/'agents'), 'base_url': 'http://fixture.invalid',
              'model': 'fixture', 'timeout_seconds': 100}
    clock = [0.0]
    monkeypatch.setattr(repair.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(repair, 'model_slot', lambda *a, **k: nullcontext())
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)
    calls, proposals = [], []

    def agent(folder, prompt, **kwargs):
        calls.append(kwargs['timeout'])
        clock[0] += 2
        atomic_write_json(Path(folder)/'output/repair.json', deepcopy(proposals[0]))
        attempt = Path(folder)/f'attempts/{len(calls)}'
        attempt.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(exit_code=0, directory=attempt, produced=['repair.json'])

    def plan(chapter, **kwargs):
        assert 'candidate' in kwargs, 'Repair must not restart the initial author'
        assert read_json(directory/'identity_context.json')['semantic_review']['approved'] is True
        atomic_write_json(directory/'clip_plan.json', {'normal_adoption': True})
        batch.rows[chapter]['plan'] = 'planned'

    batch.plan = plan
    monkeypatch.setattr(repair, 'run_session', agent)
    return directory, batch, config, calls, proposals


@pytest.mark.parametrize('failure_note', ['identity actors lack grounded names',
                                         'identity source actor mapping failed'])
def test_source_correction_resumes_after_binding_timeout_without_another_agent(case, monkeypatch, failure_note):
    directory, batch, config, calls, proposals = case
    bad = actor('假名字')
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        {'source_readable': True, 'source_problem': '', 'actors': [bad]})
    with pytest.raises(ValueError, match='lack grounded names'):
        flow.resolve_chapter(directory)
    batch.rows[1]['note'] = failure_note
    proposals.append({'kind': 'identity_source', 'correction': {'actors': [actor()]}})
    binding_calls = []

    def judge(*args, **kwargs):
        assert kwargs['name'] == 'chapter_binding_review'
        binding_calls.append(1)
        if len(binding_calls) == 1:
            raise TimeoutError('Binding service interrupted after source correction')
        return {'approved': True, 'reason': '原文支持甲', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', judge)
    before = (directory/'repair_budget_grants.json').read_bytes()
    assert repair.repair_chapter(batch, 1, config=config)['status'] == 'service_blocked'
    assert read_json(directory/'planning-repair.json')['proposal'] == proposals[0]
    assert repair.repair_chapter(batch, 1, config=config)['status'] == 'planned'
    record = read_json(directory/'planning-repair.json')
    assert calls == [100] and binding_calls == [1, 1]
    assert record['initial_seconds'] == 100 and record['used_seconds'] == 2
    assert (directory/'repair_budget_grants.json').read_bytes() == before
    assert read_json(directory/'identity_context.json')['source_actors'][0]['name'] == '甲'


def test_replayed_source_proposal_cannot_replace_a_different_saved_correction(case, monkeypatch):
    directory, _, _, _, _ = case
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        {'source_readable': True, 'actors': [actor('假名字')]})
    with pytest.raises(ValueError, match='lack grounded names'):
        flow.resolve_chapter(directory)
    original = read_json(directory/'identity_source_reading.json')
    atomic_write_json(directory/'identity_source_reading.json', {
        **original, 'answer': {**original['answer'], 'actors': [actor(presence='mentioned')]}})
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        pytest.fail('A different saved correction must not be silently overwritten'))
    with pytest.raises(ValueError, match='source proposal is stale'):
        flow.resolve_chapter(directory, source_proposal={
            'inputs': original, 'correction': {'actors': [actor()]}})


BAD_JUDGMENTS = [
    {'approved': 'true', 'reason': '不是有效布尔值', 'correction': None},
    {'approved': True, 'reason': '缺少correction'},
    {'approved': True, 'reason': None, 'correction': None},
    {'approved': True, 'reason': '尚未审查的其他候选', 'correction': {'actors': []}},
    [],
]


@pytest.mark.parametrize('answer', BAD_JUDGMENTS)
def test_incomplete_identity_judgment_never_starts_a_content_agent(case, monkeypatch, answer):
    directory, batch, config, calls, proposals = case
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: deepcopy(answer))
    with pytest.raises(Exception) as caught:
        semantic_review.close({}, {}, {}, name='chapter_binding_review')
    error = caught.value
    batch.rows[1]['note'] = f'{type(error).__name__}: {error}'
    # The saved draft exists, so misclassification would actually invoke the Agent.
    proposals.append({'kind': 'draft', 'draft': {'shots': []}})
    result = repair.repair_chapter(batch, 1, config=config)
    assert result['status'] == 'service_blocked'
    assert isinstance(error, IncompleteResponse)
    assert calls == []
    assert not (directory/'planning-repair.json').exists()


@pytest.mark.parametrize('answer', BAD_JUDGMENTS)
def test_interrupted_identity_proposal_retains_candidate_and_does_not_spend_another_turn(case, monkeypatch, answer):
    directory, batch, config, calls, proposals = case

    def initial(*args, **kwargs):
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'actors': [actor(presence='mentioned')]}
        return {'approved': False, 'reason': '甲实际在场', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', initial)
    with pytest.raises(ValueError, match='identity semantic review unresolved'):
        flow.resolve_chapter(directory)
    proposals.append({'kind': 'identity', 'correction': {'actors': [actor()]}})
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: deepcopy(answer))
    original = (directory/'repair_budget_grants.json').read_bytes()
    assert repair.repair_chapter(batch, 1, config=config)['status'] == 'service_blocked'
    record = read_json(directory/'planning-repair.json')
    assert record['proposal'] == proposals[0] and record['used_seconds'] == 2
    assert not record.get('identity_adopted') and not record.get('feedback')
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        {'approved': True, 'reason': '甲实际在场，修订正确', 'correction': None})
    assert repair.repair_chapter(batch, 1, config=config)['status'] == 'planned'
    record = read_json(directory/'planning-repair.json')
    assert calls == [100]
    assert record['initial_seconds'] == 100 and record['used_seconds'] == 2
    assert (directory/'repair_budget_grants.json').read_bytes() == original


@pytest.mark.parametrize('answer', BAD_JUDGMENTS)
def test_book_identity_protocol_failure_cannot_commit_unreviewed_ledger_data(case, monkeypatch, answer):
    directory, _, _, _, _ = case
    ledger = ledger_store.Ledger(directory.parent)
    raw = {key: [] for key in ('mentions', 'new_entities', 'claims', 'relations')}
    before = {p.name: p.read_bytes() for p in ledger.base.glob('*.json')}

    def resolve_candidate(*args, **kwargs):
        ledger.mentions_by_chapter[1] = [{'form': '甲', 'entity': ledger.entities[0]['id']}]
        return {'mentions': 1}, raw

    monkeypatch.setattr(ledger_resolution, 'resolve_candidate', resolve_candidate)
    monkeypatch.setattr(semantic_review, 'book', REAL_BOOK_REVIEW)
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: deepcopy(answer))
    with pytest.raises(IncompleteResponse):
        ledger_resolution.resolve_chapter(ledger, 1, '甲走进屋里。', raw, workers=1)
    assert 1 not in ledger.mentions_by_chapter
    assert not ledger.mentions_path(1).exists()
    assert not ledger.raw_path(1).exists()
    assert before == {p.name: p.read_bytes() for p in ledger.base.glob('*.json')}
