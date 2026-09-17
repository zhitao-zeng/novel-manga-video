import copy
import json
from collections import Counter
from pathlib import Path

import managed_repair_thin as repair
import packing_service_thin as packing
from packing_context_thin import context_for_plan
from test_managed_repair import fixture_episode
from test_split_repair_and_assets import split_episode
from novel_manga.util import atomic_write_json


def test_blocked_scan_reads_evidence_once_and_next_scan_observes_changes(tmp_path, monkeypatch):
    directory, clips, verdicts = fixture_episode(tmp_path)
    review = repair.read(directory / 'episode_review.json', {})
    takes = repair.current_takes(directory, {'clips': clips}, review)
    decisions = {c['clip_id']: {'status': 'blocked', 'reason': 'needs new evidence',
                 'inputs': repair.input_state(directory, c, verdicts[c['clip_id']], takes)} for c in clips}
    atomic_write_json(directory / 'repair_routing.json', decisions)
    counts = Counter(); original = Path.read_text
    def read(path, *args, **kwargs):
        counts[str(path)] += 1
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    assert repair.candidates(directory) == ([], {'a': 'needs new evidence', 'b': 'needs new evidence'})
    for name in ['chapter_script.json', 'segments.json', 'source_speaker_contract.json', 'review_feedback.json', 'repair_budget_grants.json']:
        assert counts[str(directory / name)] == 1, (name, counts)
    assert counts[str(directory.parent / 'story_bible.json')] == 1
    atomic_write_json(directory / 'review_feedback.json', {'a': 'corrected note'})
    assert repair.candidates(directory) == (['a'], {'b': 'needs new evidence'})


def test_packing_reads_body_evidence_once_per_compile_and_never_changes_context(tmp_path, monkeypatch):
    directory, script, saved = split_episode.__wrapped__(tmp_path, monkeypatch)
    context = context_for_plan(directory, directory.parent / 'story_bible.json', saved)
    calls = []
    monkeypatch.setattr(packing, 'bodies_for', lambda *args: calls.append(args) or {})
    before = copy.deepcopy(script)
    expected = packing.compile_plan(script, context)
    assert len(expected[0]['clips']) > 1 and len(calls) == 1
    assert 'body_refs' not in context and script == before
    assert packing.compile_plan(script, context) == expected
    assert len(calls) == 2
