from novel_manga.application.planning import presence, retention
from novel_manga.llm.config import ENDPOINTS
from novel_manga.planning.context import PlannerContext


def test_planning_checks_use_the_configured_judge_instead_of_stopped_local_services(monkeypatch):
    monkeypatch.setitem(ENDPOINTS, 'selected-judge', {
        'QWEN38_LOCAL_BASE_URL': 'http://selected.invalid/v1',
        'QWEN38_LOCAL_MODEL': 'selected-model', 'QWEN38_LOCAL_API_KEY_VAR': 'NO_KEY',
        'QWEN38_LOCAL_STREAM': '0'})
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', 'selected-judge')
    calls = []

    def ask(parts, schema, **kw):
        calls.append(kw)
        return {'verdicts': [{'promise': '主角在院中', 'status': 'delivered', 'reason': '镜头1'}],
                'grades': [{'shot': 1, 'name': '主角', 'grade': 'on_camera'}]}

    monkeypatch.setattr(retention, 'ask_json', ask)
    monkeypatch.setattr(presence, 'ask_json', ask)
    shots = [{'characters': ['主角'], 'in_frame': ['主角'], 'visual_prompt': '主角站在院中',
              'motion_prompt': '主角等待', 'turns': []}]
    assert retention.review('主角在院中', shots, '主角站在院中。')[1] == []
    assert presence.grade_presence(shots, ['主角'], ctx=PlannerContext()) == {1: {'主角': 'on_camera'}}
    assert len(calls) == 2
    assert all(row['settings'].model == 'selected-model' for row in calls)
    assert all(row['settings'].endpoints == ('http://selected.invalid/v1',) for row in calls)
