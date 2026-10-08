"""The sandbox agent's Unix socket to existing host episode tools, one job at a time."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import socket
import threading
import time

from novel_manga.util import atomic_write_json, read_json

ACTIONS = {'check', 'adopt', 'render', 'review', 'apply', 'status', 'finish', 'doctor'}
SLOW_ACTIONS = {'adopt', 'render', 'review', 'apply', 'finish', 'doctor'}
MAX_REQUEST_BYTES = 65536


def parse_request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('episode tool request is too large')
    try:
        request = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('episode tool request must be JSON') from error
    if not isinstance(request, dict) or not isinstance(request.get('action'), str) or request['action'] not in ACTIONS:
        raise ValueError('choose check, adopt, render, review, apply, status, finish or doctor')
    payload = request.get('payload', {})
    if not isinstance(payload, dict):
        raise ValueError('episode tool payload must be an object')
    return request['action'], payload


def install_client(work):
    destination = Path(work) / 'tools/episode.py'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).with_name('client.py'), destination)
    return destination


def log(event, **details):
    print(json.dumps({'event': event, **details}, ensure_ascii=False), flush=True)


class EpisodeBridge:
    def __init__(self, tools, socket_path):
        self.tools = tools
        socket_path = Path(socket_path)
        self.socket_path = socket_path if socket_path.suffix == '.sock' else socket_path / 'episode-tools.sock'
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future = None
        self._job = None
        self._count = 0
        self._turn_active = False
        self.state_path = None

    def bind_state(self, path):
        """Keep accepted operations in the existing episode run record."""
        self.state_path = Path(path)
        saved = read_json(self.state_path, {}).get('job')
        if saved:
            self._count = int(saved['id'].split('-')[-1])
            self._job = saved
            if saved['status'] in {'accepted', 'running'}:
                self._job = {**saved, 'status': 'accepted'}
                if saved['status'] == 'running' and saved['action'] == 'apply':
                    # An interrupted write may already be in the plan/history.
                    # Let the same agent inspect it instead of applying it twice.
                    self._job.update(status='failed', error='Apply was interrupted; inspect current plan and repair history before continuing.')
            self._save_job()

    def update_state(self, **values):
        with self._lock:
            if self.state_path is not None:
                state = read_json(self.state_path, {})
                state.update(values)
                state['updated_at'] = time.strftime('%F %T')
                atomic_write_json(self.state_path, state)

    def _save_job(self):
        self.update_state(job=self._job_status())

    def begin_turn(self):
        with self._lock:
            self._turn_active = True

    def end_turn(self, *, dispatch=True):
        """Execute a handoff only after the CLI has ended and released its LM slot."""
        with self._lock:
            self._turn_active = False
            if dispatch and self._job and self._job.get('status') == 'accepted':
                self._start_job()

    def _start_job(self):
        self._job['status'] = 'running'
        self._save_job()
        job = dict(self._job)
        log('job_started', id=job['id'], action=job['action'])
        self._future = self._executor.submit(self._dispatch, job['action'], job.get('payload', {}))
        self._future.add_done_callback(lambda future: self._complete(future, job))

    def stop(self):
        self._stop.set()

    def _dispatch(self, action, payload):
        result = self.tools.dispatch(action, payload)
        if not isinstance(result, dict):
            raise ValueError('episode tool handler must return an object')
        return result

    def _job_status(self):
        if self._job is None:
            return None
        job = dict(self._job)
        if self._future is None:
            return job
        if not self._future.done():
            return {**job, 'status': 'running'}
        try:
            return {**job, 'status': 'completed', 'result': self._future.result()}
        except Exception as error:
            return {**job, 'status': 'failed', 'error': f'{type(error).__name__}: {error}'}

    def _complete(self, future, job):
        log('job_finished', id=job['id'], action=job['action'],
            status='failed' if future.exception() else 'completed')
        with self._lock:
            if self._job['id'] == job['id']:
                self._job = self._job_status()
                self._save_job()

    def handle(self, action, payload):
        with self._lock:
            busy = (self._job is not None and self._job.get('status') == 'accepted') or (self._future is not None and not self._future.done())
            waiting_turn = bool(self._turn_active and self._job and self._job.get('status') == 'accepted')
            handoff = ({'yield': True, 'next': 'End this Agent turn now. The accepted operation cannot start until you return; the host will resume you with its result.'}
                       if waiting_turn else {})
            if action == 'status':
                return {'ok': True, **self._dispatch('status', {}), 'busy': busy, 'job': self._job_status(), **handoff}
            if busy:
                return {'ok': False, 'status': 'busy', 'job_id': self._job['id'],
                        'error': 'An episode operation is pending; end this turn for the host to execute it.'
                            if waiting_turn else 'An episode operation is running; wait for its completion before starting another.',
                        **handoff}
            if action == 'check':
                return {'ok': True, **self._dispatch(action, payload)}
            if action not in SLOW_ACTIONS:
                raise ValueError('unknown episode tool action')
            self._count += 1
            self._job = {'id': f'job-{self._count}', 'action': action, 'payload': payload,
                         'status': 'accepted'}
            self._future = None
            self._save_job()
            if not self._turn_active:
                self._start_job()
            return {'ok': True, 'status': 'accepted' if self._turn_active else 'running',
                    'job_id': self._job['id'], 'action': action,
                    **({'yield': True, 'next': 'End this Agent turn. The host will run the operation and resume the same session with its result.'}
                       if self._turn_active else {})}

    def serve(self):
        # bind fails if a different server or an old pathname is still present.
        # Only the socket successfully bound by this invocation is removed.
        bound = False
        try:
            if self.socket_path.exists():
                raise FileExistsError(f'episode tool socket already exists: {self.socket_path}')
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(self.socket_path))
                bound = True
                self.socket_path.chmod(0o666)  # the sandbox runs as uid 1001
                listener.listen()
                listener.settimeout(0.2)
                self.ready.set()
                log('listening', socket=str(self.socket_path))
                while not self._stop.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(10)
                        try:
                            with connection.makefile('rb') as stream:
                                action, payload = parse_request(stream.readline(MAX_REQUEST_BYTES + 1))
                            answer = self.handle(action, payload)
                        except Exception as error:
                            answer = {'ok': False, 'status': 'error', 'error': f'{type(error).__name__}: {error}'}
                        try:
                            connection.sendall(json.dumps(answer, ensure_ascii=False).encode('utf-8') + b'\n')
                        except OSError:
                            pass  # an accepted job continues if the caller disconnected
        finally:
            self._executor.shutdown(wait=True)
            if bound:
                self.socket_path.unlink(missing_ok=True)
            log('stopped')
