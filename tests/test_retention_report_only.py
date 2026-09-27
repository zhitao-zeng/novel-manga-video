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

MISSED = '提纲承诺「6. 席勒：好吧，这次免费」未落实：没有台词；按原文和提纲补进观众能听见或看见的表达'


def run(tmp_path, monkeypatch, max_redo, review):
    source = tmp_path / 'novel.txt'
    source.write_text('第一章 庭院\n' + '甲先生来到院内，乙先生在门口等他。\n' * 20)
    bible = tmp_path / 'bible.json'
    bible.write_text(json.dumps({'novel_title': '测试', 'genre': '通用', 'visual_style': '国漫',
        'palette': '青', 'style_fingerprint': 'test', 'characters': [
            {'name': name, 'role': '主角', 'appearance': '黑发', 'wardrobe': '青衣'}
            for name in ['甲先生', '乙先生']], 'locations': ['庭院：空旷院落']}))
    shot = {'label': 'c1 stage 1', 'origin_index': 1, 'clip_hint': 'c1', 'segment_id': 'seg_1',
            'source_quote': '甲先生来到院内，乙先生在门口等他。', 'location': '庭院',
            'characters': ['甲先生'], 'in_frame': ['甲先生'], 'visual_prompt': '甲先生在院内。',
            'motion_prompt': '甲先生挥手。', 'end_state': '甲先生留在原位。', 'camera': '平视', 'light': '日光',
            'shot_scale': '中景', 'actions': [], 'turns': []}
    raw = {'video_title': '庭院', 'hook': '见面', 'summary': '两人见面', 'clips': []}
    drafts = []
    monkeypatch.setattr(requests, 'call_model', lambda **kw: (drafts.append(kw), (json.dumps(raw), {}))[1])
    monkeypatch.setattr(validation, 'validate_and_normalize', lambda *a, **kw: ValidationResult([], [], [dict(shot)]))
    monkeypatch.setattr(identity, 'resolve_chapter', lambda *a, **kw: {})
    monkeypatch.setattr(presence, 'grade_presence', lambda *a, **kw: {})
    monkeypatch.setattr(retention, 'section_of', lambda outline: '6. 席勒：好吧，这次免费')
    monkeypatch.setattr(retention, 'review', review)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no HTTP')))
    monkeypatch.setattr(sys, 'argv', ['plan_chapter_thin.py', str(source), '--novel-id', 'book',
        '--bible', str(bible), '--output-root', str(tmp_path / 'out'), '--max-redo', str(max_redo),
        '--planning-backend', 'local', '--model', 'frozen', '--base-url', 'http://model.invalid/v1'])
    code = cli.main(context=PlannerContext())
    episode = tmp_path / 'out/book/book_1'
    report = json.loads((episode / 'chapter_script_report.json').read_text()) if code == 0 else {}
    return code, drafts, report, episode


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


def test_a_review_that_fails_is_reported_and_gates_nothing(tmp_path, monkeypatch):
    def down(section, shots, source_text):
        raise TimeoutError('reviewer down')
    code, drafts, report, _ = run(tmp_path, monkeypatch, 0, down)
    assert code == 0 and 'reviewer down' in report['attempts'][-1]['retention_unreviewed']
    assert any('保留内容审查未完成' in w for w in report['warnings'])
