import ast
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.media.context import RenderContext
from novel_manga.media.generation import build_request
from novel_manga.media.cache import CacheMiss
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.models.bible import Character, StoryBible
from novel_manga.config import Settings
import novel_manga.application.rendering.flow as flow


def runner(tmp_path, *, frame='16:9', local='', extra=None):
    novel = tmp_path; directory = novel / f'{novel.name}_1'; directory.mkdir(parents=True)
    profile = {'style': '2d', 'frame': frame, 'tier': 'quality', **(extra or {})}
    (novel / 'profile.json').write_text(json.dumps(profile))
    (directory / 'chapter_script.json').write_text('{"shots": []}')
    (directory / 'clip_plan.json').write_text('{"clips": []}')
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='国漫', palette='蓝',
                       characters=[Character(name='甲', appearance='黑发', wardrobe='青衣')], locations=['庭院'], style_fingerprint='test')
    return flow.ThinMediaRunner(novel_dir=novel, episode_dir=directory, settings=Settings(local_h3_base_url=local),
                                bible=bible, workers=1, max_attempts=2, profile=profile)


def clip():
    return {'clip_id': 'a', 'kind': 'video', 'prompt': '庭院空镜。', 'prompt_h3': 'An empty courtyard.',
            'request_seconds': 5, 'references': [], 'repair_take': 2}


def test_alternating_operations_do_not_share_profiles_frame_or_mutable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(flow, 'load_genre', lambda profile: {
        'card_style_suffix_3d': 'A-style' if profile['frame'] == '16:9' else 'B-style',
        'location_policy': 'sparse' if profile['frame'] == '16:9' else 'empty',
        'soften': [['独有词', 'A']] if profile['frame'] == '16:9' else []})
    a = runner(tmp_path / 'a', local='http://unused.invalid')
    first = build_request(a.context, clip(), 1)[0]
    b = runner(tmp_path / 'b', frame='9:16')
    assert a.context.frame_spec['width'] == 1920 and b.context.frame_spec['width'] == 1080
    assert a.context.asset_style.card_style_suffix_3d == 'A-style'
    assert b.context.asset_style.card_style_suffix_3d == 'B-style'
    assert len(a.context.softening_rules) == len(b.context.softening_rules) + 1
    b.context.feedback['a'] = '乙的新备注'
    b.context._blocked_clips['a'] = ['missing image']
    assert not a.context.feedback and not a.context._blocked_clips
    assert build_request(a.context, clip(), 1)[0] == first
    assert first['seed_variant'] == 200
    assert 'seed_variant' not in build_request(b.context, clip(), 1)[0]


@pytest.mark.parametrize('outcome', ['success', 'error', 'uncertain'])
def test_slot_release_and_actual_generation_accounting(tmp_path, monkeypatch, outcome):
    r = runner(tmp_path / 'book'); submitted = []; slots = []
    token = object()
    monkeypatch.setattr(flow, 'acquire_inflight_slot', lambda *a: slots.append('acquire') or token)
    monkeypatch.setattr(flow, 'release_inflight_slot', lambda handle: slots.append('release') if handle is token else pytest.fail('wrong slot'))
    monkeypatch.setattr(flow, 'media_duration', lambda path: 5)
    def generate(prompt, image, output, **kwargs):
        submitted.append(kwargs)
        if outcome == 'uncertain': raise SubmissionUncertain('submission may already exist')
        if outcome == 'error': raise RuntimeError('terminal generation error')
        output.write_bytes(b'video'); return output
    r.context.provider = SimpleNamespace(create_video=generate)
    r.context._managed_remaining = {'a': 1}
    item = clip()
    if outcome == 'success':
        r.generate_clip(item, 1)
        assert item['_generated'] and r.context._managed_remaining['a'] == 0
    else:
        with pytest.raises(RuntimeError): r.generate_clip(item, 1)
        assert not item['_generated'] and r.context._managed_remaining['a'] == 1
    assert slots == ['acquire', 'release'] and len(submitted) == 1


def test_cache_only_miss_does_not_touch_existing_movie_report_or_generator(tmp_path, monkeypatch):
    r = runner(tmp_path / 'book'); r.context.cache_only = True
    existing = r.context.episode_dir / 'existing.mp4'; existing.write_bytes(b'old movie')
    report = r.context.episode_dir / 'thin_media_report.json'; report.write_text('{"status":"assembled"}')
    r.context.clip_plan = {'clips': [clip()]}
    r.context.provider = SimpleNamespace(create_video=lambda *a, **k: pytest.fail('generation in cache-only'))
    monkeypatch.setattr(r, 'build_assets', lambda *a, **k: pytest.fail('asset build in cache-only'))
    monkeypatch.setattr(r, 'assemble', lambda *a: pytest.fail('assembly with missing clip'))
    result = r.run()
    assert result['status'] == 'cache_miss'
    assert existing.read_bytes() == b'old movie' and report.read_text() == '{"status":"assembled"}'


def test_media_modules_do_not_import_production_scripts_or_schedule_work():
    root = Path(__file__).resolve().parents[1]
    scripts = {p.stem for p in (root / 'scripts').glob('*.py')}
    for path in (root / 'src/novel_manga/media').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ''] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            assert not any(m.split('.')[0] in scripts or m.startswith('experiments') for m in modules), path
    for path in [root / 'src/novel_manga/media/analysis.py', root / 'src/novel_manga/qc.py']:
        assert 'create_video' not in path.read_text() and 'managed_repair_thin' not in path.read_text()


def test_original_command_dry_run_loads_the_flow_without_generating(tmp_path):
    r = runner(tmp_path / 'book')
    (r.context.novel_dir / 'story_bible.json').write_text(r.context.bible.model_dump_json())
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, 'scripts/render_clips_thin.py', '--novel-dir', str(r.context.novel_dir),
                             '--episode', r.context.episode_dir.name, '--dry-run'], cwd=root,
                            env={'PATH': os.environ.get('PATH', ''),
                                 'PYTHONPATH': f'{root / "src"}:{root / "scripts"}',
                                 'NOVEL_PLANNER_BACKEND': 'deterministic', 'PHANROUTER_API_KEY': 'test-only'},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['canvas'] == '1920x1080' and report['clips'] == []
    assert not (r.context.episode_dir / 'thin_media_report.json').exists()
