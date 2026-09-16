import packing_service_thin as packing_service
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from novel_manga.story.actions import normalize_actions
from novel_manga.util import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]


def test_production_does_not_import_retired_interfaces_or_experiments():
    for folder in ('src', 'scripts'):
        for path in (ROOT / folder).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                assert not any(m.startswith(('experiments', 'novel_manga.api', 'novel_manga.planner'))
                               for m in modules), path


def test_batch_and_dashboard_import_without_old_web_dependencies():
    code = '''
import sys
class Retired:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'fastapi', 'uvicorn', 'starlette', 'multipart'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, Retired())
import build_bible_thin, plan_chapter_thin, render_clips_thin, status_server
assert 'experiments.legacy.planner' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True,
                   env={**os.environ, 'PYTHONPATH': f'{ROOT / "src"}:{ROOT / "scripts"}'})


def test_scene_extra_precedes_global_alias():
    actions = [{'actor': '甲', 'action': '推开', 'target': '木门'}]
    assert normalize_actions(actions, aliases={'木门': '门主'}, extras=['木门']) == actions


def test_packing_keeps_object_target_without_casting_its_portrait(tmp_path):
    pass
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    directory.mkdir(parents=True)
    atomic_write_json(novel / 'story_bible.json', {'characters': [{'name': '甲'}, {'name': '木门'}]})
    atomic_write_json(novel / 'entity/types.json', {'木门': {'kind': 'object'}})
    script = {'shots': [{'characters': ['甲', '木门'], 'actions': [
        {'actor': '甲', 'action': '推开', 'target': '木门'}], 'turns': [], 'camera': '平视', 'light': '日光'}]}
    shot = packing_service.prepared_shots(script, directory)[0]
    assert shot['characters'] == ['甲']
    assert shot['actions'][0]['target'] == '木门'
