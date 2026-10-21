"""The retention review is reported, never a reason to rewrite a draft or to fail a chapter.

ch12 part one (2026-09-25): the review sent back 14 of 15 drafts, by old code and new, with Flash-Next or 27B
writing; a shared checklist and shot-level patches did not stop it (every run still used all five drafts); and
the rendered video's defects were none of the kinds it checks.  Its findings go into the report."""
import json
import sys

import httpx

from novel_manga.application.identity import flow as identity
from novel_manga.application.planning import cli, presence, requests, retention
from novel_manga.planning import validation
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode, PlanningIssue, ValidationResult
from novel_manga.application.packing import posture
from support.posture import unspecified_reply

MISSED = '提纲承诺「6. 席勒：好吧，这次免费」未落实：没有台词；按原文和提纲补进观众能听见或看见的表达'


from support.planning_retention import run


def missing_line(section, shots, source_text):
    return ({'section': section, 'verdicts': [{'promise': '6', 'status': 'missing', 'reason': '没有台词'}]},
            [PlanningIssue(PlanningCode.RETAINED_LINE_LOST, MISSED, field='turns')])


def test_the_findings_are_reported_and_the_draft_goes_ahead(tmp_path, monkeypatch):
    code, drafts, report, episode = run(tmp_path, monkeypatch, 0, missing_line)
    assert code == 0 and not (episode / 'planning_failed.json').exists()
    assert report['attempts'][-1]['retention_reported'] == [MISSED]
    assert any(w.startswith('report only (保留内容审查)') and '好吧，这次免费' in w for w in report['warnings'])


def test_an_earlier_draft_is_not_sent_back_either(tmp_path, monkeypatch):
    code, drafts, report, _ = run(tmp_path, monkeypatch, 3, missing_line)
    assert code == 0 and len(drafts) == 1 and len(report['attempts']) == 1


def test_an_incomplete_review_is_retained_without_rewriting_or_claiming_pass(tmp_path, monkeypatch):
    def down(section, shots, source_text):
        raise TimeoutError('reviewer down')
    code, drafts, report, _ = run(tmp_path, monkeypatch, 0, down)
    assert code == 2 and len(drafts) == 1
    assert not report
