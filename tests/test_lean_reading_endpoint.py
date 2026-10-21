"""Chapter extraction retains the selected API transport and cached reads."""
import pytest

from novel_manga.application.planning import lean_bible


@pytest.mark.parametrize('stream, expected', [('1', True), ('0', False), (None, False)])
def test_reading_uses_selected_transport_and_reuses_completed_chapter(tmp_path, monkeypatch, stream, expected):
    if stream is None:
        monkeypatch.delenv('QWEN38_LOCAL_STREAM', raising=False)
    else:
        monkeypatch.setenv('QWEN38_LOCAL_STREAM', stream)
    monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'deepseek-v4.1-flash')
    seen = []

    def answer(parts, schema, **kwargs):
        seen.append(kwargs['settings'])
        return {'people': [], 'locations': []}

    monkeypatch.setattr(lean_bible, 'ask_json', answer)
    for _ in range(2):
        result = lean_bible.read_chapters('test', {1: 'original chapter'}, tmp_path,
                                         ['http://127.0.0.1:8789/v1'], workers=8)
        assert result == {1: {'people': [], 'locations': []}}
    assert len(seen) == 1
    assert seen[0].model == 'deepseek-v4.1-flash'
    assert seen[0].stream is expected


def test_reading_workers_keep_novel_and_episode_usage_scope(tmp_path, monkeypatch):
    from novel_manga.llm import usage
    seen = []
    def answer(*args, **kwargs):
        seen.append(usage.scoped())
        return {'people': [], 'locations': []}
    monkeypatch.setattr(lean_bible, 'ask_json', answer)
    with usage.scope(novel='book', stage='reading'):
        lean_bible.read_chapters('test', {1: '原章一', 2: '原章二'}, tmp_path,
                                ['http://127.0.0.1:8789/v1'], workers=8)
    assert sorted(row['episode'] for row in seen) == [1, 2]
    assert all(row['novel'] == 'book' and row['stage'] == 'reading' for row in seen)
