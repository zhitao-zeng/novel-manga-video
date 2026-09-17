"""Existing runtime settings assembled without modifying the process environment."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
from novel_manga.llm.config import JsonEndpoint


def project_root() -> Path:
    return Path(os.environ.get('NOVEL_PROJECT_ROOT') or Path(__file__).resolve().parents[3]).resolve()


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


def h3_translation_endpoint() -> JsonEndpoint:
    defaults = {'QWEN38_LOCAL_BASE_URL': ','.join(f'http://127.0.0.1:{p}/v1' for p in range(18120, 18125)),
                'QWEN38_LOCAL_MODEL': 'Qwen3.8-27B-Project', 'QWEN38_LOCAL_API_KEY_VAR': 'H3_PROMPT_NO_KEY'}
    return JsonEndpoint.from_env({**defaults, **os.environ})


def repair_settings(root: Path) -> dict:
    config = pipeline_config(root)
    return {'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool', 'pool': 'h3pool', 'clip_cap': 15,
            'inflight': 24, 'review_mode': 'verify', **config.get('defaults', {}).get('repair', {})}


def repair_environment(root: Path, legacy: Path) -> tuple[dict, dict]:
    settings = repair_settings(root)
    env = environment(root)
    env.update(PYTHONPATH='src:scripts', NOVEL_VIDEO_MODEL=settings['model'],
               NOVEL_LOCAL_H3_URL=settings['base_url'], NOVEL_INFLIGHT_POOL=settings['pool'],
               NOVEL_REVIEW_MODE=settings['review_mode'],
               NOVEL_INFLIGHT_DIR=str(legacy.parent / 'inflight' / settings['pool']),
               NOVEL_CLIP_SECONDS_MAX=str(settings['clip_cap']), PHANROUTER_VIDEO_KEY_VAR='')
    return env, settings


def config_for_novel(pipeline: dict, novel_id: str, *, root: Path | None = None) -> dict:
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
    planning = {**defaults.get("planning", {}),
                "blocks_max": novel.get("blocks_max", 0), "blocks_min": novel.get("blocks_min", 0),
                "models": [{**models[name], "slots": slots} for name, slots in novel.get("planning", {}).items()]}
    return {
        "novel_dir": novel.get("novel_dir", f"outputs/{novel_id}"),
        "tmp_dir": str(paths.production(novel)),
        "tick_seconds": pipeline.get("tick_seconds", 90),
        "round_gap_seconds": pipeline.get("round_gap_seconds", 120),
        "keys": [{"name": name, **video[name]} for name in novel.get("render_keys", [])],
        "ranges": novel["ranges"],
        "planning": planning,
        "qwen": defaults.get("qwen", {}),
        "aimd": defaults.get("aimd", {}),
        "review": defaults.get("review", {}),
        "render": defaults.get("render", {}),
    }
