import json
import subprocess

from novel_manga.config import Settings
from novel_manga.media.postprocess import BatchRenderer
from novel_manga.render import TimedSubtitle


def test_batch_join_preserves_order_canvas_fps_and_audio(tmp_path):
    parts = []
    for i, (color, size) in enumerate([('red', '96x96'), ('blue', '128x72')]):
        path = tmp_path / f'part{i}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s={size}:r=25:d=0.4',
                        '-f', 'lavfi', '-i', f'sine=frequency={440 + i * 440}:duration=0.4',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True)
        parts.append(path)
    renderer = BatchRenderer(Settings(width=320, height=180, fps=25))
    final = tmp_path / 'joined.mp4'
    offsets = renderer._join_with_crossfade(parts, [.4, .4], final)
    assert offsets[0] == 0 and abs(offsets[1] - .4) <= 1 / 25
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(final)]))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height'], video['codec_name']) == (320, 180, 'h264')
    # The concat intermediate can report a doubled nominal rate around AAC
    # packet boundaries; verify its actual frame count and presentation span.
    # The full-assembly before/after probe checks the final delivery's 25 fps.
    assert int(video['nb_frames']) == 20
    assert abs(float(video['duration']) - .8) <= 1 / 25
    assert (audio['sample_rate'], audio['channels'], audio['codec_name']) == ('48000', 2, 'aac')
    for when, dominant in [(.1, 0), (.6, 2)]:
        rgb = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(when), '-i', str(final), '-frames:v', '1',
                                       '-vf', 'crop=2:2:160:90,scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
        assert rgb[dominant] > 200 and rgb[2 - dominant] < 40


def test_subtitle_margin_is_per_renderer_and_repeated_writes_do_not_stack(tmp_path):
    landscape = BatchRenderer(Settings(width=1920, height=1080))
    portrait = BatchRenderer(Settings(width=1080, height=1920))
    captions = [TimedSubtitle(start=.1, end=.7, text='字幕原文不变。')]
    a = landscape.write_ass_pages(tmp_path / 'a.ass', captions).read_text()
    b = portrait.write_ass_pages(tmp_path / 'b.ass', captions).read_text()
    assert ',2,90,90,86,1' in a and ',2,90,90,310,1' in b
    assert landscape.write_ass_pages(tmp_path / 'again.ass', captions).read_text() == a
