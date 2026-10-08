"""An overlong draft is edited and checked without paying for another outline."""
from support.planner_decision_cases import planner_traces


def test_overlong_draft_uses_one_compression_and_keeps_the_original_creation_call():
    result = planner_traces(['compression'])['compression']
    assert result['exit'] == 0
    assert len(result['requests']) == 1
    assert len(result['compressions']) == 1
    assert result['compressions'][0]['video_title'] == '庭院'
    assert result['reports']['chapter_script_report.json']['attempts'][0]['compressions'][0]['errors_after'] == []
    assert 'compression_attempt_01_01.before.json' in result['compression_evidence']


def test_still_overlong_after_bounded_compression_fails_without_whole_chapter_regeneration():
    result = planner_traces(['compression_failed'])['compression_failed']
    assert result['exit'] == 2
    assert len(result['requests']) == 1
    assert len(result['compressions']) == 2
    assert 'chapter_script.json' not in result['reports']
    failure = result['reports']['planning_failed.json']
    assert '超过120秒' in failure['errors'][0]
    assert len(failure['attempts'][0]['compressions']) == 2
