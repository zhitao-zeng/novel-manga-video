import novel_manga.application.rendering.submission as owner_submission
from novel_manga.application.rendering import submission as render_submission
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.render_context import uninitialized_runner
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.rendering import flow, reviewed
from novel_manga.media import generation
from novel_manga.util import read_json




def add_trial(directory, plan):
    state = history.load(directory)
    state['trials'].append({'id': len(state['trials']) + 1, 'managed': True, 'clips': ['c'],
                            'expected_plan': plan_fingerprint(plan), 'expected_notes': {}, 'renders': []})
    history.save(directory, state)
    return state['trials'][-1]['id']


@pytest.fixture
def episode(tmp_path, monkeypatch):
    directory = tmp_path / 'book' / 'book_1'
    directory.mkdir(parents=True)
    clip = {'clip_id': 'c', 'kind': 'video', 'prompt': 'The actor stands beside the table.',
            'request_seconds': 5, 'references': [], 'spoken_text': '好吧。'}
    plan = {'clips': [clip]}
    (directory / 'clip_plan.json').write_text(json.dumps(plan))
    (directory / 'chapter_script.json').write_text('{"shots": []}')
    add_trial(directory, plan)
    monkeypatch.setattr(render_submission, 'acquire_inflight_slot', lambda *a: object())
    monkeypatch.setattr(render_submission, 'release_inflight_slot', lambda *a: None)
    monkeypatch.setattr(owner_submission, 'wait_for_inflight_redraws', lambda *a: None)
    monkeypatch.setattr(render_submission, 'media_duration', lambda *a: 5.0)
    monkeypatch.setattr(flow, 'record_privacy_ok', lambda *a: None)
    from novel_manga.application.preparation import readiness
    monkeypatch.setattr(readiness, 'plan_issues', lambda *a: {})
    monkeypatch.setattr(readiness, 'reference_issues', lambda *a: [])
    monkeypatch.setattr(readiness, 'render_risk_report', lambda *a: {})
    monkeypatch.setattr(readiness, 'save_check', lambda *a: None)
    return directory, clip, plan


def runner(directory, clip, provider):
    r = uninitialized_runner()
    r.context.episode_dir = directory
    r.context.novel_dir = directory.parent
    r.context.work = directory / 'work'
    r.context.settings = SimpleNamespace(local_h3_base_url=None)
    r.context.clip_plan = {'clips': [copy.deepcopy(clip)]}
    r.context.script = {'shots': []}
    r.context.provider = SimpleNamespace(create_video=provider)
    r.context.state.remaining = {'c': max(0, 3 - history.generation_counts(directory).get('c', 0))}
    r.build_assets = lambda *a, **kw: None
    r.english_correction_for_new_take = lambda *a: False
    r.assemble = lambda *a: {'final_video': str(directory / 'candidate.mp4')}
    r.analyse_clip = lambda c, video: {'video': str(video), 'duration': 5.0, 'cer': 0.0,
                                      'max_volume_db': -10.0, 'issues': [], 'passed': True}
    return r


def task(output, task_id):
    output.with_suffix(output.suffix + '.task.json').write_text(json.dumps({'task_id': task_id}))


def completed_provider(calls, task_id='task-1'):
    def provider(prompt, image, output, **kwargs):
        calls.append(task_id)
        task(output, task_id)
        output.write_bytes(b'generated video')
        return output
    return provider

