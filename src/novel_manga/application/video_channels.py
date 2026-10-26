"""Select an existing registered video resource and retain its episode route."""
from pathlib import Path
import os

from novel_manga.util import atomic_write_json, read_json

CLOUD_MODELS = {'MiniMax-H3', 'sd2.0-fast', 'sd2.0-mini'}
SD_MODELS = {'sd2.5', 'sd2.0', 'sd2.0-fast', 'sd2.0-mini'}


def selected_channel(episode_dir, config=None):
    return ((config or {}).get('video_channel') or os.environ.get('NOVEL_VIDEO_CHANNEL')
            or read_json(Path(episode_dir)/'video-channel.json', {}).get('name'))


def channel_environment(root, name, *, episode_dir=None, model=None, persist=True):
    from .configuration import pipeline_config, RuntimePaths
    saved = read_json(Path(episode_dir)/'video-channel.json', {}) if episode_dir else {}
    if saved and saved['name'] != name:
        raise ValueError('This episode already has a different video channel; preserve its route and history')
    if saved:
        resource = saved['resource']
    else:
        resources = pipeline_config(Path(root)).get('resources', {}).get('video_keys', {})
        if name not in resources:
            raise ValueError(f'Unknown registered video channel: {name}')
        resource = resources[name]
    if model is not None and resource['model'] != model:
        raise ValueError('The selected video channel does not match the renderer model')
    if episode_dir:
        plan = read_json(Path(episode_dir)/'clip_plan.json', {})
        too_long = [c['clip_id'] for c in plan.get('clips', []) if c.get('kind', 'video') == 'video'
                    and float(c.get('request_seconds') or 0) > float(resource['clip_cap'])]
        if too_long:
            raise ValueError(f"Channel {name} requires clips of at most {resource['clip_cap']} seconds; repack the original plan: {too_long}")
    if episode_dir and not saved:
        episode_dir = Path(episode_dir)
        from .production.artifacts import media_started
        if media_started(episode_dir):
            models = {read_json(p, {}).get('model') for p in (episode_dir/'work/clips').glob('*/attempt_*/clip.mp4.task.json')}
            models.discard(None)
            if models != {resource['model']}:
                raise ValueError('Existing media cannot be silently moved to a different video channel')
        # Only routing facts, never credential values or authenticated URLs.
        fields = ('model', 'key_var', 'base_url', 'pool', 'clip_cap', 'inflight', 'inflight_dir')
        record = {key: resource[key] for key in fields if key in resource}
        if persist:
            atomic_write_json(episode_dir/'video-channel.json', {'name': name, 'resource': record})
    local = resource.get('base_url') or ''
    pool = resource.get('pool') or name
    env = {'NOVEL_VIDEO_CHANNEL': name, 'NOVEL_VIDEO_MODEL': resource['model'],
           'NOVEL_LOCAL_H3_URL': local, 'PHANROUTER_VIDEO_KEY_VAR': resource.get('key_var', ''),
           'NOVEL_CLIP_SECONDS_MAX': str(resource['clip_cap']), 'NOVEL_INFLIGHT_POOL': pool,
           'NOVEL_INFLIGHT_DIR': str(resource.get('inflight_dir') or RuntimePaths(Path(root)).temporary/'inflight'/pool),
           'NOVEL_RENDER_INFLIGHT': str(resource.get('inflight', {}).get('max', 2))}
    if resource['model'] in CLOUD_MODELS:
        env.update(PHANROUTER_INLINE_REFERENCE_IMAGES='0',
                   PHANROUTER_REFERENCE_ASSETS='0' if resource['model'] == 'MiniMax-H3' else '1')
    return env
