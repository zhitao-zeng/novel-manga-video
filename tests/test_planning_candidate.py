"""A repaired draft is new input, not an offline replay of old review evidence."""
import json
import sys
import pytest

from novel_manga.application.planning import cli
from novel_manga.util import atomic_write_json
from support.planning_retention import run
from novel_manga.planning.constants import OUTLINE_SECTIONS
from novel_manga.planning.issues import PlanningCode, PlanningIssue


def outline():
    return {'sections': {name: '原文中的见面事件和对白。' for name in OUTLINE_SECTIONS['coverage']},
            'coverage': [{'segment_id': 'seg_1', 'placement': '庭院见面'}]}


def test_repair_candidate_uses_fresh_checks_without_buying_another_initial_writer(tmp_path, monkeypatch):
    original_path = tmp_path/'out/book/book_1/response_attempt_01.raw.json'
    atomic_write_json(original_path, {'original_failed_draft': True})
    original = original_path.read_bytes()
    packet = tmp_path/'candidate.json'
    atomic_write_json(packet, {'draft': {'video_title': '修订后的庭院', 'hook': '见面',
        'summary': '两人见面', 'clips': []}, 'outline': outline()})
    main = cli.main
    from novel_manga.application.planning import flow
    original_attempts = flow.plan_attempts
    def use_current_source(args, ctx, inputs):
        value = json.loads(packet.read_text())
        value['outline']['coverage'] = [{'segment_id': s['segment_id'], 'placement': '见面事件'}
                                        for s in inputs.payload['segments']]
        atomic_write_json(packet, value)
        return original_attempts(args, ctx, inputs)
    monkeypatch.setattr(flow, 'plan_attempts', use_current_source)
    def candidate_main(**kwargs):
        sys.argv.extend(['--candidate', str(packet)])
        return main(**kwargs)
    monkeypatch.setattr(cli, 'main', candidate_main)
    reviews = []
    def review(section, shots, source):
        reviews.append(section)
        return {'section': section, 'verdicts': []}, []
    code, initial_writer_calls, report, episode = run(tmp_path, monkeypatch, 4, review)
    assert code == 0 and initial_writer_calls == []
    assert len(reviews) == 1
    assert report['attempts'][0]['candidate_from'] == str(packet)
    assert json.loads((episode/'chapter_script.json').read_text())['video_title'] == '修订后的庭院'
    archives = list((episode/'planning-history').glob('*/response_attempt_01.raw.json'))
    assert len(archives) == 1 and archives[0].read_bytes() == original


@pytest.mark.parametrize('bad_outline', [{}, {**outline(), 'coverage': [{'segment_id': 'other_book', 'placement': '旧稿'}]}])
def test_candidate_rejects_missing_or_foreign_outline_before_review(tmp_path, monkeypatch, bad_outline):
    packet = tmp_path/'candidate.json'
    atomic_write_json(packet, {'draft': {'clips': []}, 'outline': bad_outline})
    main = cli.main
    def candidate_main(**kwargs):
        sys.argv.extend(['--candidate', str(packet)])
        return main(**kwargs)
    monkeypatch.setattr(cli, 'main', candidate_main)
    reviews = []
    with pytest.raises(SystemExit) as stopped:
        run(tmp_path, monkeypatch, 4,
            lambda *a: (reviews.append(a), ({'verdicts': []}, []))[1])
    assert stopped.value.code == 2 and reviews == []


def test_completed_pose_reading_survives_later_interruption_without_publishing(tmp_path, monkeypatch):
    from novel_manga.application.planning import flow, presence, retention
    from novel_manga.application.packing import posture
    from support.posture import unspecified_reply
    packet = tmp_path / 'candidate.json'
    atomic_write_json(packet, {'draft': {'video_title': '同一候选', 'clips': []}, 'outline': outline()})
    main, attempts = cli.main, flow.plan_attempts
    def current_outline(args, ctx, inputs):
        value = json.loads(packet.read_text())
        value['outline']['coverage'] = [{'segment_id': s['segment_id'], 'placement': '庭院'}
                                       for s in inputs.payload['segments']]
        atomic_write_json(packet, value)
        return attempts(args, ctx, inputs)
    reads, interrupted = [], [True]
    def candidate_main(**kwargs):
        sys.argv.extend(['--candidate', str(packet)])
        monkeypatch.setattr(posture, 'ask_json',
            lambda *a, **k: (reads.append(True), unspecified_reply(*a, **k))[1])
        def check_presence(*a, **k):
            if interrupted[0]:
                raise KeyboardInterrupt()
            return {'section': a[0], 'verdicts': [], 'grades': {'1': {'甲先生': 'on_camera'}}, 'marks': []}, []
        monkeypatch.setattr(retention, 'review', check_presence)
        return main(**kwargs)
    monkeypatch.setattr(cli, 'main', candidate_main)
    monkeypatch.setattr(flow, 'plan_attempts', current_outline)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, monkeypatch, 0, lambda *a: ({}, []))
    episode = tmp_path / 'out/book/book_1'
    snapshot = json.loads((episode / 'planning_attempt.json').read_text())
    assert snapshot['posture_states']['batches'] and snapshot['candidate']['video_title'] == '同一候选'
    assert not (episode / 'chapter_script.json').exists()
    interrupted[0] = False
    code, writers, _, _ = run(tmp_path, monkeypatch, 0, lambda *a: ({}, []))
    assert code == 0 and writers == [] and len(reads) == 1


def test_interrupted_pose_batch_resumes_completed_prefix_without_new_writer(tmp_path, monkeypatch):
    import httpx
    from novel_manga.application.planning import flow
    from novel_manga.application.packing import posture
    from novel_manga.planning import validation
    from novel_manga.planning.issues import ValidationResult
    from support.posture import unspecified_reply
    packet = tmp_path/'candidate.json'
    atomic_write_json(packet, {'draft': {'video_title': '已有稿', 'clips': []}, 'outline': outline()})
    main, attempts = cli.main, flow.plan_attempts
    def current_outline(args, ctx, inputs):
        value = json.loads(packet.read_text())
        value['outline']['coverage'] = [{'segment_id': s['segment_id'], 'placement': '庭院'}
                                       for s in inputs.payload['segments']]
        atomic_write_json(packet, value)
        return attempts(args, ctx, inputs)
    calls, interrupt = [], [True]
    def candidate_main(**kwargs):
        sys.argv.extend(['--candidate', str(packet)])
        normalize = validation.validate_and_normalize
        def three_shots(*a, **kw):
            checked = normalize(*a, **kw)
            return ValidationResult(checked.issues, checked.warnings,
                [{**checked.shots[0], 'index': i, 'label': f'c1 stage {i}'} for i in range(1, 4)])
        monkeypatch.setattr(validation, 'validate_and_normalize', three_shots)
        def ask(parts, schema, **kw):
            index = next(iter(schema['properties']['stages']['properties']))
            calls.append(index)
            if index == '2' and interrupt[0]:
                raise httpx.ReadTimeout('interrupted second batch')
            return unspecified_reply(parts, schema, **kw)
        monkeypatch.setattr(posture, 'ask_json', ask)
        return main(**kwargs)
    monkeypatch.setattr(posture, 'BATCH', 1)
    monkeypatch.setattr(flow, 'plan_attempts', current_outline)
    monkeypatch.setattr(cli, 'main', candidate_main)
    code, writers, _, episode = run(tmp_path, monkeypatch, 0, lambda *a: ({}, []))
    saved = json.loads((episode/'planning_attempt.json').read_text())
    assert code == 2 and writers == [] and set(saved['posture_states']['stages']) == {'1'}
    assert not (episode/'chapter_script.json').exists()
    interrupt[0] = False
    code, writers, _, _ = run(tmp_path, monkeypatch, 0, lambda *a: ({}, []))
    assert code == 0 and writers == [] and calls == ['1', '2', '2', '3']


def test_offline_replay_does_not_publish_an_unchecked_prior_attempt_cache(tmp_path, monkeypatch):
    from novel_manga.application.planning import retention
    episode = tmp_path / 'out/book/book_1'
    atomic_write_json(episode / 'planning_attempt.json',
                      {'posture_states': {'policy': 'old-candidate', 'inputs': []}})
    raw = tmp_path / 'response_attempt_01.raw.json'
    atomic_write_json(raw, {'video_title': '重放稿', 'clips': []})
    atomic_write_json(tmp_path / 'analysis_attempt_01.txt', outline())
    main = cli.main
    def replay_main(**kwargs):
        sys.argv.extend(['--replay', str(raw)])
        monkeypatch.setattr(retention, 'section_of', lambda *a: '')
        return main(**kwargs)
    monkeypatch.setattr(cli, 'main', replay_main)
    with pytest.raises(SystemExit) as stopped:
        run(tmp_path, monkeypatch, 0, lambda *a: ({}, []))
    assert stopped.value.code == 2
    assert not (episode / 'posture_states.json').exists()


@pytest.mark.parametrize('issue', [PlanningCode.QUOTE_NOT_SOURCE, PlanningCode.DURATION_ABOVE_MAXIMUM])
def test_agent_candidate_is_checked_without_host_patch_or_compression(tmp_path, monkeypatch, issue):
    from novel_manga.application.planning import flow, requests, compression
    packet = tmp_path/'candidate.json'
    atomic_write_json(packet, {'draft': {'video_title': '原Agent候选', 'clips': []}, 'outline': outline()})
    main = cli.main
    original_attempts = flow.plan_attempts
    def current_outline(args, ctx, inputs):
        value = json.loads(packet.read_text())
        value['outline']['coverage'] = [{'segment_id': s['segment_id'], 'placement': '见面事件'}
                                       for s in inputs.payload['segments']]
        atomic_write_json(packet, value)
        return original_attempts(args, ctx, inputs)
    def candidate_main(**kwargs):
        sys.argv.extend(['--candidate', str(packet)])
        return main(**kwargs)
    monkeypatch.setattr(flow, 'plan_attempts', current_outline)
    monkeypatch.setattr(cli, 'main', candidate_main)
    monkeypatch.setattr(requests, 'patch_batches', lambda *a, **k: pytest.fail('host rewrote Agent candidate'))
    monkeypatch.setattr(compression, 'compress_plan', lambda *a, **k: pytest.fail('host compressed Agent candidate'))
    failure = PlanningIssue(issue, '真实未解决的候选问题', stage='c1 stage 1', field='start_state')
    code, writers, _, episode = run(tmp_path, monkeypatch, 5, lambda *a: ({}, []), validation_issues=[failure])
    assert code != 0 and writers == [] and not (episode/'chapter_script.json').exists()
    failed = json.loads((episode/'planning_failed.json').read_text())
    assert any('真实未解决' in text for text in failed['errors'])
