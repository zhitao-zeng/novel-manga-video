from novel_manga.application.agents.episode.media_operations import MediaOperations
"""Prepared-script fixture shared by episode agent integration tests."""
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.agents.episode import contracts
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json, read_json
from support.episode_agent import CATALOG, SOURCES, episode


def prepared(tmp_path, monkeypatch, *, old_english=True):
    run = tmp_path / 'run'
    book = run / 'test-book'
    directory = book / 'test-book_7'
    work = run / 'sandbox'
    directory.mkdir(parents=True)
    (work / 'output').mkdir(parents=True)
    original = tmp_path / 'original/test-book_7'
    original.mkdir(parents=True)
    ctx = SimpleNamespace(project_root=Path(__file__).resolve().parents[2],
                          source_novel_dir=original.parent, source_episode_dir=original,
                          run_dir=run, novel_dir=book, episode_dir=directory, work_dir=work,
                          input_mode='script', target_seconds=120, clip_seconds_max=15)
    script, plan = contracts.adopt(episode(), catalog=CATALOG, source_segments=SOURCES, profile={})
    script['author_extra'] = {'approved': ['retain source and camera']}
    plan['original_bookkeeping'] = {'keep': True}
    prompts = {}
    for clip in plan['clips']:
        clip['generation_seed'] = 1234
        clip['prompt'] = '旧中文请求保留作元数据。'
        prompts[clip['clip_id']] = clip['prompt_h3'].replace('relaxed grip', 'firm grip')
        if not old_english:
            clip.pop('prompt_h3')
            clip.pop('prompt_h3_of')
    for target in (directory, original):
        atomic_write_json(target / 'chapter_script.json', script)
        atomic_write_json(target / 'clip_plan.json', plan)
    atomic_write_json(directory / 'review_feedback.json', {})
    atomic_write_json(book / 'profile.json', {})
    atomic_write_json(work / 'output/requests.json', {'prompts': prompts})

    class Runner:
        def __init__(self):
            self.context = SimpleNamespace(episode_dir=directory)

        def run(self, *, assemble=True):
            return {'status': 'assembled', 'clips': [], 'failed_clips': [],
                    'gate_failed_clips': [], 'assembly': {'thin_passed': True}}

    monkeypatch.setattr(MediaOperations, 'runner', lambda self: Runner())
    monkeypatch.setattr('novel_manga.application.review.execution.review_saved',
                        lambda directory, **kw: read_json(directory/'episode_review.json', {}))
    return ctx, plan, prompts
