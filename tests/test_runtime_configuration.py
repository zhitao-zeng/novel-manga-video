import json
import os
import subprocess
import sys
from pathlib import Path

from novel_manga.application.configuration import RuntimePaths, config_for_novel, dashboard_novels, environment, h3_translation_endpoint
from novel_manga.application.packing.context import compiler_options


def test_registered_production_configuration_matches_original():
    root = Path(__file__).parents[1]
    pipeline = json.loads((root / 'configs/pipeline.json').read_text())
    expected = json.loads((Path(__file__).parent / 'fixtures/runtime_config_before.json').read_text())
    current = {n['id']: config_for_novel(pipeline, n['id'], root=root) for n in pipeline['novels']}
    assert {name: {k: v for k, v in row.items() if k not in {'executor', 'agent', 'model_environment'}}
            for name, row in current.items()} == expected
    assert all(row['executor'] == 'standard' for row in current.values())


def test_new_novel_uses_one_registration_for_dashboard_and_production(tmp_path):
    pipeline = {'novels': [{'id': 'new-book', 'title': '新书', 'novel_dir': 'books/new', 'tmp_dir': 'runtime/new',
                            'render_keys': [], 'planning': {}, 'ranges': []}]}
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/pipeline.json').write_text(json.dumps(pipeline))
    spec = pipeline['novels'][0]
    board = dashboard_novels(tmp_path)[0]
    run = config_for_novel(pipeline, spec['id'], root=tmp_path)
    assert board['title'] == spec['title']
    assert board['conductor'] == Path(run['tmp_dir']) / 'conductor.log'
    assert RuntimePaths(tmp_path).novel(spec) == tmp_path / 'books/new'


def test_file_environment_preserves_existing_precedence_without_mutation(tmp_path):
    (tmp_path / '.env').write_text('export MODEL="file"\nSECOND=other\n')
    inherited = {'MODEL': 'caller'}
    assert environment(tmp_path, inherited) == {'MODEL': 'caller', 'SECOND': 'other'}
    assert inherited == {'MODEL': 'caller'}


def test_preparation_respects_selected_endpoint_and_shared_slots(tmp_path, monkeypatch):
    from novel_manga.application.configuration import preparation_environment
    (tmp_path/'configs').mkdir()
    (tmp_path/'configs/pipeline.json').write_text(json.dumps({'defaults': {'preparation': {
        'endpoints': ['http://retired/v1'], 'model': 'retired-model'}}}))
    monkeypatch.setenv('NOVEL_PREPARATION_ENDPOINT', 'deepseek')
    resources = '[{"base":"http://shared/v1","slots":8}]'
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', resources)
    env = preparation_environment(tmp_path)
    assert env['QWEN38_LOCAL_MODEL'] == 'DeepSeek-V4.1-Flash'
    assert env['NOVEL_MODEL_RESOURCES'] == resources


def test_h3_endpoint_defaults_and_overrides_are_per_call(monkeypatch):
    for name in ['QWEN38_LOCAL_BASE_URL', 'QWEN38_LOCAL_MODEL', 'QWEN38_LOCAL_API_KEY_VAR']:
        monkeypatch.delenv(name, raising=False)
    first = h3_translation_endpoint()
    assert len(first.endpoints) == 5
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'http://different.invalid/v1')
    monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'alternate')
    second = h3_translation_endpoint()
    assert second.model == 'alternate' and second.endpoints == ('http://different.invalid/v1',)
    assert first.model != second.model and len(first.endpoints) == 5


def test_translation_override_keeps_request_gate_and_final_judge_on_ds(monkeypatch):
    from novel_manga.application import configuration
    from novel_manga.review.endpoints import judge_settings
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'http://172.28.4.52:8831/v1')
    monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'DeepSeek-V4.1-Flash')
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', 'deepseek')
    monkeypatch.setenv('NOVEL_H3_TRANSLATION_ENDPOINT', 'flashnext')
    monkeypatch.delenv('NOVEL_H3_REQUEST_CHECK_ENDPOINT', raising=False)
    before = dict(os.environ)
    assert configuration.h3_translation_endpoint().model == 'Qwen3.8-Flash-Next'
    assert configuration.h3_request_check_endpoint().model == 'DeepSeek-V4.1-Flash'
    assert judge_settings().model == 'DeepSeek-V4.1-Flash'
    assert dict(os.environ) == before
    monkeypatch.setenv('NOVEL_H3_REQUEST_CHECK_ENDPOINT', 'flashnext')
    assert configuration.h3_request_check_endpoint().model == 'Qwen3.8-Flash-Next'


def test_importing_translation_does_not_set_process_model_environment():
    root = Path(__file__).parents[1]
    subprocess.run([sys.executable, '-c',
                    "import os; import build_h3_prompts; assert not any(k.startswith('QWEN38_') for k in os.environ)"],
                   cwd=root, env={'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': 'src:scripts'},
                   check=True, capture_output=True)


def test_compiler_duration_options_follow_each_explicit_environment():
    a = compiler_options(environ={'NOVEL_CLIP_SECONDS_MAX': '15'})
    b = compiler_options(environ={'NOVEL_CLIP_SECONDS_MAX': '30'})
    assert (a.max_clip_seconds, a.max_stages, a.soft_cut_seconds) == (15, 3, 9)
    assert (b.max_clip_seconds, b.max_stages, b.soft_cut_seconds) == (30, 6, 18)
    assert compiler_options(environ={'NOVEL_CLIP_SECONDS_MAX': '15'}) == a
