import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

from novel_manga.util import atomic_write_json
from novel_manga.application.packing.context import context_for_plan, load_context
from novel_manga.application.packing.service import compile_plan
from novel_manga.application.profiles import plan_fingerprint
from test_split_repair_and_assets import split_episode

ROOT = Path(__file__).resolve().parents[1]


def test_packing_inputs_and_cut_explanations_are_isolated_across_parallel_contexts(tmp_path, monkeypatch):
    directory, script, saved = split_episode.__wrapped__(tmp_path, monkeypatch)
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '林凡说话。'}])
    a = context_for_plan(directory, directory.parent / 'story_bible.json', saved)
    b = copy.deepcopy(a)
    b['compiler_options'] = replace(a['compiler_options'], max_clip_seconds=30, soft_cut_seconds=18, max_stages=6,
                                     voices={'林凡': 'series_assets/voices/另一本书.wav'})
    script_before = copy.deepcopy(script)
    def compile_one(ctx):
        return compile_plan(script, ctx)
    expected = [compile_one(a), compile_one(b)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(compile_one, [a,b]*4))
    assert results == expected * 4
    assert script == script_before
    assert expected[0][0]['limits']['max_clip_seconds'] == 15
    assert expected[1][0]['limits']['max_clip_seconds'] == 30
    assert not any(r['role'] == 'voice' for c in expected[0][0]['clips'] for r in c.get('references', []))
    assert any(r['role'] == 'voice' for c in expected[1][0]['clips'] for r in c.get('references', []))
    assert len(expected[0][0]['clips']) > len(expected[1][0]['clips'])
    results[-1][1]['decisions'].clear()
    assert compile_one(a) == expected[0]


def test_command_publishes_same_plan_and_invalidates_only_stale_render_report(tmp_path, monkeypatch):
    directory, script, _ = split_episode.__wrapped__(tmp_path, monkeypatch)
    bible = directory.parent / 'story_bible.json'
    atomic_write_json(directory / 'chapter_script.json', script)
    atomic_write_json(directory / 'segments.json', [{'segment_id': 'seg_1', 'text': '林凡说话。'}])
    ctx = load_context(directory, bible, frame='16:9', tier='fast')
    plan, decisions = compile_plan(script, ctx)
    atomic_write_json(directory / 'thin_media_report.json', {'clip_plan_fingerprint': 'older-plan'})
    command = [sys.executable, str(ROOT / 'scripts/build_clip_plan_thin.py'), '--episode-dir', str(directory),
               '--bible', str(bible), '--frame', '16:9', '--tier', 'fast']
    env = {**os.environ, 'PYTHONPATH': f'{ROOT / "src"}:{ROOT / "scripts"}'}
    subprocess.run(command, cwd=tmp_path, env=env, check=True, capture_output=True, timeout=20)
    assert json.loads((directory / 'clip_plan.json').read_text()) == plan
    assert json.loads((directory / 'pack_decisions.json').read_text()) == decisions
    assert not (directory / 'thin_media_report.json').exists()
    report = {'clip_plan_fingerprint': plan_fingerprint(plan), 'keep': True}
    atomic_write_json(directory / 'thin_media_report.json', report)
    subprocess.run(command, cwd=tmp_path, env=env, check=True, capture_output=True, timeout=20)
    assert json.loads((directory / 'thin_media_report.json').read_text()) == report


def test_production_imports_services_not_the_packing_command():
    script_names = {p.stem for p in (ROOT / 'scripts').glob('*.py')}
    for folder in ['src', 'scripts']:
        for path in (ROOT / folder).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                assert not any(m in {'build_clip_plan_thin', 'story_identity'} for m in modules), path
                if folder == 'src':
                    assert not any(m.split('.')[0] in script_names for m in modules), path
