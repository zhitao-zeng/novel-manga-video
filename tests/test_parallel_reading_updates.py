"""Parallel chapter replies must not discard another chapter's roster verdict."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from novel_manga.application.review import bible


def test_parallel_unknown_actor_verdicts_both_survive(tmp_path, monkeypatch):
    path = tmp_path / 'reading_cast.json'
    path.write_text(json.dumps({'characters': ['主角'], 'declined': [], 'aliases': {'头儿': '主角'}}))
    finished = threading.Barrier(2)

    def reply(parts, schema, **kwargs):
        text = parts[0]['text']
        name = '甲新客' if '指称：甲新客' in text else '乙新客'
        finished.wait(timeout=5)
        return {'people': [{'name': name, 'verdict': '角色', 'why': '本章有明确出场'}]}

    monkeypatch.setattr(bible.model_client, 'ask_json', reply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: bible.judge_unknown(tmp_path, [name], '原文中客人出现。', 1),
                                ['甲新客', '乙新客']))
    assert all(results)
    final = json.loads(path.read_text())
    assert set(final['characters']) == {'主角', '甲新客', '乙新客'}
    assert final['aliases'] == {'头儿': '主角'}
