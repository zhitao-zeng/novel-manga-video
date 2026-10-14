"""Read the host-decoded video evidence index using Python's standard library.

This tool selects existing frames, not arbitrary timestamps or new decoding.
"""
import argparse
import json
from pathlib import Path


def inspect(work, take, seconds=None):
    evidence = json.loads((work/'input/evidence.json').read_text())
    row = next((r for r in evidence['takes'] if Path(r['path']).name == take), None)
    if row is None:
        raise ValueError('Unknown take; choose one listed in input/evidence.json')
    frames = row.get('frames', [])
    if seconds is not None:
        timed = [f for f in frames if isinstance(f.get('seconds'), (int, float))]
        frames = [min(timed, key=lambda f: abs(f['seconds']-seconds))] if timed else []
    return {'take': take, 'current': row['current'], 'video': row['path']+'/clip.mp4',
            'request': row.get('request'), 'asr': row.get('asr'),
            'requested_seconds': seconds, 'selection': 'nearest supplied frame' if seconds is not None else 'all supplied frames',
            'frames': frames, 'instruction': 'Read the listed JPEG paths with the image reader. Timestamps are actual sampled positions, not exact requested times.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('take')
    parser.add_argument('--seconds', type=float)
    args = parser.parse_args()
    work = Path(__file__).resolve().parents[1]
    print(json.dumps(inspect(work, args.take, args.seconds), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
