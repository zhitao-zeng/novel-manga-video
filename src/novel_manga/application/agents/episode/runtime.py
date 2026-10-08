"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os

from novel_manga.application.configuration import environment, repair_environment, repair_settings, RuntimePaths
from novel_manga.llm.config import ENDPOINTS
from novel_manga.util import read_json


@contextmanager
def process_environment(values):
    """This sequential command owns its environment until its host jobs have settled."""
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def runtime_environment(ctx, config):
    env = environment(ctx.project_root)
    pipeline = read_json(ctx.project_root / 'configs/pipeline.json', {})
    review_mode = str(env.get('NOVEL_REVIEW_MODE') or '').strip()
    if not review_mode:
        profile = read_json(ctx.novel_dir/'profile.json', None)
        if profile is None:
            profile = read_json(ctx.source_novel_dir/'profile.json', None)
        if profile is None:
            metadata = read_json(ctx.novel_dir/'novel.json', None)
            if metadata is None:
                metadata = read_json(ctx.source_novel_dir/'novel.json', {})
            profile = metadata.get('profile', {})
        review_mode = str(profile.get('review_mode') or repair_settings(ctx.project_root)['review_mode'])
    if not env.get('NOVEL_LOCAL_H3_URL', '').strip():
        env, settings = repair_environment(ctx.project_root, ctx.run_dir)
        # The pool belongs to the service, not this episode's sandbox directory.
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('pool') == settings['pool']), {})
        env['NOVEL_INFLIGHT_DIR'] = str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / settings['pool'])
        env.setdefault('NOVEL_RENDER_INFLIGHT', str((key.get('inflight') or {}).get('max', settings['inflight'])))
    elif not env.get('NOVEL_RENDER_INFLIGHT'):
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('base_url') == env['NOVEL_LOCAL_H3_URL']), {})
        env['NOVEL_RENDER_INFLIGHT'] = str((key.get('inflight') or {}).get('max', 24))
        env.setdefault('NOVEL_INFLIGHT_DIR', str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / (key.get('pool') or 'h3pool')))
    # Review policy follows configuration even when the H3 endpoint is explicit.
    env['NOVEL_REVIEW_MODE'] = review_mode
    from novel_manga.application.configuration import model_resources_environment
    for name, value in model_resources_environment(ctx.project_root, pipeline).items():
        env.setdefault(name, value)
    base = config['base_url'].rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    defaults = {'QWEN38_LOCAL_BASE_URL': base, 'QWEN38_LOCAL_MODEL': config['model'],
                'QWEN38_LOCAL_API_KEY_VAR': config['key_var'], 'QWEN38_LOCAL_STREAM': '0'}
    for key, value in defaults.items():
        env[key] = os.environ.get(key) or value
    preset = next((name for name, values in ENDPOINTS.items()
                   if all(values.get(key) == env[key] for key in
                          ('QWEN38_LOCAL_BASE_URL', 'QWEN38_LOCAL_MODEL',
                           'QWEN38_LOCAL_API_KEY_VAR'))), None)
    preset = preset or os.environ.get('SECOND_REVIEW_JUDGE') or os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE')
    if preset is None:
        raise ValueError('The configured sandbox endpoint has no existing QA preset; '
                         'set SECOND_REVIEW_JUDGE and NOVEL_REVIEW_VIDEO_JUDGE to existing presets.')
    from novel_manga.llm.config import TRANSPORT_ENV
    transport = ENDPOINTS.get(preset, {})
    env.update({name:transport[name] for name in TRANSPORT_ENV if name in transport})
    env['SECOND_REVIEW_JUDGE'] = os.environ.get('SECOND_REVIEW_JUDGE') or preset
    env['NOVEL_REVIEW_VIDEO_JUDGE'] = os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE') or preset
    from novel_manga.llm.config import endpoint_settings
    resources = None
    for field, variable in (('h3_translation_endpoint', 'NOVEL_H3_TRANSLATION_ENDPOINT'),
                            ('h3_request_check_endpoint', 'NOVEL_H3_REQUEST_CHECK_ENDPOINT')):
        selected = str(config.get(field, env.get(variable)) or '').strip()
        if field in config or variable in env:
            env[variable] = selected
        if not selected:
            continue
        endpoint = endpoint_settings(selected)
        # A preparation role using the primary endpoint retains its existing
        # shared capacity; only the separate helper model gets this new cap.
        if base.removesuffix('/v1') in {value.rstrip('/').removesuffix('/v1') for value in endpoint.endpoints}:
            continue
        slots = int(config.get('h3_preparation_parallel', 2))
        if slots <= 0:
            raise ValueError('h3_preparation_parallel must be positive')
        if resources is None:
            resources = json.loads(env.get('NOVEL_MODEL_RESOURCES') or '[]')
        target = ','.join(endpoint.endpoints)
        existing = next((row for row in resources if row['base'].rstrip('/') == target.rstrip('/')), None)
        if existing is None:
            resources.append({'base': target, 'slots': slots})
        else:
            existing['slots'] = min(int(existing['slots']), slots)
    if resources is not None:
        env['NOVEL_MODEL_RESOURCES'] = json.dumps(resources, separators=(',', ':'))
    env['NOVEL_PROJECT_ROOT'] = str(ctx.project_root)
    env['NOVEL_CLIP_SECONDS_MAX'] = str(ctx.clip_seconds_max)
    return env
