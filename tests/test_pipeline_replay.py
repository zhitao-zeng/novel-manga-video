import json
from pathlib import Path
from support.pipeline_replay import clip_requests


def test_saved_chapters_keep_requests_bindings_cache_and_routes(tmp_path):
    expected = json.loads((Path(__file__).parent / 'fixtures/pipeline_requests_before.json').read_text())
    assert clip_requests(tmp_path) == expected
    assert clip_requests(tmp_path) == expected
