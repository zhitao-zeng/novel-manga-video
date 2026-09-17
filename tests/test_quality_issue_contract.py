import copy
import json
from pathlib import Path
from unittest.mock import patch
import novel_manga.application.profiles as profile
from novel_manga.media.issues import QualityIssue, is_speech_issue, replaced_by_speech_recheck, missing_dialogue


def quality_cases():
    cases=[{}, {'passed':False}, {'passed':True,'issues':[]},
     {'passed':False,'issues':['voice_energy_missing']}, {'passed':False,'issues':['missing_0.7_over_0.5','black_frames']},
     {'passed':False,'issues':['unscripted_speech']}, {'passed':False,'issues':['excess_unplanned_speech','director_instruction_spoken']},
     {'passed':True,'issues':[],'speech_issues':['missing_0.7_over_0.5']},
     {'passed':False,'issues':['unknown_check','voice_energy_missing_extra']},
     {'passed':False,'issues':['unscripted_speech','unscripted_speech'],'speech_issues':['unscripted_speech']}]
    results=[]
    for mode in ['observe','enforce']:
     with patch.object(profile,'speech_gate_policy',lambda *a:mode),patch.object(profile,'load_profile',lambda *a:{'qc_ignore':['existing_override']}):
      results.append({'mode':mode,'results':[profile.speech_gate_result(Path('/no-book'),v) for v in cases],
        'ignored':profile.media_qc_ignores(Path('/no-book'))})
    return results


def test_quality_gates_match_the_frozen_current_worktree():
    expected = json.loads((Path(__file__).parent / 'fixtures/quality_issues_before.json').read_text())
    assert quality_cases() == expected


def test_issue_categories_and_recheck_scope_have_one_definition():
    assert is_speech_issue(QualityIssue.UNSCRIPTED_SPEECH.code)
    assert is_speech_issue(missing_dialogue(0.7, 0.5))
    assert not is_speech_issue(QualityIssue.BLACK_FRAMES.code)
    assert not is_speech_issue('unrecognized_problem')
    assert replaced_by_speech_recheck('voice_energy_missing_suffix')
    assert not is_speech_issue('voice_energy_missing_suffix')  # preserve the narrower gate behavior
    assert not replaced_by_speech_recheck(QualityIssue.UNSCRIPTED_SPEECH.code)


def test_observation_keeps_other_failures_and_does_not_mutate_input():
    from novel_manga.media.issues import apply_speech_policy
    source = {'issues': ['unscripted_speech', 'black_frames'], 'passed': False}
    before = copy.deepcopy(source)
    result = apply_speech_policy(source, observe=True)
    assert result['issues'] == ['black_frames'] and not result['passed']
    assert result['speech_issues'] == ['unscripted_speech'] and source == before
