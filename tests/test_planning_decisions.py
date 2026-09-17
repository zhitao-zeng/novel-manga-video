import copy
import json
from pathlib import Path
import pytest

from novel_manga.planning.issues import PlanningCode as Code, PlanningIssue as Issue, ValidationResult
from novel_manga.planning.decisions import decide_validation, decide_strict, patch_targets
from planner_decision_cases import planner_traces


def test_complete_retry_requests_patches_reports_and_publication_match_previous_flow():
    expected = json.loads((Path(__file__).parent / 'fixtures/planner_decisions_before.json').read_text())
    assert planner_traces() == expected


def decide(issues, *, final=False, rounds=0, seconds=180, waive=False):
    return decide_validation(ValidationResult(issues, ['existing warning'], []), final_attempt=final,
                             patch_rounds=rounds, patch_seconds_left=seconds, allow_floor_waiver=waive)


@pytest.mark.parametrize('text', ['不同语言的提示', 'unknown location appears inside quoted source', '低于本次要求的下限'])
def test_stage_diagnostic_wording_does_not_change_its_repair_scope(text):
    issue = Issue(Code.QUOTE_NOT_SOURCE, text, stage='clip_1 stage 2', field='source_quote')
    result = decide([issue], final=True, waive=True)
    assert result.action == 'patch' and result.targets == ([], {'clip_1 stage 2': [text]})
    assert not result.floor_waived


def test_clip_problem_and_missing_source_are_routed_by_explicit_metadata():
    issue = Issue(Code.UNKNOWN_LOCATION, 'missing speaker', stage='clip_1 stage 2', field='location')
    assert decide([issue]).action == 'rewrite'
    missing = Issue(Code.UNCITED_SEGMENT, 'localized explanation', segment_id='seg_3')
    local = Issue(Code.VISIBLE_SPEAKER, 'fix speaker', stage='clip_1 stage 2', field='turns')
    assert patch_targets([missing, missing, local]) == (['seg_3'], {'clip_1 stage 2': ['fix speaker']})


def test_floor_waiver_applies_only_at_its_existing_point_in_the_last_attempt():
    short = Issue(Code.DURATION_BELOW_MINIMUM, 'The episode is too short')
    assert decide([short], final=True, waive=True).floor_waived
    assert decide([short], final=True, waive=True).action == 'accept'
    assert decide([short], final=False, waive=True).action == 'rewrite'
    assert decide([short], final=True, waive=False).action == 'rewrite'  # a patch's later shortfall is not waived
    assert not decide([short, Issue(Code.NO_STAGES, 'none')], final=True, waive=True).floor_waived


@pytest.mark.parametrize('rounds,seconds', [(3,180),(0,0)])
def test_exhausted_patch_budget_cannot_create_another_patch(rounds, seconds):
    issue = Issue(Code.VISIBLE_SPEAKER, 'fix speaker', stage='clip_1 stage 1')
    result = decide([issue], rounds=rounds, seconds=seconds)
    assert result.action == 'rewrite' and result.targets is None


def test_strict_waiver_and_decisions_do_not_modify_the_validation_result():
    issue = Issue(Code.STRICT_SPEECH_BELOW_MINIMUM, 'new diagnostic wording')
    before = [issue]
    assert decide_strict(before, final_attempt=False).action == 'rewrite'
    final = decide_strict(before, final_attempt=True)
    assert final.action == 'accept' and final.strict_waived == ['new diagnostic wording']
    result = ValidationResult([Issue(Code.DURATION_BELOW_MINIMUM, 'short')], ['kept'], [{'source_quote':'original'}])
    original = copy.deepcopy(result)
    decide_validation(result, final_attempt=True, patch_rounds=0, patch_seconds_left=180, allow_floor_waiver=True)
    assert result == original
