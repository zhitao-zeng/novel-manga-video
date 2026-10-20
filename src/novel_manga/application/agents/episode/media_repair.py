"""Fixed camera crop of an existing take; preserve audio and require normal review."""
import json
import math
from pathlib import Path
import shutil
import subprocess

from novel_manga.media.results import selected_video
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError, first_pass_scope


def validate(ctx, decision):
    from .repair_targets import validate_target
    from novel_manga.application.review.execution import media_matches
    first_pass_scope(ctx, [decision['clip_id']])
    validate_target(ctx, decision)
    crop = decision.get('crop')
    if (not isinstance(crop, dict) or set(crop) != {'x', 'y', 'width'}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in crop.values())
            or not 0 < crop['width'] <= 1 or not 0 <= crop['x'] <= 1-crop['width']
            or not 0 <= crop['y'] < 1):
        raise RepairArgumentError('crop requires normalized x, y, width; 0 < width <= 1, x+width <= 1')
    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    report = read_json(ctx.episode_dir/'thin_media_report.json', {})
    if not media_matches(plan, report, notes=read_json(ctx.episode_dir/'review_feedback.json', {})):
        raise RepairArgumentError('crop needs a current selected take matching the current request')
    row = next(r for r in report['clips'] if r['clip_id'] == decision['clip_id'])
    selected = row.get('selected') or {}
    previous = selected.get('crop') or {}
    video = selected_video(selected)
    source = previous['source'] if video and video == previous.get('video') else video
    if not source or not Path(source).is_file():
        raise RepairArgumentError('crop needs an existing current take; it cannot replace missing footage')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'json', str(source)], text=True))['streams'][0]
    sw, sh = info['width'], info['height']
    frame = read_json(ctx.novel_dir/'profile.json', {}).get('frame')
    width, height = (1920, 1080) if frame == '16:9' or (not frame and sw >= sh) else (1080, 1920)
    cw = int(sw*crop['width'])//2*2
    ch = int(cw*height/width)//2*2
    x, y = int(sw*crop['x'])//2*2, int(sh*crop['y'])//2*2
    if cw < 2 or ch < 2 or x+cw > sw or y+ch > sh:
        raise RepairArgumentError('crop window does not fit the source at the production aspect ratio')
    return report, row, Path(source), crop, (x,y,cw,ch,width,height)


def apply(ctx, decision):
    report, row, source, crop, geometry = validate(ctx, decision)
    selected = row['selected']
    previous = selected.get('crop') or {}
    identity = take_identity(source)
    if (previous.get('source_take') == identity and previous.get('request') == crop
            and Path(previous.get('video', '')).is_file()):
        return {'ok': True, 'changed': [], 'media_changed': [], 'reused': True,
                'review_required': True, 'media_operation': 'crop', 'blocked': {}}
    x,y,cw,ch,width,height = geometry
    root = ctx.episode_dir/'work/clips'/decision['clip_id']
    destination = root/f'crop_{len(list(root.glob("crop_*")))+1:03d}'
    destination.mkdir(parents=True)
    output = destination/'clip.mp4'; partial = destination/'partial.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source),
        '-map', '0:v:0', '-map', '0:a?', '-vf', f'crop={cw}:{ch}:{x}:{y},scale={width}:{height},fps=25,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '2',
        '-c:a', 'copy', '-movflags', '+faststart', str(partial)], check=True)
    partial.replace(output)
    # Same audio packets and duration: carry actual existing analysis, not planned words.
    for name in ['native.wav', 'asr.json', 'asr_raw.json', 'chunks.json']:
        if (source.parent/name).is_file():
            shutil.copy2(source.parent/name, destination/name)
    receipt = {'source': str(source), 'source_take': identity, 'request': crop,
               'video': str(output), 'pixels': [x, y, cw, ch], 'audio': 'copied unchanged'}
    selected.update(postmix_video=str(output), crop=receipt)
    atomic_write_json(destination/'crop.json', receipt)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', report)
    return {'ok': True, 'changed': [], 'media_changed': [decision['clip_id']],
            'media_operation': 'crop', 'review_required': True, 'blocked': {}, 'crop': receipt,
            'next': 'Review the current cropped take, then finish; no content acceptance is implied.'}
