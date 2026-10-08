"""Host transition diagnostics never replace authority or change operation outcomes."""
import json
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.production import episode_state as events
from novel_manga.application.agents.episode.tool_state import record_state
from novel_manga.application.agents.episode.bridge import EpisodeBridge
from novel_manga.util import read_json


def test_events_append_and_skip_invalid_payloads(tmp_path):
    assert not events.record(tmp_path / 'absent', 'operation')
    assert not events.record(tmp_path, 'operation', value=object())
    assert not (tmp_path / events.FILE).exists()
    assert events.record(tmp_path, 'operation', phase='rendering')
    assert events.record(tmp_path, 'acceptance_saved', result='needs_review', passed=False)
    rows = [json.loads(line) for line in (tmp_path / events.FILE).read_text().splitlines()]
    assert len(rows) == 2 and rows[0]['phase'] == 'rendering'
    assert rows[0]['result'] == 'observed' and rows[1]['passed'] is False
    assert events.last_event(tmp_path) == rows[1]


def test_incomplete_tail_keeps_previous_valid_event(tmp_path):
    assert events.record(tmp_path, 'job', job_status='running')
    good = events.last_event(tmp_path)
    with (tmp_path / events.FILE).open('a') as stream:
        stream.write('{"ts":')
    assert events.last_event(tmp_path) == good


def test_tool_state_keeps_authority_when_diagnostics_fail(tmp_path, monkeypatch):
    ctx = SimpleNamespace(run_dir=tmp_path, episode_dir=tmp_path / 'book/book_1')
    original = Path.open
    def fail_projection(path, *args, **kwargs):
        if path.name == events.FILE:
            raise PermissionError('diagnostic append unavailable')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail_projection)
    record_state(ctx, 'rendering', workers=2)
    authority = read_json(tmp_path / 'execution.json')
    assert authority['phase'] == 'rendering' and authority['workers'] == 2
    assert authority['episode'] == str(ctx.episode_dir)


def test_bridge_logs_transitions_without_treating_status_as_progress(tmp_path):
    class Tools:
        def dispatch(self, action, payload):
            assert action == 'status'
            return {'generated_counts': {}}
    bridge = EpisodeBridge(Tools(), tmp_path / 'unused.sock')
    try:
        bridge.bind_state(tmp_path / 'agent-run.json')
        bridge.update_state(status='running', phase='tool_running', pid=123,
                            job={'id': 'job-1', 'action': 'render', 'status': 'accepted'})
        assert events.last_event(tmp_path)['job_status'] == 'accepted'
        bridge.update_state(job={'id': 'job-1', 'action': 'render', 'status': 'running'})
        saved = (tmp_path / events.FILE).read_bytes()
        bridge.update_state(remaining_seconds=100)
        bridge.handle('status', {})
        assert (tmp_path / events.FILE).read_bytes() == saved
        assert not (tmp_path / 'book' / events.FILE).exists()
    finally:
        bridge._executor.shutdown(wait=True)
