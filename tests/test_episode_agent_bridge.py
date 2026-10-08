"""The episode agent's local transport, with no model or media services."""
from contextlib import contextmanager
import json
import subprocess
import sys
import threading
import time

import pytest

from novel_manga.application.agents.episode import bridge, client


class FakeTools:
    def __init__(self):
        self.calls = []
        self.release = threading.Event()
        self.entered = threading.Event()
        self.failure = None

    def dispatch(self, action, payload):
        self.calls.append((action, payload))
        if action == 'status':
            return {'generated_counts': {'clip_01': 1}}
        if action == 'check':
            return {'valid': True, 'file': payload['file']}
        self.entered.set()
        assert self.release.wait(5)
        if self.failure:
            raise self.failure
        return {'action': action, 'finished': True}


@contextmanager
def running_server(tmp_path, tools):
    server = bridge.EpisodeBridge(tools, tmp_path / 'episode.sock')
    thread = threading.Thread(target=server.serve)
    thread.start()
    assert server.ready.wait(3)
    try:
        yield server
    finally:
        tools.release.set()
        server.stop()
        thread.join(5)
        assert not thread.is_alive()


def wait_job(server, expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.request('status', {}, socket_path=server.socket_path)
        if result['job']['status'] == expected:
            return result
        time.sleep(0.01)
    pytest.fail(f'job did not reach {expected}')


def test_check_roundtrip_and_slow_job_busy_completion(tmp_path):
    tools = FakeTools()
    with running_server(tmp_path, tools) as server:
        checked = client.request('check', {'file': 'output/episode.json'}, socket_path=server.socket_path)
        assert checked == {'ok': True, 'valid': True, 'file': 'output/episode.json'}
        started = client.request('render', {}, socket_path=server.socket_path)
        assert started['ok'] and started['job_id'] == 'job-1'
        assert tools.entered.wait(1)
        for action in ('render', 'review', 'apply', 'finish', 'check'):
            rejected = client.request(action, {}, socket_path=server.socket_path)
            assert rejected['status'] == 'busy' and not rejected['ok']
        status = client.request('status', {}, socket_path=server.socket_path)
        assert status['busy'] is True
        assert status['generated_counts'] == {'clip_01': 1}
        assert status['job']['action'] == 'render'
        assert [action for action, _ in tools.calls if action not in {'check', 'status'}] == ['render']
        tools.release.set()
        result = wait_job(server, 'completed')
        assert result['busy'] is False
        assert result['job']['result'] == {'action': 'render', 'finished': True}
    assert not server.socket_path.exists()


def test_job_error_is_reported_and_next_operation_can_start(tmp_path):
    tools = FakeTools()
    tools.failure = ValueError('candidate missing planned dialogue')
    with running_server(tmp_path, tools) as server:
        started = client.request('apply', {'file': 'output/repair.json'}, socket_path=server.socket_path)
        assert started['status'] == 'running'
        tools.release.set()
        result = wait_job(server, 'failed')
        assert result['job']['error'] == 'ValueError: candidate missing planned dialogue'
        tools.failure = None
        again = client.request('review', {}, socket_path=server.socket_path)
        assert again['job_id'] == 'job-2'
        assert wait_job(server, 'completed')['job']['result']['action'] == 'review'


def test_pending_turn_queries_request_handoff_instead_of_polling(tmp_path):
    tools = FakeTools()
    with running_server(tmp_path, tools) as server:
        server.begin_turn()
        accepted = client.request('render', {}, socket_path=server.socket_path)
        assert accepted['yield'] is True
        status = client.request('status', {}, socket_path=server.socket_path)
        assert status['busy'] and status['yield'] is True
        assert status['job']['status'] == 'accepted'
        assert 'cannot start until you return' in status['next']
        rejected = client.request('review', {}, socket_path=server.socket_path)
        assert rejected['status'] == 'busy' and rejected['yield'] is True
        assert 'poll' not in rejected['error']
        assert not tools.entered.is_set()
        server.end_turn()
        assert tools.entered.wait(1)
        tools.release.set()
        assert wait_job(server, 'completed')['job']['result']['action'] == 'render'
        assert [a for a, _ in tools.calls if a != 'status'] == ['render']


def test_stop_waits_for_the_owned_job_then_removes_only_socket(tmp_path):
    tools = FakeTools()
    untouched = tmp_path / 'episode.json'
    untouched.write_text('{}')
    with running_server(tmp_path, tools) as server:
        client.request('finish', {}, socket_path=server.socket_path)
        assert tools.entered.wait(1)
        server.stop()
        assert server.socket_path.exists()
        assert untouched.read_text() == '{}'
    assert untouched.read_text() == '{}'


@pytest.mark.parametrize('raw', [
    b'not-json\n', b'[]\n', b'{"action":"unknown","payload":{}}\n',
    b'{"action":"render","payload":[]}\n', b'{"action":[],"payload":{}}\n', b'{"payload":{}}\n',
    b' ' * (bridge.MAX_REQUEST_BYTES + 1),
])
def test_parser_rejects_invalid_small_requests(raw):
    with pytest.raises(ValueError):
        bridge.parse_request(raw)


@pytest.mark.parametrize('arguments,expected', [
    (['check'], ('check', {})),
    (['check', 'output/fixed.json'], ('check', {'file': 'output/fixed.json'})),
    (['render'], ('render', {})),
    (['render', 'output/episode.json'], ('render', {'file': 'output/episode.json'})),
    (['apply'], ('apply', {'file': 'output/repair.json'})),
    (['review'], ('review', {})), (['status'], ('status', {})), (['finish'], ('finish', {})),
])
def test_cli_argument_contract(monkeypatch, capsys, arguments, expected):
    calls = []
    monkeypatch.setattr(client, 'request', lambda action, payload: calls.append((action, payload)) or {'ok': True})
    assert client.main(arguments) == 0
    assert calls == [expected]
    assert json.loads(capsys.readouterr().out) == {'ok': True}


def test_client_install_is_standalone_and_documents_commands(tmp_path):
    installed = bridge.install_client(tmp_path)
    assert installed == tmp_path / 'tools/episode.py'
    result = subprocess.run([sys.executable, str(installed), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'check' in result.stdout and 'render' in result.stdout and 'status' in result.stdout
    assert 'episode-tools.sock' in result.stdout


def test_client_connection_error_still_prints_json(monkeypatch, capsys):
    def unavailable(*args):
        raise FileNotFoundError('episode socket is not running')
    monkeypatch.setattr(client, 'request', unavailable)
    assert client.main(['status']) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer == {'ok': False, 'status': 'error',
                      'error': 'FileNotFoundError: episode socket is not running'}


def test_existing_socket_path_is_not_deleted(tmp_path):
    target = tmp_path / 'episode.sock'
    target.write_text('belongs to someone else')
    server = bridge.EpisodeBridge(FakeTools(), target)
    with pytest.raises(FileExistsError):
        server.serve()
    assert target.read_text() == 'belongs to someone else'
