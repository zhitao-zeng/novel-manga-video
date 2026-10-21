"""Synthetic planner fixture for retention and gate lifecycle regressions."""
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


def run(tmp_path, monkeypatch, max_redo, review, *, validation_issues=()):
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
    monkeypatch.setattr(validation, 'validate_and_normalize', lambda *a, **kw: ValidationResult(list(validation_issues), [], [dict(shot)]))
    monkeypatch.setattr(identity, 'resolve_chapter', lambda *a, **kw: {})
    monkeypatch.setattr(presence, 'grade_presence', lambda *a, **kw: {})
    monkeypatch.setattr(retention, 'section_of', lambda outline: '6. 席勒：好吧，这次免费')
    monkeypatch.setattr(presence, 'review_visibility', lambda shots, everyone, **kw: {
        'grades': {str(i): {name: 'on_camera' for p,name,_ in presence.candidates_for(shots,everyone,ctx=kw.get('ctx')) if p==i}
                   for i in {p for p,_,_ in presence.candidates_for(shots,everyone,ctx=kw.get('ctx'))}}, 'marks': []})
    monkeypatch.setattr(retention, 'review', lambda section, shots, source, **kw: review(section,shots,source))
    monkeypatch.setattr(posture, 'ask_json', unspecified_reply)
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
