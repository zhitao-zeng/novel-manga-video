"""Host-session fixtures shared by episode CLI and resource handoff regressions."""
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode import flow
from novel_manga.application.agents.episode.context import create_context
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def episode_context(tmp_path):
    book = tmp_path / 'original/book'
    (book / 'book_1').mkdir(parents=True)
    atomic_write_json(book / 'novel.json', {'novel_id': 'book'})
    context = create_context(book, 1, output_root=tmp_path / 'isolated', input_mode='source')
    return replace(context, project_root=Path(__file__).resolve().parents[2])


@pytest.fixture
def sandbox_config():
    return {'image': 'agent-sandbox:test', 'base_url': 'http://agent.example',
            'model': 'configured-agent', 'key_var': 'AGENT_TEST_KEY', 'timeout_seconds': 30}


def install_fake_runtime(monkeypatch, ctx):
    monkeypatch.setattr(flow, 'runtime_environment', lambda *args: dict(os.environ))
    monkeypatch.setattr(flow, 'prepare_inputs', lambda context: (
        (context.work_dir / 'input').mkdir(parents=True, exist_ok=True),
        (context.work_dir / 'output').mkdir(parents=True, exist_ok=True)))
    calls = []

    class FakeTools:
        def __init__(self, context):
            self.context = context

        def _main_media_started(self):
            return False

        def dispatch(self, action, payload):
            calls.append(action)
            if action == 'finish':
                atomic_write_json(ctx.work_dir / 'input/latest-finish.json', {
                    'passed': True, 'technical_passed': True,
                    'author_session': read_json(ctx.work_dir / 'episode-session.json')})
            return {'ok': True, 'action': action}

    monkeypatch.setattr(flow, 'EpisodeTools', FakeTools)
    return calls


def fake_attempt(ctx, index, exit_code=0):
    directory = ctx.work_dir / f'attempt-{index}'
    directory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(ok=exit_code == 0, exit_code=exit_code, directory=directory,
                           record={'session_id': 'same-session'}, produced=[])
