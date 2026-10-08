"""A preparation worker must keep an explicit model choice through child launches."""
import json
import os

import pytest

from novel_manga.application.configuration import preparation_environment
from novel_manga.llm.config import ENDPOINTS, JsonEndpoint


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/pipeline.json').write_text(json.dumps({
        'defaults': {'preparation': {
            'endpoints': ['http://default-preparation.invalid/v1'],
            'model': 'default-preparation-model', 'key_var': 'DEFAULT_PREPARATION_KEY',
            'clip_cap': 15,
        }},
    }))
    (tmp_path / '.env').write_text('GPU52_DEEPSEEK_API_KEY=file-ds-key\n')
    return tmp_path


def test_preparation_defaults_still_follow_pipeline(root, monkeypatch):
    monkeypatch.delenv('NOVEL_PREPARATION_ENDPOINT', raising=False)
    env = preparation_environment(root)
    endpoint = JsonEndpoint.from_env(env)
    assert endpoint.model == 'default-preparation-model'
    assert endpoint.endpoints == ('http://default-preparation.invalid/v1',)
    assert env['NOVEL_CLIP_SECONDS_MAX'] == '15'


def test_deepseek_preparation_keeps_credentials_and_choice_in_children(root, monkeypatch):
    monkeypatch.setenv('NOVEL_PREPARATION_ENDPOINT', 'deepseek')
    monkeypatch.delenv('GPU52_DEEPSEEK_API_KEY', raising=False)
    before = dict(os.environ)
    env = preparation_environment(root)
    assert dict(os.environ) == before
    endpoint = JsonEndpoint.from_env(env)
    assert endpoint.model == 'DeepSeek-V4.1-Flash'
    assert endpoint.endpoints == (ENDPOINTS['deepseek']['QWEN38_LOCAL_BASE_URL'],)
    assert endpoint.key == 'file-ds-key'
    assert not endpoint.stream
    # Every episode subprocess enters the same CLI and assembles its lane again.
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    child = JsonEndpoint.from_env(preparation_environment(root))
    assert child == endpoint


def test_bad_preparation_choice_fails_before_workers_start(root, monkeypatch):
    monkeypatch.setenv('NOVEL_PREPARATION_ENDPOINT', 'deepsek')
    with pytest.raises(ValueError, match='unknown preparation endpoint deepsek'):
        preparation_environment(root)
