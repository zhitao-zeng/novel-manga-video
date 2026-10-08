import novel_manga.application.profiles as book_profiles
from novel_manga.application.rendering import submission as render_submission
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


from support.render_context import media_runner as runner, video_clip as clip


def test_first_pass_defers_unprepared_clip_before_model_or_video_work(tmp_path,monkeypatch):
    r=runner(tmp_path/'book',local='http://unused.invalid')
    r.context.options.first_pass_only=True
    r.context.options.first_pass_clips={'other'}
    monkeypatch.setattr(r,'generate_clip',lambda *a:pytest.fail('unprepared generation'))
    result=r.process_clip(clip())
    assert result['preparation_deferred'] and result['selected'] is None


def test_alternating_operations_do_not_share_profiles_frame_or_mutable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(book_profiles, 'load_genre', lambda profile: {
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
    b.context.state.blocked_clips['a'] = ['missing image']
    assert not a.context.feedback and not a.context.state.blocked_clips
    assert build_request(a.context, clip(), 1)[0] == first
    assert first['seed_variant'] == 200
    assert 'seed_variant' not in build_request(b.context, clip(), 1)[0]


@pytest.mark.parametrize('outcome', ['success', 'error', 'uncertain'])
def test_slot_release_and_actual_generation_accounting(tmp_path, monkeypatch, outcome):
    r = runner(tmp_path / 'book'); submitted = []; slots = []
    token = object()
    monkeypatch.setattr(render_submission, 'acquire_inflight_slot', lambda *a: slots.append('acquire') or token)
    monkeypatch.setattr(render_submission, 'release_inflight_slot', lambda handle: slots.append('release') if handle is token else pytest.fail('wrong slot'))
    monkeypatch.setattr(render_submission, 'media_duration', lambda path: 5)
    def generate(prompt, image, output, **kwargs):
        submitted.append(kwargs)
        if outcome == 'uncertain': raise SubmissionUncertain('submission may already exist')
        if outcome == 'error': raise RuntimeError('terminal generation error')
        output.write_bytes(b'video'); return output
    r.context.provider = SimpleNamespace(create_video=generate)
    r.context.state.remaining = {'a': 1}
    item = clip()
    if outcome == 'success':
        r.generate_clip(item, 1)
        assert r.context.state.clip(item['clip_id']).generated and r.context.state.remaining['a'] == 0
    else:
        with pytest.raises(RuntimeError): r.generate_clip(item, 1)
        assert not r.context.state.clip(item['clip_id']).generated and r.context.state.remaining['a'] == 1
    assert slots == ['acquire', 'release'] and len(submitted) == 1
    timing = json.loads((r.context.work/'clips/a/attempt_01/generation-timing.json').read_text())
    assert timing['video_present'] == (outcome == 'success')
    assert timing['preparation_s'] >= 0 and timing['admission_wait_s'] >= 0
    assert timing['provider_elapsed_s'] >= 0


def test_cache_only_miss_does_not_touch_existing_movie_report_or_generator(tmp_path, monkeypatch):
    r = runner(tmp_path / 'book'); r.context.options.cache_only = True
    existing = r.context.episode_dir / 'existing.mp4'; existing.write_bytes(b'old movie')
    report = r.context.episode_dir / 'thin_media_report.json'; report.write_text('{"status":"assembled"}')
    r.context.clip_plan = {'clips': [clip()]}
    r.context.provider = SimpleNamespace(create_video=lambda *a, **k: pytest.fail('generation in cache-only'))
    monkeypatch.setattr(r, 'build_assets', lambda *a, **k: pytest.fail('asset build in cache-only'))
    monkeypatch.setattr(r, 'assemble', lambda *a: pytest.fail('assembly with missing clip'))
    result = r.run()
    assert result['status'] == 'cache_miss'
    assert existing.read_bytes() == b'old movie' and report.read_text() == '{"status":"assembled"}'


def test_first_pass_cannot_submit_a_retake_even_when_old_take_no_longer_matches(tmp_path, monkeypatch):
    r = runner(tmp_path/'book')
    r.context.options.first_pass_only = True
    old = r.context.work/'clips/a/attempt_01/clip.mp4'
    old.parent.mkdir(parents=True); old.write_bytes(b'old take')
    monkeypatch.setattr(flow.cache, 'current_video', lambda *a: None)
    r.context.provider = SimpleNamespace(create_video=lambda *a, **k: pytest.fail('retake during first pass'))
    with pytest.raises(ValueError, match='first pass'):
        r.generate_clip(clip(), 2)
    assert old.read_bytes() == b'old take'


def test_first_pass_leaves_existing_clip_audio_and_review_for_later(tmp_path, monkeypatch):
    r = runner(tmp_path/'book'); r.context.options.first_pass_only = True
    old = r.context.work/'clips/a/attempt_01/clip.mp4'
    old.parent.mkdir(parents=True); old.write_bytes(b'existing first video')
    monkeypatch.setattr('novel_manga.story.voice_delivery.postmixed', lambda *a: True)
    monkeypatch.setattr('novel_manga.media.inner_voice.prepare', lambda *a: pytest.fail('post-render audio delayed first video'))
    result = r.process_clip(clip())
    assert result['selected']['video'] == str(old)
    assert result['selected']['passed'] is False and result['review_deferred'] is True


def test_first_pass_cli_uses_existing_take_history_and_never_assembles(tmp_path, monkeypatch, capsys):
    from novel_manga.application.rendering import cli
    from novel_manga.application.repair import history
    from novel_manga.util import atomic_write_json
    r=runner(tmp_path/'book');directory=r.context.episode_dir;book=r.context.novel_dir
    (book/'story_bible.json').write_text(r.context.bible.model_dump_json())
    atomic_write_json(directory/'clip_plan.json',{'clips':[clip()]})
    atomic_write_json(directory/'chapter_script.json',{'shots':[]})
    history.save(directory,{'trials':[],'prior_generation_counts':{'clips':{'a':2}}})
    def generate(self,*,assemble=True):
        assert assemble is False and self.context.options.first_pass_only
        assert self.context.options.workers==1 and self.context.options.max_attempts==1
        assert self.context.state.remaining['a']==1
        p=directory/'work/clips/a/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'first available take')
        return {'status':'clips_ready','clips':[{'clip_id':'a','attempts':[],'selected':{'video':str(p),'passed':True}}],
            'failed_clips':[],'gate_failed_clips':[],'assembly':None,'elapsed_seconds':1}
    monkeypatch.setattr(flow.ThinMediaRunner,'run',generate)
    monkeypatch.setenv('PHANROUTER_API_KEY','test-only-key')
    monkeypatch.setattr(sys,'argv',['render_clips_thin.py','--novel-dir',str(book),'--episode',directory.name,
        '--workers','0','--inflight','6','--first-pass','--max-attempts','1'])
    assert cli.main()==0
    assert history.generation_counts(directory)=={'a':2}
    assert history.load(directory)['trials'][-1]['managed'] is True
    assert not(directory/f'{directory.name}.mp4').exists()
    capsys.readouterr()


def test_clips_only_execution_persists_results_and_leaves_assembly_to_the_caller(tmp_path, monkeypatch):
    r=runner(tmp_path/'book');r.context.options.cache_only=True
    r.context.clip_plan={'clips':[clip()]}
    monkeypatch.setattr(r,'process_clip',lambda c:{'clip_id':c['clip_id'],
        'selected':{'video':'current.mp4','passed':False},'attempts':[]})
    calls=[]
    monkeypatch.setattr(r,'assemble',lambda results:calls.append('assemble') or {'thin_passed':True})
    result=r.run(assemble=False)
    assert not calls and result['status']=='clips_ready' and result['assembly'] is None
    assert result['gate_failed_clips']==['a']
    assert json.loads((r.context.episode_dir/'thin_media_report.json').read_text())==result
    assert r.run()['status']=='assembled' and calls==['assemble']


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
