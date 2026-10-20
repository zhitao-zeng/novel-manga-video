from types import SimpleNamespace
import json
import pytest
from novel_manga.media import generation, cache
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from support.h3_admission import runner_for, h3_clip, _bound, ENGLISH_BODY


def remote_runner(tmp_path, monkeypatch, clip):
    runner = runner_for(tmp_path, monkeypatch, clip)
    runner.context.settings = SimpleNamespace(local_h3_base_url='', video_model='MiniMax-H3')
    return runner


def test_api_h3_sends_the_prepared_request_without_chinese_director_notes(tmp_path, monkeypatch):
    clip = _bound()
    runner = remote_runner(tmp_path, monkeypatch, clip)
    runner.context.feedback[clip['clip_id']] = '只保留一个人物，不要重读这句说明。'
    request = generation.build_request(runner.context, clip, 2)[0]
    assert request['prompt'] == ENGLISH_BODY
    assert '<d>[Chinese] 你好</d>' in request['prompt']
    assert '【生成目标】' not in request['prompt'] and '导演修正' not in request['prompt']
    assert 'seed_variant' not in request and 'h3_sampling' not in request


def test_api_h3_reuses_an_existing_english_request(tmp_path, monkeypatch):
    clip = _bound()
    runner = remote_runner(tmp_path, monkeypatch, clip)
    request, refs, digests, retry = generation.build_request(runner.context, clip, 1)
    request['prompt'] = ENGLISH_BODY
    assert cache.request_matches(runner.context, clip, request, refs, digests)
    assert not cache.request_matches(runner.context, clip, {**request, 'prompt': clip['prompt']}, refs, digests)


@pytest.mark.parametrize('translation', ['missing', 'unstamped'])
def test_api_h3_never_submits_a_new_unfinished_translation(tmp_path, monkeypatch, translation):
    clip = h3_clip() if translation == 'missing' else _bound()
    runner = remote_runner(tmp_path, monkeypatch, clip)
    with pytest.raises(RuntimeError, match='stale words|no English prompt yet|current English'):
        runner.generate_clip(clip, 1)
    assert not runner.context.state.clip(clip['clip_id']).generated
    assert not (runner.context.work/'clips'/clip['clip_id']/'attempt_01/request.json').exists()


def test_api_h3_cannot_replace_an_unresolved_task_with_changed_inputs(tmp_path, monkeypatch):
    clip = _bound()
    runner = remote_runner(tmp_path, monkeypatch, clip)
    directory = runner.context.work/'clips'/clip['clip_id']/'attempt_01'
    directory.mkdir(parents=True)
    request, refs, digests, retry = generation.build_request(runner.context, clip, 1)
    saved = {**request, 'prompt': 'An earlier different request.'}
    (directory/'request.json').write_text(json.dumps(saved))
    (directory/'clip.mp4.task.json').write_text(json.dumps({'task_id': 'already-accepted'}))
    with pytest.raises(SubmissionUncertain, match='original task is unresolved'):
        runner.generate_clip(clip, 1)
    assert json.loads((directory/'request.json').read_text()) == saved
    assert not runner.context.state.clip(clip['clip_id']).generated


def test_other_remote_model_keeps_its_original_chinese_route(tmp_path, monkeypatch):
    clip = _bound()
    runner = remote_runner(tmp_path, monkeypatch, clip)
    runner.context.settings.video_model = 'sd2.5'
    assert generation.build_request(runner.context, clip, 1)[0]['prompt'] == clip['prompt']


def test_existing_explicit_chinese_h3_cache_opt_out_is_retained(tmp_path, monkeypatch):
    clip = _bound(prompt_h3_skip=True)
    runner = remote_runner(tmp_path, monkeypatch, clip)
    request, refs, digests, retry = generation.build_request(runner.context, clip, 1)
    assert cache.request_matches(runner.context, clip, request, refs, digests)
    assert request['prompt'] == clip['prompt']
