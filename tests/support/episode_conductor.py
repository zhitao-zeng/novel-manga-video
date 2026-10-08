"""The Agent executor shares production controls without calling models or video services."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.configuration import config_for_novel, model_resources_environment
from novel_manga.application.production import conductor_common, conductor_dispatch, conductor_flow, conductor_state, conductor_workers, control, episode_agents
from novel_manga.application.production.episode_agents import _model_environment as select_agent_model


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def pipeline(root):
    return {'resources': {'video_keys': {'h3': {
        'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool', 'key_var': '', 'pool': 'test-h3',
        'clip_cap': 15, 'parallel': 3, 'inflight': {'min': 1, 'max': 4, 'start': 2}}},
        'planning_models': {'shared': {'base': 'http://first/v1,http://second/v1', 'model': 'agent-model', 'slots': 2, 'key_var': 'MODEL_KEY'}}},
        'defaults': {'planning': {'block_size': 3, 'margin': 1}},
        'novels': [{'id': 'book', 'title': 'Book', 'novel_dir': str(root / 'book'), 'tmp_dir': str(root / 'production'),
                    'render_keys': ['h3'], 'planning': {'shared': 1}, 'blocks_max': 1, 'blocks_min': 1,
                    'ranges': [{'chapters': '1-3', 'plan_mode': 30}], 'executor': 'episode_agent',
                    'agent': {'input_mode': 'source', 'output_root': str(root / 'agent-output'),
                              'parallel': 2, 'workers': 3}}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / 'book').mkdir()
    write(tmp_path / 'book/novel.json', {'novel_id': 'book'})
    config = pipeline(tmp_path)
    write(tmp_path / 'configs/pipeline.json', config)
    monkeypatch.setattr(conductor_common, 'REPO', tmp_path)
    monkeypatch.setattr(control, 'processes', lambda: [])
    monkeypatch.setattr(episode_agents, '_model_environment', lambda c, row=None: {'NOVEL_AGENT_MODEL': 'agent-model'})
    spawned = []
    def spawn(c, name, command, extra=None):
        spawned.append((name, command, extra))
        c.procs[name] = SimpleNamespace(pid=900 + len(spawned), poll=lambda: None)
    monkeypatch.setattr(conductor_workers, 'spawn', spawn)
    return tmp_path, config, spawned


def make(config, root):
    return conductor_flow.Conductor(config_for_novel(config, 'book', root=root), dry_run=False)



def configure_pair(root, config, monkeypatch, count=16):
    resources = config['resources']['planning_models']
    resources['shared'].update(endpoint='deepseek', slots=8)
    resources['qwen'] = {'endpoint': 'flashnext', 'base': 'http://qwen/v1',
        'model': 'qwen-model', 'key_var': 'QWEN_KEY', 'slots': 4}
    novel = config['novels'][0]
    novel['planning'] = {'shared': 8, 'qwen': 4}
    novel['ranges'][0]['chapters'] = f'1-{count}'
    for endpoint, model, base, key in [('deepseek','agent-model','http://first','MODEL_KEY'),
                                       ('flashnext','qwen-model','http://qwen','QWEN_KEY')]:
        write(root/f'configs/{endpoint}.json', {'model': model, 'base_url': base, 'key_var': key})
    novel['agent'].update(parallel=12, config='configs/deepseek.json',
        model_configs={'deepseek': 'configs/deepseek.json', 'flashnext': 'configs/flashnext.json'})
    monkeypatch.setattr(episode_agents, '_model_environment', select_agent_model)
    monkeypatch.setenv('MODEL_KEY', 'test-ds-credential')
    monkeypatch.setenv('QWEN_KEY', 'test-qwen-credential')


def configure_native(root, config, monkeypatch, count):
    from novel_manga.util import read_json
    configure_pair(root, config, monkeypatch, count=count)
    for endpoint in ('deepseek', 'flashnext'):
        path = root/f'configs/{endpoint}.json'
        write(path, {**read_json(path), 'production_mode': 'pipeline_review'})
    config['novels'][0]['agent'].update(input_mode='auto', production_parallel=2, parallel=12)
