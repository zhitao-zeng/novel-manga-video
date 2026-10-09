"""A rendered dark end card must not lengthen a short story-tail dark interval."""
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from novel_manga.config import Settings
from novel_manga.media.quality import inspect_media


@pytest.mark.parametrize('dark_start,outro,expected', [(2.2, 3.2, True), (1.5, 3.2, False), (2.2, 0, False)])
def test_black_frame_gate_measures_story_and_keeps_true_darkness_strict(tmp_path, dark_start, outro, expected):
    video = tmp_path/'video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'color=c=blue:s=192x108:r=25:d=6,drawbox=color=black:t=fill:enable=between(t\\,{dark_start}\\,5.6)',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(video)], check=True)
    for name in ['cover', 'ending']:
        Image.new('RGB', (192, 108), 'blue').save(tmp_path/f'{name}.jpeg')
    ass = tmp_path/'subtitles.ass'
    ass.write_text('Dialogue: retained subtitle evidence')
    qc = inspect_media(video, tmp_path/'cover.jpeg', tmp_path/'ending.jpeg', ass,
        Settings(width=192, height=108), tmp_path/'qc.json', silent_outro_seconds=outro)
    check = qc['checks']['black_frames']
    assert check['passed'] is expected
    assert check['detail']['max_black_seconds'] == pytest.approx((6-outro if outro else 5.64)-dark_start, abs=.08)
    assert check['detail']['scope'] == ('story' if outro else 'full_video')

