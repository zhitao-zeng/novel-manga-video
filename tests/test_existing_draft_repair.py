from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from novel_manga.application.planning import repair
from novel_manga.util import atomic_write_json, read_json
from tests.test_planning_agent_repair import repair_batch
from tests.test_planning_waste import batch as native_batch


def test_explicit_candidate_is_checked_even_when_old_plan_exists(tmp_path, monkeypatch):
    batch = native_batch(tmp_path, monkeypatch)
    old = batch.episode_dir(1)/'clip_plan.json'
    atomic_write_json(old, {'old': True})
    candidate = tmp_path/'candidate.json'
    atomic_write_json(candidate, {'draft': {'clips': []}})
    calls = []
    batch.run = lambda command, path: calls.append(command) or (1, 'ReviewIncomplete: offline')
    batch.plan(1, candidate=candidate)
    assert batch.rows[1]['plan'] == 'failed'
    assert len(calls) == 1 and '--candidate' in calls[0]
    assert read_json(old) == {'old': True}  # preserve original until replacement decision


@pytest.mark.parametrize('valid', [True, False])
@pytest.mark.parametrize('published', [True, False])
def test_existing_plan_repair_uses_original_budget_and_does_not_accept_stale_file(tmp_path, monkeypatch, valid, published):
    batch, config = repair_batch(tmp_path)
    directory = batch.episode_dir(2)
    if published:
        atomic_write_json(directory/'clip_plan.json', {'old': True})
    context = {'source': 'same'}
    monkeypatch.setattr(repair, 'current_context', lambda *a: context)
    inputs = {'kind': 'draft', 'source_segments': [{'text': '甲拒绝。'}],
              'candidate': {'clips': []}, 'outline': {}, 'failure': 'meaning reversed',
              'planning_input': {'available_characters': ['甲'],
                                 'anonymous_offscreen_speakers': ['小丫头'],
                                 'planning_budget': {'episode_max_seconds': 75}}}
    monkeypatch.setattr(repair, 'failure_inputs', lambda *a: inputs)
    atomic_write_json(directory/'planning-repair.json', {'context': context,
        'inputs': {**inputs, 'kind': 'identity', 'failure': 'old resolved failure'},
        'initial_seconds': 100, 'used_seconds': 73, 'status': 'service_blocked',
        'identity_adopted': True,
        'proposal': {'kind': 'identity', 'correction': {}}})
    clock = [0.0]
    monkeypatch.setattr(repair.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(repair, 'model_slot', lambda *a, **k: nullcontext())
    def agent(folder, prompt, **kwargs):
        assert kwargs['timeout'] == 27
        seed = read_json(folder/'output/repair.json')
        if seed != {'kind': 'draft', 'draft': inputs['candidate'], 'outline': inputs['outline']}:
            pytest.fail('Saved draft was not provided for local edits')
        material = read_json(folder/'input/planning-failure.json')
        assert material['candidate_file'] == 'output/repair.json'
        assert not {'candidate', 'outline', 'planning_input'}.intersection(material)
        if material.get('repair_target') != inputs['failure']:
            pytest.fail('Original material error was lost from repair input')
        if material.get('available_characters') != ['甲'] or material.get('anonymous_offscreen_speakers') != ['小丫头']:
            pytest.fail('Current cast facts were dropped together with historical authoring limits')
        clock[0] += 2
        atomic_write_json(folder/'output/repair.json', {'kind': 'draft', 'draft': {'corrected': True}})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', produced=['repair.json'])
    monkeypatch.setattr(repair, 'run_session', agent)
    def plan(chapter, **kwargs):
        if 'candidate' not in kwargs:
            pytest.fail('Old identity proposal restarted the initial writer')
        assert read_json(kwargs['candidate'])['draft'] == {'corrected': True}
        batch.rows[chapter].update(plan='planned' if valid else 'failed',
                                  note='' if valid else 'ReviewIncomplete: offline')
        if valid:
            atomic_write_json(directory/'clip_plan.json', {'new': True})
    batch.plan = plan
    result = repair.repair_chapter(batch, 2, config=config, existing=published)
    assert result['status'] == ('planned' if valid else 'service_blocked')
    saved = read_json(directory/'planning-repair.json')
    assert saved['initial_seconds'] == 100 and saved['used_seconds'] == 75
    assert (directory/'planning-repair.history.jsonl').exists()


def test_existing_plan_does_not_renew_exhausted_budget(tmp_path, monkeypatch):
    batch, config = repair_batch(tmp_path)
    directory = batch.episode_dir(2)
    atomic_write_json(directory/'clip_plan.json', {'old': True})
    atomic_write_json(directory/'planning-repair.json', {'initial_seconds': 100, 'used_seconds': 100})
    monkeypatch.setattr(repair, 'run_session', lambda *a, **k: pytest.fail('budget renewed'))
    assert repair.repair_chapter(batch, 2, config=config, existing=True)['status'] == 'held'
    assert read_json(directory/'planning-repair.json')['used_seconds'] == 100


@pytest.mark.parametrize('published', [True, False])
def test_existing_identity_repair_revalidates_saved_draft_without_new_author(tmp_path, monkeypatch, published):
    batch, config = repair_batch(tmp_path)
    directory = batch.episode_dir(2)
    if published:
        atomic_write_json(directory/'clip_plan.json', {'old': True})
    atomic_write_json(directory/'planning_attempt.json', {'candidate': {'clips': ['original']}, 'outline': {}})
    inputs = {'kind': 'identity', 'source_segments': [{'text': '甲进屋。'}],
              'identity_inputs': {}, 'failure': 'wrong identity'}
    monkeypatch.setattr(repair, 'current_context', lambda *a: {})
    monkeypatch.setattr(repair, 'failure_inputs', lambda *a: inputs)
    monkeypatch.setattr(repair, 'model_slot', lambda *a, **k: nullcontext())
    from novel_manga.application.identity import flow
    monkeypatch.setattr(flow, 'resolve_chapter', lambda *a, **k: {})
    def agent(folder, prompt, **kwargs):
        atomic_write_json(folder/'output/repair.json', {'kind': 'identity', 'correction': {}})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', produced=['repair.json'])
    monkeypatch.setattr(repair, 'run_session', agent)
    calls = []
    def plan(chapter, **kwargs):
        calls.append(kwargs)
        if 'candidate' not in kwargs:
            pytest.fail('Existing draft must not restart the writer')
        assert read_json(kwargs['candidate'])['draft'] == {'clips': ['original']}
        batch.rows[chapter]['plan'] = 'planned'
        atomic_write_json(directory/'clip_plan.json', {'new': True})
    batch.plan = plan
    result = repair.repair_chapter(batch, 2, config=config, existing=published)
    assert result['status'] == 'planned' and len(calls) == 1


def test_confirmed_identity_defect_uses_normal_proposal_review_without_faking_old_verdict(tmp_path):
    batch, _ = repair_batch(tmp_path)
    path = batch.episode_dir(2)/'identity_binding_review.json'
    approved = {'approved': True, 'inputs': {'context': {}, 'candidate': {'actors': [], 'bindings': []}}}
    atomic_write_json(path, approved)
    assert repair.failure_inputs(batch, 2)['kind'] == 'draft'
    batch.rows[2]['repair_stage'] = 'identity'
    assert repair.failure_inputs(batch, 2)['kind'] == 'identity'
    assert read_json(path) == approved
