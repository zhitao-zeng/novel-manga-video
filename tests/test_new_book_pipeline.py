import json
from pathlib import Path

from novel_manga.application.planning import setup_book
from novel_manga.util import atomic_write_json


def test_source_registration_needs_no_seed_model_and_keeps_existing_numbering(tmp_path):
    source = tmp_path/'source.md'
    source.write_text('# 第一章 开始\n\n甲推开门。'*40)
    book = tmp_path/'book'
    first = setup_book.register_source(book, source, 'book', '测试')
    before = (book/'novel.json').read_bytes()
    again = setup_book.register_source(book, source, 'book', '测试')
    assert first['chapters'] == again['chapters']
    assert (book/'novel.json').read_bytes() == before
    assert not (book/'story_bible.json').exists()


def test_handoff_fills_only_missing_recaps_without_rereading(tmp_path):
    atomic_write_json(tmp_path/'novel.json', {'chapters': [
        {'index': 1, 'title': '开篇'}, {'index': 2, 'title': '来客'}]})
    original = {'chapter': 1, 'title': '原题', 'summary': '已验证的丰富摘要', 'hook': '原悬念'}
    atomic_write_json(tmp_path/'recap.json', [original])
    for n in [1, 2]:
        atomic_write_json(tmp_path/f'lean/extract/ch-{n:03d}.json',
                          {'chapter': n, 'answer': {'summary': f'初读摘要{n}'}})
    assert setup_book.handoff_reading(tmp_path, [1, 2]) == [2]
    rows = json.loads((tmp_path/'recap.json').read_text())
    assert rows[0] == original
    assert rows[1] == {'chapter': 2, 'title': '来客', 'summary': '初读摘要2', 'hook': ''}
    before = (tmp_path/'recap.json').read_bytes()
    assert setup_book.handoff_reading(tmp_path, [1, 2]) == []
    assert (tmp_path/'recap.json').read_bytes() == before


def test_a_recorded_empty_cast_is_still_a_completed_reading(tmp_path):
    atomic_write_json(tmp_path/'reading_cast.json', {'characters': [], 'chapters': [1]})
    atomic_write_json(tmp_path/'lean/extract/ch-001.json', {'answer': {'summary': '原文描写空旷山谷。'}})
    assert setup_book.reading_ready(tmp_path, [1])


def test_existing_adopted_book_skips_read_merge_and_seed_and_enters_planning(tmp_path, monkeypatch):
    book = tmp_path/'outputs/book';book.mkdir(parents=True)
    source = tmp_path/'source.md';source.write_text('# 第一章 开始\n\n甲进门。')
    atomic_write_json(book/'novel.json', {'novel_id': 'book', 'title': '测试', 'source': str(source),
        'chapters': [{'index': 1, 'title': '开始', 'start': 0, 'end': len(source.read_text())}]})
    atomic_write_json(book/'story_bible.json', {'characters': [{'name': '甲'}], 'locations': []})
    atomic_write_json(book/'reading_cast.json', {'characters': ['甲'], 'aliases': {}, 'chapters': [1]})
    atomic_write_json(book/'lean/extract/ch-001.json', {'answer': {'summary': '甲进门。'}})
    config = tmp_path/'agent.json';atomic_write_json(config, {'model': 'deepseek-v4.1-flash',
        'base_url': 'http://127.0.0.1:8789', 'key_var': 'TEST_KEY', 'runs_root': str(tmp_path/'runs')})
    monkeypatch.setattr(setup_book, 'ROOT', tmp_path)
    monkeypatch.setattr(setup_book, 'preflight', lambda *a, **k: ({}, []))
    calls = []
    monkeypatch.setattr(setup_book, 'run', lambda command, *a, **k: calls.append(command) or 0)
    before = {p: p.read_bytes() for p in [book/'novel.json', book/'story_bible.json', book/'reading_cast.json']}
    assert setup_book.main([str(source), '--novel-id', 'book', '--title', '测试', '--style', '2d',
        '--chapters', '1', '--agent-config', str(config), '--stage', 'plan', '--plan-parallel', '8']) == 0
    assert len(calls) == 1
    assert Path(calls[0][1]).name == 'thin_batch.py'
    assert Path(calls[0][1]).is_absolute()
    assert Path(calls[0][1]).parent.parent == Path(setup_book.__file__).resolve().parents[4]
    assert '--repair-planning' in calls[0] and '--no-grow-bible' in calls[0]
    assert {p: p.read_bytes() for p in before} == before
    assert json.loads((book/'recap.json').read_text())[0]['summary'] == '甲进门。'


def test_new_book_builds_the_seed_once_after_reading_and_resumes_without_model_work(tmp_path, monkeypatch):
    from novel_manga.application.identity import ledger_flow
    from tests.test_reading_merge import person
    source = tmp_path/'source.md';source.write_text('# 第一章 开始\n\n甲进门。')
    config = tmp_path/'agent.json';atomic_write_json(config, {'model': 'deepseek-v4.1-flash',
        'base_url': 'http://127.0.0.1:8789', 'key_var': 'TEST_KEY', 'runs_root': str(tmp_path/'runs')})
    monkeypatch.setattr(setup_book, 'ROOT', tmp_path)
    monkeypatch.setattr(setup_book, 'preflight', lambda *a, **k: ({}, []))
    monkeypatch.setattr(ledger_flow, 'adopt_roster', lambda *a: {'seeded': [], 'unresolved': []})
    calls = []
    def read(book, chapters, **kwargs):
        assert not (book/'story_bible.json').exists()
        calls.extend(['read', 'merge'])
        atomic_write_json(book/'lean/export.json', {'characters': [person('甲')], 'locations': []})
        atomic_write_json(book/'lean/extract/ch-001.json', {'answer': {'summary': '甲进门。'}})
        return book/'lean/export.json'
    def build(command, *args, **kwargs):
        assert Path(command[1]).name == 'build_bible_thin.py'
        assert Path(command[1]).is_absolute()
        calls.append('seed')
        atomic_write_json(tmp_path/'outputs/book/story_bible.json', {'characters': [{'name': '甲'}], 'locations': []})
        return 0
    monkeypatch.setattr(setup_book, 'read_and_merge', read)
    monkeypatch.setattr(setup_book, 'run', build)
    args = [str(source), '--novel-id', 'book', '--title', '测试', '--style', '2d',
            '--chapters', '1', '--agent-config', str(config)]
    assert setup_book.main(args) == 0
    assert calls == ['read', 'merge', 'seed']
    assert setup_book.main(args) == 0
    assert calls == ['read', 'merge', 'seed']


def test_preflight_checks_selected_reader_instead_of_retired_environment_endpoint(monkeypatch):
    from novel_manga.llm import config as endpoints
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'http://retired-27b/v1')
    monkeypatch.setenv('TEST_KEY', 'test-only')
    selected = endpoints.JsonEndpoint('deepseek-v4.1-flash', ('http://127.0.0.1:8789/v1',),
                                     key='test-only', stream=True, min_max_tokens=0)
    monkeypatch.setattr(endpoints, 'endpoint_settings', lambda name: selected)
    seen = []
    monkeypatch.setattr(setup_book, 'alive', lambda url, key='': seen.append(url) or 'retired' not in url)
    overrides, problems = setup_book.preflight({'base_url': 'http://127.0.0.1:8789',
        'key_var': 'TEST_KEY', 'model': selected.model}, reading=True)
    assert not problems
    assert all('retired' not in url for url in seen)
    assert overrides['NOVEL_LLM_MODEL'] == selected.model


def test_reading_payment_failure_stops_queued_work_and_preserves_completed_cache(tmp_path, monkeypatch):
    import httpx
    import pytest
    from novel_manga.application.planning import lean_bible
    from novel_manga.llm.config import JsonEndpoint
    calls = []
    atomic_write_json(tmp_path/'ch-001.json', {'answer': {'summary': '已完成'}})
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        raise httpx.HTTPStatusError('402 Payment Required', request=httpx.Request('POST', 'http://test'),
                                   response=httpx.Response(402))
    monkeypatch.setattr(lean_bible, 'ask_json', ask)
    with pytest.raises(httpx.HTTPStatusError):
        lean_bible.read_chapters('测试', {n: '甲进门。' for n in range(1, 5)}, tmp_path,
            ['http://test'], workers=1, settings=JsonEndpoint('ds41', ('http://test',)))
    assert calls == ['lean_extract']
    assert json.loads((tmp_path/'ch-001.json').read_text())['answer']['summary'] == '已完成'
