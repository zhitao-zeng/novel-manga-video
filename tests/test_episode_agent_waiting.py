"""Waiting for host work must not repeatedly inspect the full production history."""
from concurrent.futures import Future

import pytest

from novel_manga.application.agents.episode import bridge, lifecycle


@pytest.mark.parametrize('failed', [False, True])
def test_wait_inspects_episode_once_after_pending_job_finishes(tmp_path, monkeypatch, failed):
    calls = []

    class Tools:
        def dispatch(self, action, payload):
            calls.append(action)
            return {'generated_counts': {'clip_01': 11}}

    server = bridge.EpisodeBridge(Tools(), tmp_path/'episode.sock')
    future = Future()
    server._job = {'id': 'job-11', 'action': 'render', 'status': 'running'}
    server._future = future
    waits = []

    def advance(seconds):
        # The expensive inspection must not compete with the outstanding render.
        assert calls == []
        waits.append(seconds)
        if len(waits) == 5:
            if failed:
                future.set_exception(ValueError('renderer disconnected'))
            else:
                future.set_result({'quality_passed': True})

    monkeypatch.setattr(lifecycle.time, 'sleep', advance)
    result = lifecycle.wait_for_idle(server)
    assert len(waits) == 5 and calls == ['status']
    assert result['busy'] is False
    assert result['generated_counts'] == {'clip_01': 11}
    assert result['job']['id'] == 'job-11'
    assert result['job']['status'] == ('failed' if failed else 'completed')
    if failed:
        assert result['job']['error'] == 'ValueError: renderer disconnected'


def test_operation_status_preserves_accepted_turn_without_dispatch(tmp_path):
    class Tools:
        def dispatch(self, *args):
            pytest.fail('accepted work cannot dispatch before the Agent yields')

    server = bridge.EpisodeBridge(Tools(), tmp_path/'episode.sock')
    server.begin_turn()
    accepted = server.handle('render', {})
    result = server.operation_status()
    assert result['busy'] and result['job']['id'] == accepted['job_id']
    assert result['job']['status'] == 'accepted'


def test_idle_wait_still_returns_full_episode_status(tmp_path, monkeypatch):
    class Tools:
        def dispatch(self, action, payload):
            assert action == 'status'
            return {'remaining_takes': {'clip_01': 0}}

    server = bridge.EpisodeBridge(Tools(), tmp_path/'episode.sock')
    monkeypatch.setattr(lifecycle.time, 'sleep', lambda seconds: pytest.fail('idle bridge must not wait'))
    assert lifecycle.wait_for_idle(server) == {
        'ok': True, 'remaining_takes': {'clip_01': 0}, 'busy': False, 'job': None}
