"""Production URLs must not contain inline comments; quoted secrets retain hashes."""
import os
import pytest
from unittest.mock import patch
from novel_manga.util import load_dotenv
from novel_manga.llm.resources import _endpoint


@pytest.fixture(autouse=True)
def restore_environment():
    with patch.dict(os.environ):
        yield


def test_unquoted_image_url_comment_does_not_become_part_of_port(tmp_path, monkeypatch):
    monkeypatch.delenv('NOVEL_LOCAL_QWEN_IMAGE_URL', raising=False)
    file=tmp_path/'.env'
    file.write_text('NOVEL_LOCAL_QWEN_IMAGE_URL=http://127.0.0.1:8770 # local image server\n')
    load_dotenv(file)
    import os
    assert _endpoint(os.environ['NOVEL_LOCAL_QWEN_IMAGE_URL'])=='127.0.0.1_8770'


def test_quoted_values_and_literal_hash_keep_content_and_explicit_env_wins(tmp_path, monkeypatch):
    names=['DOTENV_TEST_QUOTED','DOTENV_TEST_HASH','DOTENV_TEST_EXISTING']
    for name in names:monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('DOTENV_TEST_EXISTING','explicit choice')
    file=tmp_path/'.env';file.write_text('export DOTENV_TEST_QUOTED="value # retained" # note\n'
        'DOTENV_TEST_HASH=value#retained # note\nDOTENV_TEST_EXISTING=other # note\n')
    load_dotenv(file)
    import os
    assert os.environ['DOTENV_TEST_QUOTED']=='value # retained'
    assert os.environ['DOTENV_TEST_HASH']=='value#retained'
    assert os.environ['DOTENV_TEST_EXISTING']=='explicit choice'
