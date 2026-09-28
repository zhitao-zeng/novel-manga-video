import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.production import batch_cli, execution, flow, render, reports, runs
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.review import execution as review_execution
from novel_manga.util import atomic_write_json, read_json


def batch_for(tmp_path, monkeypatch, *, status='pending', **options):
    directory = tmp_path / 'book/book_1'
    directory.mkdir(parents=True)
    plan = {'clips': [{'clip_id': 'a', 'kind': 'video', 'prompt': 'existing scene', 'request_seconds': 10}]}
    media = {'clips': [{'clip_id': 'a'}], 'assembly': {'thin_passed': True},
             'clip_plan_fingerprint': plan_fingerprint(plan), 'review_feedback': {}}
    atomic_write_json(directory / 'clip_plan.json', plan)
    atomic_write_json(directory / 'chapter_script.json', {})
    atomic_write_json(directory / 'thin_media_report.json', media)
    args = dict(unattended=False, review_only=False, no_render=False, dry_run=False,
                cache_only=False, rerender=False, retake_failed=False, workers=4, inflight=8,
                tier='quality', prescreen=False, moderation_repair=True, prune=False, stage='render')
    args.update(options)
    batch = object.__new__(flow.Batch)
    batch.args = SimpleNamespace(**args)
    batch.novel_dir, batch.novel_id, batch.rows = directory.parent, 'book', {1: {}}
    batch.fast = False
    batch.reviewing = args['unattended'] or args['review_only']
    batch.commands = []
    batch.render_status = lambda ch: status
    batch.prepare_cards = lambda ch: None
    def run(command, log):
        batch.commands.append(command)
        return 0, ''
    batch.run = run
    from novel_manga.application.packing import ranges
    from novel_manga.application.preparation import readiness
    monkeypatch.setattr(ranges, 'repair_episode', lambda *a, **kw: {'changed': [], 'skipped': []})
    monkeypatch.setattr(readiness, 'inspect_episode', lambda *a, **kw: (plan, {}))
    monkeypatch.setattr(readiness, 'save_check', lambda *a, **kw: None)
    monkeypatch.delenv('NOVEL_LOCAL_H3_URL', raising=False)
    monkeypatch.delenv('NOVEL_CLIP_SECONDS_MAX', raising=False)
    return batch, directory, plan, media


@pytest.mark.parametrize('fast', [False, True])
def test_unattended_batch_calls_shared_reviewed_renderer_once_even_when_clips_fail(tmp_path, monkeypatch, fast):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status='clips_failed', unattended=True)
    batch.fast = fast
    batch.plan = lambda *a, **k: pytest.fail('no extra whole-chapter replan outside the shared engine')
    batch.review_episode = lambda *a: pytest.fail('no second review after render --review')
    if fast:
        from novel_manga.application.packing import single_card
        monkeypatch.setattr(single_card, 'repair_missing_expressions', lambda *a: {'changed': [], 'retained': []})
        monkeypatch.setattr(single_card, 'single_card_plan', lambda *a: [])
    for _ in range(3):
        runs.count_run(directory)
    counter = (directory / runs.RUNS_FILE).read_bytes()
    render.render(batch, 1)
    assert len(batch.commands) == 1
    assert '--review' in batch.commands[0] and '--repair' not in batch.commands[0]
    assert batch.commands[0][batch.commands[0].index('--inflight') + 1] == '8'
    assert (directory / runs.RUNS_FILE).read_bytes() == counter
    assert not (directory / '.render.lock').exists()


def test_existing_unattended_episode_enters_shared_repair_and_does_not_append_old_feedback(tmp_path, monkeypatch):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status='done', unattended=True)
    atomic_write_json(directory / 'review_feedback.json', {})
    before = (directory / 'review_feedback.json').read_bytes()
    monkeypatch.setattr(execution, 'review_only', lambda *a: pytest.fail('repair command owns its review'))
    render.render(batch, 1)
    assert len(batch.commands) == 1 and '--repair' in batch.commands[0]
    assert '--review' not in batch.commands[0]
    assert (directory / 'review_feedback.json').read_bytes() == before
    assert not (directory / '.render.lock').exists()


@pytest.mark.parametrize('status', ['done', 'stale', 'clips_failed', 'pending', 'plan_blocked'])
def test_review_only_never_renders_or_prepares_even_for_stale_or_partial_episodes(tmp_path, monkeypatch, status):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status=status,
                                             unattended=True, review_only=True, rerender=True)
    batch.prepare_cards = lambda *a: pytest.fail('review-only cannot rebuild assets')
    seen = []
    def review(path):
        seen.append(path)
        atomic_write_json(path / 'episode_review.json', {'flags': ['a: wrong'], 'feedback': {'a': 'wrong'}})
    monkeypatch.setattr(execution, 'review_only', review)
    render.render(batch, 1)
    assert seen == [directory] and not batch.commands
    assert batch.rows[1]['review_flags'] == ['a: wrong']
    assert not (directory / runs.RUNS_FILE).exists()


def test_review_only_cli_overrides_default_all_and_unattended_without_planning(tmp_path, monkeypatch):
    observed = []
    def make_batch(args):
        observed.append(copy.deepcopy(vars(args)))
        return SimpleNamespace(novel_dir=tmp_path, novel_id='book', source=tmp_path / 'novel.txt',
                               args=args, rows={}, render_status=lambda ch: 'done')
    monkeypatch.setattr(flow, 'Batch', make_batch)
    monkeypatch.setattr(render, 'render', lambda batch, ch: observed.append(('review', ch)))
    monkeypatch.setattr(reports, 'report', lambda *a: 0)
    monkeypatch.setattr(batch_cli.pc_audit, 'blocking_problems', lambda *a: pytest.fail('review-only is not render preparation'))
    monkeypatch.setattr(batch_cli, 'load_dotenv', lambda *a: None)
    monkeypatch.delenv('NOVEL_LOCAL_H3_URL', raising=False)
    monkeypatch.setattr(sys, 'argv', ['thin_batch.py', '--novel-dir', str(tmp_path), '--chapters', '1',
                                     '--review-only', '--unattended'])
    assert batch_cli.main() == 0
    assert observed[0]['stage'] == 'render' and observed[0]['no_render']
    assert not observed[0]['unattended']
    assert observed[1:] == [('review', 1)]


def test_render_only_does_not_enter_content_review_or_replan_the_chapter(tmp_path, monkeypatch):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status='clips_failed')
    batch.plan = lambda *a, **k: pytest.fail('render-only cannot replan a chapter')
    batch.review_episode = lambda *a: pytest.fail('render-only cannot start content review')
    batch.moderation_blocked = lambda *a: True
    render.render(batch, 1)
    assert batch.commands and all('--review' not in c and '--repair' not in c for c in batch.commands)


def test_cache_only_unattended_passes_cache_only_to_the_shared_operation(tmp_path, monkeypatch):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, unattended=True, cache_only=True, rerender=True)
    batch.prepare_cards = lambda *a: pytest.fail('cache-only cannot rebuild assets')
    render.render(batch, 1)
    assert len(batch.commands) == 1
    assert {'--review', '--cache-only'} <= set(batch.commands[0])
    assert not (directory / runs.RUNS_FILE).exists()


def test_existing_reviewed_repair_respects_the_episode_lock(tmp_path, monkeypatch):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status='done', unattended=True)
    lock = directory / '.render.lock'
    lock.write_text('123')
    monkeypatch.setattr(execution.common, 'pid_alive', lambda pid: pid == 123)
    render.render(batch, 1)
    assert not batch.commands and lock.read_text() == '123'
    assert 'locked' in batch.rows[1]['note']


def test_batch_cannot_report_success_when_shared_reviewed_execution_failed(tmp_path, monkeypatch):
    batch, directory, plan, media = batch_for(tmp_path, monkeypatch, status='done', unattended=True)
    batch.title, batch.card_review = 'book', {}
    batch.args.chapters = '1'
    batch.rows[1] = {'render': 'done', 'thin_passed': True,
                     'quality_review': {'passed': False, 'remaining': ['a']}}
    monkeypatch.setattr(reports, 'delivery_report', lambda *a: None)
    assert reports.report(batch, [1]) == 2
    batch.rows[1]['quality_review']['passed'] = True
    assert reports.report(batch, [1]) == 0
