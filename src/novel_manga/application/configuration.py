"""Existing runtime settings assembled without modifying the process environment."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
from novel_manga.llm.config import ENDPOINTS, JsonEndpoint, endpoint_settings

EXECUTORS = ('standard', 'episode_agent')


def validate_agent_h3(keys):
    keys = list(keys)
    if not keys:
        raise ValueError('episode_agent requires an assigned H3 render resource')
    if any(not (key.get('model') == 'MiniMax-H3' and not key.get('base_url'))
           and (not key.get('base_url') or not str(key.get('model', '')).startswith('minimax-h3'))
           for key in keys):
        raise ValueError('episode_agent supports H3 render resources only; SD cannot use this executor')


def project_root() -> Path:
    return Path(os.environ.get('NOVEL_PROJECT_ROOT') or Path(__file__).resolve().parents[3]).resolve()


def speaker_model_path(root: Path) -> Path:
    return Path(os.environ.get('NOVEL_SPEAKER_MODEL') or root.parent / 'models/speaker/campplus_sv_zh-cn.onnx')


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    temporary: Path = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'temporary', Path(os.environ.get('NOVEL_TMP_DIR') or self.root.parent / 'tmp'))

    def novel(self, spec):
        return (self.root / spec.get('novel_dir', f"outputs/{spec['id']}")).resolve()

    def production(self, spec):
        return (self.root / spec.get('tmp_dir', str(self.temporary / f"conductor-{spec['id']}"))).resolve()


def pipeline_config(root: Path) -> dict:
    return json.loads((root / 'configs/pipeline.json').read_text(encoding='utf-8'))


def dashboard_novels(root: Path) -> list[dict]:
    paths = RuntimePaths(root)
    novels = pipeline_config(root).get('novels', [])
    indexed = sorted(enumerate(novels), key=lambda item: item[1].get('display_order', item[0]))
    return [{'id': n['id'], 'title': n.get('title', n['id']),
             'conductor': paths.production(n) / 'conductor.log' if n.get('conductor_log', True) else None}
            for _, n in indexed]


def environment(root: Path, base: dict | None = None) -> dict:
    result = dict(os.environ if base is None else base)
    path = root / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            result.setdefault(key.strip().removeprefix('export ').strip(), value.strip().strip("'\""))
    return result


def _h3_preparation_endpoint(variable: str) -> JsonEndpoint:
    name = str(os.environ.get(variable) or '').strip()
    if name:
        return endpoint_settings(name)
    defaults = ENDPOINTS['phanthy-ds41']
    return JsonEndpoint.from_env({**defaults, **os.environ})


def h3_translation_endpoint() -> JsonEndpoint:
    return _h3_preparation_endpoint('NOVEL_H3_TRANSLATION_ENDPOINT')


def h3_request_check_endpoint() -> JsonEndpoint:
    return _h3_preparation_endpoint('NOVEL_H3_REQUEST_CHECK_ENDPOINT')


def repair_settings(root: Path) -> dict:
    config = pipeline_config(root)
    return {'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool', 'pool': 'h3pool', 'clip_cap': 15,
            'inflight': 24, 'review_mode': 'verify', **config.get('defaults', {}).get('repair', {})}


def repair_environment(root: Path, legacy: Path) -> tuple[dict, dict]:
    settings = repair_settings(root)
    env = {**environment(root), **model_resources_environment(root)}
    env.setdefault('PYTHONPATH', 'src')
    env.update(NOVEL_VIDEO_MODEL=settings['model'],
               NOVEL_LOCAL_H3_URL=settings['base_url'], NOVEL_INFLIGHT_POOL=settings['pool'],
               NOVEL_REVIEW_MODE=settings['review_mode'],
               NOVEL_INFLIGHT_DIR=str(legacy.parent / 'inflight' / settings['pool']),
               NOVEL_CLIP_SECONDS_MAX=str(settings['clip_cap']), PHANROUTER_VIDEO_KEY_VAR='')
    return env, settings


def model_resources_environment(root: Path, pipeline: dict | None = None) -> dict:
    """Every endpoint in a comma-separated resource shares that resource's global cap."""
    pipeline = pipeline_config(root) if pipeline is None else pipeline
    resources = [{'base': row['base'], 'slots': int(row.get('slots', 1))}
                 for row in pipeline.get('resources', {}).get('planning_models', {}).values()
                 if row.get('base')]
    return {'NOVEL_MODEL_RESOURCES': json.dumps(resources, separators=(',', ':')),
            'NOVEL_MODEL_INFLIGHT_DIR': str(RuntimePaths(root).temporary / 'model-inflight')}


def episode_agent_settings(root: Path, spec: dict) -> dict:
    settings = {'input_mode': 'auto', 'parallel': 1, 'workers': 2, **spec.get('agent', {})}
    if settings['input_mode'] not in {'auto', 'source', 'script'}:
        raise ValueError('agent.input_mode must be auto, source or script')
    if int(settings['parallel']) < 1 or int(settings['workers']) < 1:
        raise ValueError('agent.parallel and agent.workers must be positive')
    settings['parallel'], settings['workers'] = int(settings['parallel']), int(settings['workers'])
    if 'production_parallel' in settings:
        settings['production_parallel']=int(settings['production_parallel'])
        if settings['production_parallel']<1:
            raise ValueError('agent.production_parallel must be positive')
    if settings.get('asset_manifest'):
        settings['asset_manifest']=str((root/settings['asset_manifest']).resolve())
    if settings.get('first_pass_priority') and not settings.get('production_parallel'):
        raise ValueError('agent.first_pass_priority requires independent production_parallel capacity')
    if 'task_pool_size' in settings:
        settings['task_pool_size']=int(settings['task_pool_size'])
        if settings['task_pool_size']<1:raise ValueError('agent.task_pool_size must be positive')
    if 'normal_parallel' in settings:
        settings['normal_parallel'] = int(settings['normal_parallel'])
        if not 0 <= settings['normal_parallel'] <= settings['parallel']:
            raise ValueError('agent.normal_parallel must fit within agent.parallel')
    settings['output_root'] = str((root / settings.get('output_root', 'outputs/episode-agent')).resolve())
    if settings.get('config'):
        settings['config'] = str((root / settings['config']).resolve())
    return settings


def config_for_novel(pipeline: dict, novel_id: str, *, root: Path | None = None,
                     executor: str | None = None) -> dict:
    """Build one novel's conductor config out of the shared pipeline description."""
    paths = RuntimePaths(root or project_root())
    novels = {n["id"]: n for n in pipeline.get("novels", [])}
    if novel_id not in novels:
        raise SystemExit(f"{novel_id} is not in the pipeline file: {sorted(novels)}")
    novel = novels[novel_id]
    resources = pipeline.get("resources", {})
    video = resources.get("video_keys", {})
    models = resources.get("planning_models", {})
    missing = [k for k in novel.get("render_keys", []) if k not in video] + \
              [m for m in novel.get("planning", {}) if m not in models]
    if missing:
        raise SystemExit(f"{novel_id} asks for resources that are not defined: {missing}")
    defaults = pipeline.get("defaults", {})
    selected = executor or novel.get('executor', 'standard')
    if selected not in EXECUTORS:
        raise ValueError(f'executor must be one of {EXECUTORS}')
    planning = {**defaults.get("planning", {}),
                "blocks_max": novel.get("blocks_max", 0), "blocks_min": novel.get("blocks_min", 0),
                "models": [{**models[name], "slots": slots} for name, slots in novel.get("planning", {}).items()]}
    return {
        "novel_dir": novel.get("novel_dir", f"outputs/{novel_id}"),
        "tmp_dir": str(paths.production(novel)),
        "tick_seconds": novel.get("tick_seconds", pipeline.get("tick_seconds", 90)),
        "round_gap_seconds": novel.get("round_gap_seconds", pipeline.get("round_gap_seconds", 120)),
        "keys": [{"name": name, **video[name]} for name in novel.get("render_keys", [])],
        "ranges": novel["ranges"],
        "planning": planning,
        "qwen": defaults.get("qwen", {}),
        "aimd": defaults.get("aimd", {}),
        "review": {**defaults.get("review", {}), **novel.get('review',{})},
        "render": defaults.get("render", {}),
        "executor": selected,
        **({'workspace_index':str((paths.root/novel['workspace_index']).resolve())} if novel.get('workspace_index') else {}),
        "agent": episode_agent_settings(paths.root, novel),
        "model_environment": model_resources_environment(paths.root, pipeline),
        **({"stop_on_quota_exhausted": True} if novel.get("stop_on_quota_exhausted") else {}),
    }


def preparation_environment(root: Path) -> dict:
    settings = pipeline_config(root).get('defaults', {}).get('preparation', {})
    env = {**model_resources_environment(root), **environment(root)}
    defaults = h3_translation_endpoint()
    env.update(QWEN38_LOCAL_BASE_URL=','.join(settings.get('endpoints', defaults.endpoints)),
               QWEN38_LOCAL_MODEL=settings.get('model', 'Qwen3.8-27B-Project'),
               QWEN38_LOCAL_API_KEY_VAR=settings.get('key_var', 'H3_PROMPT_NO_KEY'),
               NOVEL_CLIP_SECONDS_MAX=str(settings.get('clip_cap', 15)))
    selected = str(env.get('NOVEL_PREPARATION_ENDPOINT') or '').strip()
    if selected:
        if selected not in ENDPOINTS:
            raise ValueError(f'unknown preparation endpoint {selected}; pick one of {sorted(ENDPOINTS)}')
        env.update(ENDPOINTS[selected])
        endpoint = JsonEndpoint.from_env(env)
        env.update(NOVEL_LLM_BASE_URL=endpoint.endpoints[0], NOVEL_LLM_MODEL=endpoint.model,
                   NOVEL_LLM_API_KEY=endpoint.key)
    return env
