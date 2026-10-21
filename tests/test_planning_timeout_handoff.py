import sys
import json
import httpx
import pytest

from novel_manga.application.planning import cli, repair
from novel_manga.application.production.flow import failure_line


def test_read_timeout_remains_an_execution_failure_for_the_running_batch(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['plan', 'source.txt', '--novel-id', 'book', '--bible', 'bible.json'])
    def timeout(*a, **k):
        raise httpx.ReadTimeout('timed out')
    monkeypatch.setattr(cli, 'run', timeout)
    assert cli._plan() == 2
    captured = capsys.readouterr()
    note = failure_line((captured.out + captured.err).splitlines())
    assert 'ReadTimeout' in note and repair.service_failure(note)


def test_invalid_saved_outline_reaches_agent_as_its_actual_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['plan', 'source.txt', '--novel-id', 'book', '--bible', 'bible.json'])
    def invalid(*a, **k):
        raise cli.PlanningInputError('invalid candidate outline: missing source coverage')
    monkeypatch.setattr(cli, 'run', invalid)
    with pytest.raises(SystemExit) as stopped:
        cli._plan()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    note = failure_line((captured.out + captured.err).splitlines())
    assert 'missing source coverage' in note and not repair.service_failure(note)


def test_outline_feedback_identifies_wrong_nesting_within_batch_note(monkeypatch, capsys):
    from novel_manga.planning.constants import OUTLINE_SECTIONS
    from novel_manga.planning.contracts import validate_outline
    sections = {key: '保留当前剧情安排' for key in OUTLINE_SECTIONS['coverage']}
    wrong = {**sections, 'segments_covered': ['seg_1']}
    errors = validate_outline(json.dumps(wrong), 'coverage', ['seg_1'])
    monkeypatch.setattr(sys, 'argv', ['plan', 'source.txt', '--novel-id', 'book', '--bible', 'bible.json'])
    def invalid(*a, **k):
        raise cli.PlanningInputError('invalid candidate outline: ' + '; '.join(errors))
    monkeypatch.setattr(cli, 'run', invalid)
    with pytest.raises(SystemExit):
        cli._plan()
    captured = capsys.readouterr()
    note = failure_line((captured.out + captured.err).splitlines())[:240]
    assert 'outline.sections' in note and 'outline.coverage' in note
    corrected = {'sections': sections,
                 'coverage': [{'segment_id': 'seg_1', 'placement': '当前镜头'}]}
    assert validate_outline(json.dumps(corrected), 'coverage', ['seg_1']) == []
    corrected['sections']['clip_allocation'] = ''
    assert validate_outline(json.dumps(corrected), 'coverage', ['seg_1'])
