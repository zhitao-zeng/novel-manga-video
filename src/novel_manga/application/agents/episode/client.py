"""Standalone CLI copied into /work/tools/episode.py for the sandbox agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys

DEFAULT_SOCKET = Path('/work/episode-tools.sock')


def request(action, payload, *, socket_path=None):
    """One small JSON request and response on the episode's Unix socket."""
    message = json.dumps({'action': action, 'payload': payload}, ensure_ascii=False).encode('utf-8') + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(30)
        connection.connect(str(socket_path or DEFAULT_SOCKET))
        connection.sendall(message)
        with connection.makefile('rb') as response:
            line = response.readline()
    if not line:
        raise ValueError('episode tools returned no response')
    answer = json.loads(line)
    if not isinstance(answer, dict):
        raise ValueError('episode tools response must be an object')
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Call the host episode workflow through /work/episode-tools.sock; print JSON.',
        epilog='render, review, apply and finish return a job_id immediately. When yield=true, '
               'end this Agent turn; the host executes the operation and resumes the same session. '
               'On resume, inspect the host state and job result supplied in the continuation prompt. '
               'Only status is available while an operation is running; new operations return busy. '
               'Files are paths relative to /work. No file contents or credentials are sent by this CLI.')
    commands = parser.add_subparsers(dest='action', required=True)
    check = commands.add_parser('check', help='Check the current authored episode or final requests without rendering.')
    check.add_argument('file', nargs='?', help='default: output/requests.json for a prepared script, otherwise output/episode.json')
    adopt = commands.add_parser('adopt', help='Validate and adopt a draft for input/asset repair without generating video.')
    adopt.add_argument('file', nargs='?', help='default: current authored episode or requests file')
    render = commands.add_parser('render', help='Validate and render a draft or current plan, and export the existing independent review evidence.')
    render.add_argument('file', nargs='?', help='optional authored episode or requests file; omit to use the current prepared plan')
    commands.add_parser('review', help='Review the current generated takes.')
    apply = commands.add_parser('apply', help='Apply the agent-authored repair file.')
    apply.add_argument('file', nargs='?', default='output/repair.json', help='default: output/repair.json')
    commands.add_parser('status', help='Read current state, generation budget and the latest job result.')
    commands.add_parser('doctor', help='Ask the host to check configured services; yields like other host jobs.')
    commands.add_parser('finish', help='Run the existing final acceptance and delivery step.')
    args = parser.parse_args(argv)
    payload = {'file': args.file} if getattr(args, 'file', None) is not None else {}
    try:
        answer = request(args.action, payload)
    except (OSError, ValueError) as error:
        answer = {'ok': False, 'status': 'error', 'error': f'{type(error).__name__}: {error}'}
    print(json.dumps(answer, ensure_ascii=False), flush=True)
    return 0 if answer.get('ok', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
