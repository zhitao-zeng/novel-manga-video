"""Estimate speech budget from historical shots.json. Adapted from c393940 voice_v2.py; not ASR measurement."""
import argparse
import json
from pathlib import Path
import re

NAMES = {'shanyin': '山音', 'drama': 'Drama', 'community': '社区', 'dream': '造梦师', 'leos': 'Leos', 'visual': 'Visual'}


def report(root):
    rows = ['语音时间按每秒 4 个汉字估算，不是实际发声时长。', '',
            '| 版本 | 计划秒 | 独白字数 | 出声台词字数 | 预计语音秒 | 预计占比 |', '|---|---|---|---|---|---|']
    found = 0
    for method, label in NAMES.items():
        for version, folder in [('旧', 'render'), ('新', 'render_v2')]:
            path = root / folder / method / 'shots.json'
            if not path.is_file():
                continue
            shots = json.loads(path.read_text(encoding='utf-8'))['shots']
            total = sum(s['planned_seconds'] for s in shots)
            counts = [sum(len(re.findall(r'[一-鿿]', line)) for s in shots if bool(s['voiceover']) == voice
                          for line in s['lines']) for voice in (True, False)]
            seconds = sum(counts) / 4
            ratio = f'{seconds / total:.0%}' if total else '无计划时长'
            rows.append(f'| {label}·{version} | {total:g} | {counts[0]} | {counts[1]} | {seconds:g} | {ratio} |')
            found += 1
    if not found:
        raise ValueError('没有找到 render 或 render_v2 下六种方法的 shots.json')
    return '\n'.join(rows) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = report(args.root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)
