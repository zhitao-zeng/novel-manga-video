#!/usr/bin/env python
"""Build each character's reference voice from the episodes already rendered.

    build_voices_thin.py --novel-dir outputs/X [--min-seconds 8] [--target-seconds 14] [--only 沈玄川,苏清月]

Seedance clones a voice from a reference_audio, and the reference has to be real
speech of some length: two seconds of "嗯" does nothing, twelve seconds of lines
puts the generated voice past the same-speaker line (see
docs/seedance-reference-audio.md).  This gathers, for every character, the ASR
chunks the runner aligned to that character's lines alone, longest first, and
concatenates them until the target length, into

    outputs/<novel>/series_assets/voices/<name>.wav   (16 kHz mono)
    outputs/<novel>/series_assets/voices/voices.json  (seconds, sources)

A character below --min-seconds gets no file: a weak reference is worse than
none.  Re-running only rebuilds characters whose material grew; pass --rebuild to
redo everything.  The renderer attaches these automatically.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MIN_CHUNK_SECONDS = 1.0


def attributed_chunks(novel_dir: Path) -> dict[str, list[tuple[float, Path, float, float, str]]]:
    """name -> [(seconds, wav, start, end, episode)] for chunks aligned to that name's lines only."""
    found: dict[str, list[tuple[float, Path, float, float, str]]] = defaultdict(list)
    for directory in sorted(d for d in novel_dir.iterdir() if d.is_dir() and d.name.startswith(novel_dir.name + "_")):
        report_path, plan_path = directory / "thin_media_report.json", directory / "clip_plan.json"
        if not (report_path.is_file() and plan_path.is_file()):
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            plan = {c["clip_id"]: c for c in json.loads(plan_path.read_text(encoding="utf-8"))["clips"]}
        except (OSError, ValueError, KeyError):
            continue
        for clip in report.get("clips", []):
            selected = clip.get("selected") or {}
            lines = (plan.get(clip["clip_id"]) or {}).get("lines") or []
            if not lines or not selected.get("video"):
                continue
            wav = Path(selected["video"]).parent / "native.wav"
            if not wav.is_file():
                continue
            for chunk in selected.get("chunks", []):
                if chunk.get("subtitle") != "script_span":
                    continue
                matched = str(chunk.get("matched_lines") or "")
                owners = {line["speaker_name"] for line in lines
                          if line.get("text") and len(line["text"]) >= 3 and line["text"][:4] in matched}
                if len(owners) != 1:
                    continue
                start, end = float(chunk["start"]), float(chunk["end"])
                if end - start >= MIN_CHUNK_SECONDS:
                    found[owners.pop()].append((end - start, wav, start, end, directory.name))
    return found


def build(name: str, pieces: list[tuple[float, Path, float, float, str]], out_dir: Path, target: float, cap: float) -> dict:
    pieces = sorted(pieces, key=lambda item: -item[0])
    work = out_dir / ".work" / name
    work.mkdir(parents=True, exist_ok=True)
    chosen, total, sources = [], 0.0, set()
    for span, wav, start, end, episode in pieces:
        if total >= target:
            break
        piece = work / f"{len(chosen):02d}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", str(wav),
                        "-ac", "1", "-ar", "16000", str(piece)], check=True)
        chosen.append(piece)
        total += span
        sources.add(episode)
    listing = work / "pieces.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in chosen), encoding="utf-8")
    target_path = out_dir / f"{name}.wav"
    # Hard cap: Seedance allows 30.2 s of reference audio per request, and a
    # clip may carry two or three voices, so one voice must not eat the budget.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-t", f"{cap:.2f}", "-ac", "1", "-ar", "16000", str(target_path)], check=True)
    return {"seconds": round(min(total, cap), 1), "pieces": len(chosen), "sources": sorted(sources), "path": str(target_path.relative_to(out_dir.parent.parent))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--min-seconds", type=float, default=8.0, help="characters with less attributed speech get no reference")
    parser.add_argument("--target-seconds", type=float, default=12.0)
    parser.add_argument("--max-seconds", type=float, default=14.0, help="hard cap per voice; two voices must fit the service's 30 s")
    parser.add_argument("--only", help="comma-separated character names")
    parser.add_argument("--rebuild", action="store_true", help="rebuild every character, not just the ones with more material")
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    out_dir = novel_dir / "series_assets" / "voices"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "voices.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    wanted = {n.strip() for n in args.only.split(",")} if args.only else None

    chunks = attributed_chunks(novel_dir)
    built, skipped = [], []
    for name, pieces in sorted(chunks.items(), key=lambda item: -sum(p[0] for p in item[1])):
        if wanted and name not in wanted:
            continue
        if name.startswith("无名"):
            continue  # crowd and bystander roles are meant to sound different every time
        available = sum(p[0] for p in pieces)
        if available < args.min_seconds:
            skipped.append((name, round(available, 1)))
            continue
        previous = manifest.get(name) or {}
        if not args.rebuild and previous.get("seconds", 0) >= min(args.target_seconds, available) - 0.5 and previous.get("seconds", 0) <= args.max_seconds and (out_dir / f"{name}.wav").is_file():
            continue
        manifest[name] = build(name, pieces, out_dir, args.target_seconds, args.max_seconds)
        built.append((name, manifest[name]["seconds"]))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"音色库 {out_dir}：共 {len(manifest)} 个角色")
    for name, seconds in built:
        print(f"  建好 {name} {seconds}s")
    for name, seconds in skipped[:12]:
        print(f"  素材不足 {name} {seconds}s（不足 {args.min_seconds}s，不建）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
