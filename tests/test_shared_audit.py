import repair_manager_workers_thin as repair_manager_workers
import json
import os
from concurrent.futures import ThreadPoolExecutor

import novel_manga.review.audit_queue as audit_queue
import novel_manga.application.review.store as review_store
import novel_manga.repair.scheduling as schedule_rules
import repair_manager_flow_thin as repair_manager_flow
import repair_manager_workers_thin as repair_manager_workers


def targets(n=30):
    return [{'episode': i, 'clip': 'clip_01', 'video': f'/video/{i}.mp4', 'take': [i, 10, 100]} for i in range(1, n + 1)]


def test_concurrent_flash_qwen_workers_claim_each_clip_once(tmp_path):
    path = tmp_path / 'queue.sqlite3'
    audit_queue.initialize(path, targets())
    def work(lane):
        ids = []
        while row := audit_queue.claim(path, lane, os.getpid(), set()):
            ids.append(row['id'])
            audit_queue.finish(path, row, 'done', {'lane': lane, 'id': row['id']})
        return ids
    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = [i for group in pool.map(work, ['qwen'] * 8 + ['flash'] * 2) for i in group]
    assert len(ids) == len(set(ids)) == 30
    audit_queue.initialize(path, targets())
    assert audit_queue.summary(path)['counts'] == {'done': 30}


def test_repair_owned_episode_is_skipped_until_it_is_free(tmp_path):
    path = tmp_path / 'queue.sqlite3'
    audit_queue.initialize(path, targets(2))
    row = audit_queue.claim(path, 'qwen', os.getpid(), {1})
    assert row['episode'] == 2
    assert audit_queue.claim(path, 'flash', os.getpid(), {1}) is None
    assert audit_queue.claim(path, 'flash', os.getpid(), set())['episode'] == 1


def test_restart_only_requeues_abandoned_work(tmp_path, monkeypatch):
    path = tmp_path / 'queue.sqlite3'
    audit_queue.initialize(path, targets(2))
    audit_queue.claim(path, 'qwen', 111, set())
    audit_queue.claim(path, 'flash', 222, set())
    monkeypatch.setattr(audit_queue, 'alive', lambda pid: pid == 222)
    audit_queue.recover_abandoned(path)
    assert audit_queue.summary(path)['counts'] == {'pending': 1, 'running': 1}
    assert audit_queue.claim(path, 'qwen', 333, set())['episode'] == 1


def test_joint_qwen_result_is_loaded_and_explicit_confirmation_keeps_priority(tmp_path):
    legacy, state = tmp_path / 'old', tmp_path / 'state'
    legacy.mkdir();state.mkdir()
    row = {'ep': 1, 'clip': 'clip_01', 'video': '/video/1.mp4', 'take': [1, 10, 100], 'verdict': 'obvious', 'mode': 'joint'}
    audit_queue.initialize(state / 'shared_audit.sqlite3', targets(1))
    claim = audit_queue.claim(state / 'shared_audit.sqlite3', 'qwen', os.getpid(), set())
    audit_queue.finish(state / 'shared_audit.sqlite3', claim, 'done', row)
    (legacy / 'wy_verify.jsonl').write_text(json.dumps({**row, 'mode': 'all', 'verdict': 'fine'}) + '\n')
    local, flash = review_store.current_evidence(legacy, state)
    assert next(iter(local.values()))['verdict'] == 'obvious' and not flash
    (state / 'verified.jsonl').write_text(json.dumps({**row, 'mode': 'confirm', 'verdict': 'fine'}) + '\n')
    local, _ = review_store.current_evidence(legacy, state)
    assert next(iter(local.values()))['verdict'] == 'fine'


def test_shared_scanners_are_supplementary_and_failed_checks_are_not_success(tmp_path, monkeypatch):
    m = repair_manager_flow.Manager(tmp_path / 'book', tmp_path / 'old')
    result = tmp_path / 'exit.json';result.write_text('{"returncode":4}')
    job = m.add('scan', [], source='shared_qwen', pid=123, result=str(result))
    assert schedule_rules.supplementary(job)
    monkeypatch.setattr(repair_manager_workers, 'alive', lambda pid: False)
    repair_manager_workers.reap(m)
    assert job['status'] == 'needs_attention'
