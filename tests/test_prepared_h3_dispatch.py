import preparation_store_thin as preparation_store
import repair_manager_dispatch_thin as repair_manager_dispatch
import repair_manager_state_thin as repair_manager_state
import json
import pytest

from novel_manga.util import atomic_write_json
import repair_manager_flow_thin as repair_manager_flow
import repair_manager_workers_thin as repair_manager_workers
import review_store_thin as review_store
import thin_runs as thin_runs


def setup(tmp_path, monkeypatch):
    novel = tmp_path / 'book'
    for n in (1, 2, 3):
        directory = novel / f'book_{n}'
        directory.mkdir(parents=True)
        atomic_write_json(directory / 'clip_plan.json', {'clips': []})
        atomic_write_json(directory / 'chapter_script.json', {'shots': []})
    m = repair_manager_flow.Manager(novel, tmp_path / 'legacy')
    m.state.update(preparation_gate=True, scan_started=True)
    seen = []
    def reconcile(directory, *args, **kwargs):
        seen.append(directory.name)
        return {}, {'clips': {}, 'feedback': {}}, {}
    monkeypatch.setattr(review_store, 'read_reconciled', reconcile)
    monkeypatch.setattr(thin_runs, 'episode_status', lambda *args: 'pending')
    return m, seen


def test_unprepared_episodes_are_not_rendered_or_given_video_reviews(tmp_path, monkeypatch):
    m, seen = setup(tmp_path, monkeypatch)
    preparation_store.record(m.novel / 'book_1', 'ready')
    preparation_store.record(m.novel / 'book_2', 'needs_repair')
    repair_manager_state.install_snapshot(m, repair_manager_state.read_snapshot(m))
    repair_manager_dispatch.schedule(m)
    assert seen == ['book_1']
    assert [j['episodes'] for j in m.state['jobs']] == [[1]]
    assert m.state['summary']['preparation']['waiting'] == 2
    assert m.state['summary']['total'] == 3


def test_stale_preparation_is_rejected_and_new_ready_work_is_admitted(tmp_path, monkeypatch):
    m, seen = setup(tmp_path, monkeypatch)
    directory = m.novel / 'book_1'
    preparation_store.record(directory, 'ready')
    atomic_write_json(directory / 'chapter_script.json', {'shots': [{'motion_prompt': 'changed'}]})
    repair_manager_state.install_snapshot(m, repair_manager_state.read_snapshot(m))
    repair_manager_dispatch.schedule(m)
    assert not m.state['jobs'] and not seen
    preparation_store.record(directory, 'ready')
    repair_manager_state.install_snapshot(m, repair_manager_state.read_snapshot(m))
    repair_manager_dispatch.schedule(m)
    assert [j['episodes'] for j in m.state['jobs']] == [[1]]


def test_production_edits_do_not_return_an_admitted_episode_to_preparation(tmp_path, monkeypatch):
    m, seen = setup(tmp_path, monkeypatch)
    directory = m.novel / 'book_1'
    preparation_store.record(directory, 'ready')
    repair_manager_state.install_snapshot(m, repair_manager_state.read_snapshot(m))
    atomic_write_json(directory / 'chapter_script.json', {'shots': [{'motion_prompt': 'production repair'}]})
    m.save()
    resumed = repair_manager_flow.Manager(m.novel, m.legacy)
    repair_manager_state.install_snapshot(resumed, repair_manager_state.read_snapshot(resumed))
    assert resumed.state['admitted_episodes'] == [1]
    assert not resumed.info[1].get('awaiting_preparation')


def test_initial_production_preserves_chapter_order(tmp_path, monkeypatch):
    m, seen = setup(tmp_path, monkeypatch)
    for n in (1, 2, 3):
        preparation_store.record(m.novel / f'book_{n}', 'ready')
    repair_manager_state.install_snapshot(m, repair_manager_state.read_snapshot(m))
    repair_manager_dispatch.schedule(m)
    assert [j['episodes'] for j in m.state['jobs']] == [[1], [2], [3]]


def test_empty_ready_queue_waits_for_preparation_instead_of_exiting(tmp_path, monkeypatch):
    m, seen = setup(tmp_path, monkeypatch)
    m.state['phase'] = 2
    m.last_delivery = repair_manager_workers.time.monotonic()
    monkeypatch.setattr(repair_manager_workers.time, 'sleep', lambda seconds: (_ for _ in ()).throw(InterruptedError('stop test loop')))
    with pytest.raises(InterruptedError):
        m.run()
    assert m.state['status'] == 'waiting_preparation'
    assert not m.state['jobs']
