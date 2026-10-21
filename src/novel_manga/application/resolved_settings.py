"""Resolve operation settings once, preserving the existing source precedence."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import copy
import json
import os

from . import profiles
from .configuration import (
    RuntimePaths, environment, model_resources_environment, repair_environment, repair_settings,
)
from novel_manga.llm.config import ENDPOINTS, TRANSPORT_ENV, endpoint_settings
from novel_manga.util import read_json


@dataclass(frozen=True)
class ResolvedBookSettings:
    profile: dict
    frame: dict
    effective: dict
    genre: dict
    style: dict


def resolve_book_settings(novel_dir: Path, *, supplied_profile=None, overrides=None,
                          script=None) -> ResolvedBookSettings:
    """CLI overrides remain authoritative; an explicit renderer profile remains unvalidated."""
    overrides = overrides or {}
    profile = copy.deepcopy(supplied_profile) if supplied_profile else profiles.load_profile(novel_dir, **overrides)
    if script is not None:
        method = script.get('story_method') or {}
        if method:
            profile.update(script.get('profile') or {})
            profile.update({key:value for key,value in overrides.items() if value})
            profile['story_method'] = method['id']
        else:
            profile.pop('story_method', None)
    return ResolvedBookSettings(profile, dict(profiles.frame_spec(profile)),
                               profiles.effective_profile(profile), profiles.load_genre(profile),
                               profiles.load_style(profile, novel_dir))


@dataclass(frozen=True)
class RecordedPlanSettings:
    overrides: dict
    limits: dict
    two_views: str | None


def recorded_plan_settings(plan: dict) -> RecordedPlanSettings:
    """Recorded limits win when rebuilding a plan; book defaults only fill omitted fields."""
    profile = (plan.get('totals') or {}).get('profile') or {}
    saved = plan.get('limits') or {}
    cap = float(saved.get('max_clip_seconds') or (15 if '-15s' in plan.get('policy', '') else 30))
    limits = {'max_clip_seconds':cap,
              'max_stages':int(saved.get('max_stages') or (3 if cap <= 15 else 6)),
              'soft_cut_seconds':float(saved.get('soft_cut_seconds') or cap * .6),
              'camera_policy':saved.get('camera_policy', 'authored')}
    return RecordedPlanSettings({key:profile.get(key) for key in ('style','frame','tier')},
                                limits, profile.get('two_views'))



def resolve_agent_environment(ctx, config):
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
    transport = ENDPOINTS.get(preset, {})
    env.update({name:transport[name] for name in TRANSPORT_ENV if name in transport})
    env['SECOND_REVIEW_JUDGE'] = os.environ.get('SECOND_REVIEW_JUDGE') or preset
    env['NOVEL_REVIEW_VIDEO_JUDGE'] = os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE') or preset
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
