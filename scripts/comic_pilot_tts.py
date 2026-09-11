#!/usr/bin/env python3
"""Synthesize a frozen comic pilot script with an existing, offline IndexTTS 2.5 install."""
import argparse
import json
import sys
import time
import wave
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    out = args.manifest.parent
    sys.path.insert(0, str(args.repo))
    import torch
    from indextts.infer_v2_5 import IndexTTS2
    torch.set_num_threads(4)
    torch.manual_seed(data['seed'])
    began = time.monotonic()
    model = IndexTTS2(cfg_path=str(args.model_dir / 'config.yaml'), model_dir=str(args.model_dir),
                      device='cuda:0', use_bf16=True, use_cuda_kernel=False, use_qwen_emo=False)
    load_seconds = time.monotonic() - began
    rows = []
    for scene in data['scenes']:
        target = out / 'audio' / f"scene_{scene['id']:02d}.wav"
        target.parent.mkdir(exist_ok=True)
        started = time.monotonic()
        # Existing speech is kept for a rendering-only rerun. The manifest is frozen.
        reused = target.is_file()
        if not reused:
            model.infer(spk_audio_prompt=scene['voice_reference'], text=scene['text'], output_path=str(target),
                        lang='zh', do_sample=False, duration_factor=1.0)
        with wave.open(str(target), 'rb') as handle:
            seconds = handle.getnframes() / handle.getframerate()
        row = {'scene': scene['id'], 'speaker': scene['speaker'], 'text': scene['text'], 'path': str(target),
               'audio_seconds': seconds, 'synthesis_seconds': time.monotonic() - started, 'reused': reused}
        rows.append(row)
        (out / 'tts_report.json').write_text(json.dumps({'model': 'IndexTTS-2.5', 'load_seconds': load_seconds,
            'rows': rows, 'elapsed_seconds': time.monotonic() - began,
            'peak_vram_gb': torch.cuda.max_memory_allocated()/1024**3}, ensure_ascii=False, indent=2))
        print(f"scene {scene['id']:02d}: {seconds:.2f}s audio, {row['synthesis_seconds']:.2f}s compute", flush=True)


if __name__ == '__main__':
    main()
