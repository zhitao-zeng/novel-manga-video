import json

from novel_manga.llm import usage as llm_usage
from novel_manga.llm.transport import _meter


def test_record_writes_scoped_row(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    with llm_usage.scope(novel='book', episode=12, stage='review'):
        assert llm_usage.record(name='judge', model='m1',
                                usage={'prompt_tokens': 100, 'completion_tokens': 5,
                                       'cache_read_input_tokens': 400})
    rows = [json.loads(x) for x in (tmp_path / '.runtime' / llm_usage.FILE).read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row['prompt_tokens'] == 100 and row['completion_tokens'] == 5
    assert row['cache_read_input_tokens'] == 400
    assert row['novel'] == 'book' and row['episode'] == 12 and row['stage'] == 'review'


def test_record_skips_empty_usage_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    assert llm_usage.record(name='x', model='m', usage=None) is False
    assert llm_usage.record(name='x', model='m', usage={}) is False
    assert not (tmp_path / '.runtime' / llm_usage.FILE).exists()


def test_scope_nests_and_restores():
    with llm_usage.scope(novel='a'):
        assert llm_usage.scoped()['novel'] == 'a'
        with llm_usage.scope(novel='b', episode=1):
            assert llm_usage.scoped()['novel'] == 'b' and llm_usage.scoped()['episode'] == 1
        assert llm_usage.scoped()['novel'] == 'a'
    assert llm_usage.scoped() == {}


def test_meter_extracts_schema_name_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    request = {'model': 'Qwen3.8-Flash-Next',
               'response_format': {'type': 'json_schema', 'json_schema': {'name': 'posture_continuity', 'schema': {}}}}
    body = {'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
    _meter('http://x:1/v1/chat/completions', request, body)
    row = json.loads((tmp_path / '.runtime' / llm_usage.FILE).read_text().splitlines()[0])
    assert row['name'] == 'posture_continuity'
    assert row['model'] == 'Qwen3.8-Flash-Next'
    assert row['endpoint'] == 'http://x:1/v1'
    assert row['total_tokens'] == 12


def test_meter_survives_malformed_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    _meter('http://x/v1/chat/completions', {}, {})
    _meter('http://x/v1/chat/completions', {'model': 1}, {'usage': None})
    assert not (tmp_path / '.runtime' / llm_usage.FILE).exists()
