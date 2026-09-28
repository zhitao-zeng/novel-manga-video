"""Compare existing takes at six moments. Adapted from c393940 render/refsheet.py; no generation calls."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont


def build(root, shot, arms, takes, output):
    width, label = 320, 30
    font = ImageFont.load_default(size=20)
    panels = []
    with tempfile.TemporaryDirectory(prefix='reference-comparison-') as temporary:
        for arm in arms:
            for take in takes:
                video = root / arm / f'S{shot}_t{take}.mp4'
                duration = float(subprocess.check_output(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(video)], text=True))
                times = [0, min(.20, duration / 2), duration * .25, duration * .5, duration * .75, max(0, duration - .08)]
                frames = []
                for i, moment in enumerate(times):
                    path = Path(temporary) / f'frame_{i}.png'
                    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{moment:.3f}', '-i', str(video),
                                    '-frames:v', '1', str(path)], check=True)
                    with Image.open(path) as original:
                        frame = original.convert('RGB')
                    frames.append(frame.resize((width, round(frame.height * width / frame.width)), Image.Resampling.LANCZOS))
                panels.append((f'{arm} t{take}', times, frames))
    height = max(frame.height for _, _, frames in panels for frame in frames)
    sheet = Image.new('RGB', (width * 6 + 150, (height + label) * len(panels) + label), 'white')
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 4), f'shot {shot}: start / 0.20s / 25% / 50% / 75% / end', fill='black', font=font)
    for row, (name, times, frames) in enumerate(panels):
        y = label + row * (height + label)
        draw.text((8, y + height // 2), name, fill='black', font=font)
        for i, (moment, frame) in enumerate(zip(times, frames)):
            x = 150 + i * width
            sheet.paste(frame, (x, y))
            draw.text((x + 5, y + height + 4), f'{moment:.2f}s', fill='black', font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, 'JPEG', quality=85)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--shot', required=True)
    parser.add_argument('--arms', nargs='+', default=['sheet', 'both', 'picked'])
    parser.add_argument('--takes', nargs='+', type=int, default=[0, 1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.shot, args.arms, args.takes, args.output)
    print(args.output)
