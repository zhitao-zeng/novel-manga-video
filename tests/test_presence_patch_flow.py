import json
import sys
import httpx

from novel_manga.application.planning import cli, requests, presence
from novel_manga.application.identity import flow as identity
from novel_manga.planning import validation
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode, PlanningIssue, ValidationResult
from novel_manga.application.packing import posture
from support.posture import unspecified_reply


def test_final_attempt_still_reviews_presence_and_keeps_the_writer_on_disagreement(tmp_path, monkeypatch):
    # The duration floor waiver does not skip the presence review, and the review's disagreement does
    # not fail the chapter: the writer's draft stands and the disagreement goes into the report.
    source = tmp_path / 'novel.txt'
    source.write_text('第一章 庭院\n' + '甲先生来到院内，乙先生在门口等他。\n' * 20)
    bible = tmp_path / 'bible.json'
    bible.write_text(json.dumps({'novel_title': '测试', 'genre': '通用', 'visual_style': '国漫',
        'palette': '青', 'style_fingerprint': 'test', 'characters': [
            {'name': name, 'role': '主角', 'appearance': '黑发', 'wardrobe': '青衣'}
            for name in ['甲先生', '乙先生']], 'locations': ['庭院：空旷院落']}))
    shot = {'label': 'c1 stage 1', 'origin_index': 1, 'clip_hint': 'c1', 'segment_id': 'seg_1',
            'source_quote': '甲先生来到院内，乙先生在门口等他。', 'location': '庭院',
            'characters': ['甲先生'], 'in_frame': ['甲先生'], 'visual_prompt': '甲先生在院内，乙先生在门口。',
            'motion_prompt': '甲先生挥手。', 'end_state': '两人留在原位。', 'camera': '平视', 'light': '日光',
            'shot_scale': '中景', 'actions': [], 'turns': []}
    raw = {'video_title': '庭院', 'hook': '见面', 'summary': '两人见面', 'clips': []}
    monkeypatch.setattr(requests, 'call_model', lambda **kw: (json.dumps(raw), {}))
    monkeypatch.setattr(validation, 'validate_and_normalize', lambda *a, **kw: ValidationResult(
        [PlanningIssue(PlanningCode.DURATION_BELOW_MINIMUM, '未达本集时长下限')], [], [shot]))
    monkeypatch.setattr(identity, 'resolve_chapter', lambda *a, **kw: {})
    calls = []
    def grade(*args, **kwargs):
        calls.append(kwargs['source'])
        return {1: {'乙先生': 'on_camera'}}
    monkeypatch.setattr(presence, 'grade_presence', grade)
    monkeypatch.setattr(posture, 'ask_json', unspecified_reply)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no HTTP')))
    monkeypatch.setattr(sys, 'argv', ['plan_chapter_thin.py', str(source), '--novel-id', 'book',
        '--bible', str(bible), '--output-root', str(tmp_path / 'out'), '--max-redo', '0',
        '--planning-backend', 'local', '--model', 'frozen', '--base-url', 'http://model.invalid/v1'])
    assert cli.main(context=PlannerContext()) == 0
    assert len(calls) == 1
    episode = tmp_path / 'out/book/book_1'
    assert not (episode / 'planning_failed.json').exists()
    report = json.loads((episode / 'chapter_script_report.json').read_text())
    assert any('在场复核' in message for message in report['attempts'][-1]['presence_waived'])
    assert any('在场分歧' in warning for warning in report['warnings'])
    script = json.loads((episode / 'chapter_script.json').read_text())
    assert script['shots'][0]['in_frame'] == ['甲先生']     # the writer's list, not the judge's
