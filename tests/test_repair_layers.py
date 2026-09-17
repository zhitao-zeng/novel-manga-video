import ast
import copy
from pathlib import Path

import pytest

from novel_manga.repair.policy import source_decision, diagnosed_decision
from novel_manga.repair.proposal import RepairProposal
from novel_manga.repair.execution import retake_proposal
import novel_manga.application.repair.flow as flow
import novel_manga.application.repair.publication as publication


def test_source_checks_keep_priority_and_repeat_failures_only_reframe_generation():
    request = source_decision(['wrong subject'], {}, True, 'correct owner')
    assert request.action == 'source' and request.diagnosis == {'cause': 'request_mismatch', 'reason': 'correct owner'}
    attribution = source_decision([], {'actor_missing': True}, False, '')
    assert attribution.action == 'source' and attribution.diagnosis['cause'] == 'source_attribution'
    assert source_decision([], {'actor_missing': True}, True, '') is None
    assert diagnosed_decision({'cause': 'generation_mismatch'}).action == 'retake'
    assert diagnosed_decision({'cause': 'generation_mismatch'}, True).action == 'reframe'
    assert diagnosed_decision({'cause': 'source_mismatch'}, True).action == 'source'


def candidate():
    return {'episode': 1, 'clips': 1, 'changed': ['a'], 'proposal': {
        'script': {'shots': []}, 'plan': {'clips': [{'clip_id': 'a', 'prompt': 'new'}]},
        'notes': {}, 'changes': {'a': []}, 'structural_repair': {}}}


def test_proposal_roundtrip_is_detached_from_mutable_inputs():
    result = candidate(); before = copy.deepcopy(result)
    proposal = RepairProposal.from_result(result)
    assert proposal.as_result(True) == result
    proposal.plan['clips'][0]['prompt'] = 'changed candidate'
    assert result == before
    assert 'proposal' not in proposal.as_result()


def test_retake_candidate_does_not_publish_or_change_the_original_plan():
    plan = {'clips': [{'clip_id': 'a', 'repair_take': 2}, {'clip_id': 'b'}]}
    before = copy.deepcopy(plan)
    proposal = retake_proposal(plan, 'a', {'cause': 'generation_mismatch'})
    assert plan == before and proposal.plan['clips'][0]['repair_take'] == 3
    assert proposal.plan['clips'][1] == plan['clips'][1]
    assert proposal.changed == ['a']


@pytest.mark.parametrize('apply,changed,expected', [(False, ['a'], 0), (True, [], 0), (True, ['a'], 1)])
def test_flow_only_publishes_accepted_effective_candidates(tmp_path, monkeypatch, apply, changed, expected):
    raw = candidate(); raw['changed'] = changed; calls = []
    monkeypatch.setattr(flow, '_proposal_data', lambda *a, **k: raw)
    monkeypatch.setattr(publication, 'publish_rewrite', lambda *a, **k: calls.append(a))
    result = flow.repair_episode(tmp_path / 'book', 1, apply, return_proposal=True)
    assert len(calls) == expected and result == raw


def test_publication_preserves_history_before_script_plan_and_notes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(publication.history, 'begin_trial', lambda *a, **k: calls.append('history'))
    monkeypatch.setattr(publication, 'atomic_write_json', lambda p, value: calls.append(p.name))
    publication.publish_candidate(tmp_path, RepairProposal.from_result(candidate()), 'reframe', {'a'})
    assert calls == ['history', 'chapter_script.json', 'clip_plan.json', 'review_feedback.json']


def test_shared_rules_and_workers_do_not_import_operator_scripts():
    root = Path(__file__).resolve().parents[1]
    script_names = {p.stem for p in (root / 'scripts').glob('*.py')}
    for folder in ('story', 'repair'):
        for p in (root / 'src/novel_manga' / folder).glob('*.py'):
            for node in ast.walk(ast.parse(p.read_text())):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else [])
                assert not any(m.split('.')[0] in script_names for m in modules), p
    worker = ast.parse((root / 'src/novel_manga/application/repair/flow.py').read_text())
    assert not any(isinstance(n, ast.ImportFrom) and n.module == 'novel_manga.application.repair.managed' for n in ast.walk(worker))
