"""Gate telemetry: events append to one JSONL log and aggregate per gate code."""
from novel_manga.planning.gate_stats import gate_report, record_gate_event


def test_gate_report_aggregates_per_code(tmp_path):
    novel_dir = tmp_path / 'novel'
    novel_dir.mkdir()
    record_gate_event(novel_dir, 1, 'duration_below_minimum', fatal=True)
    record_gate_event(novel_dir, 2, 'duration_below_minimum', fatal=True)
    record_gate_event(novel_dir, 3, 'retained_line_lost', fatal=False)
    report = gate_report(novel_dir)
    assert report['duration_below_minimum'] == {'blocked': 2, 'fatal': 2, 'passed_chapters': 0}
    assert report['retained_line_lost'] == {'blocked': 1, 'fatal': 0, 'passed_chapters': 1}


def test_gate_report_counts_only_passing_chapters(tmp_path):
    novel_dir = tmp_path / 'novel'
    novel_dir.mkdir()
    # Chapter 4 fired the same gate but failed on another one: not a passed chapter.
    record_gate_event(novel_dir, 4, 'retained_line_lost', fatal=False)
    record_gate_event(novel_dir, 4, 'quote_not_source', fatal=True)
    record_gate_event(novel_dir, 5, 'retained_line_lost', fatal=False)
    report = gate_report(novel_dir)
    assert report['retained_line_lost'] == {'blocked': 2, 'fatal': 0, 'passed_chapters': 1}
    assert report['quote_not_source'] == {'blocked': 1, 'fatal': 1, 'passed_chapters': 0}


def test_gate_report_empty_when_no_events(tmp_path):
    assert gate_report(tmp_path) == {}
