"""Read a tool input within the existing episode workspace."""
import json
from pathlib import Path


def input_file(ctx, payload, default):
    value = payload.get('file', default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('file requires a workspace path')
    path = Path(value)
    if path.is_relative_to('/work'):
        path = ctx.work_dir / path.relative_to('/work')
    elif path.is_absolute():
        raise ValueError('file must be relative to /work or start with /work/')
    else:
        path = ctx.work_dir / path
    if not path.resolve().is_relative_to(ctx.work_dir.resolve()):
        raise ValueError('file must belong to the episode workspace')
    if not path.is_file():
        raise ValueError(f'input file is missing: {value}')
    return json.loads(path.read_text(encoding='utf-8'))


def draft_file(ctx):
    return 'output/requests.json' if ctx.input_mode == 'script' else 'output/episode.json'
