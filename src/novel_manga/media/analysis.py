from __future__ import annotations
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from PIL import Image
from ..util import atomic_write_json, media_duration, run
from ..runtime_backends import correct_protected_lexicon, edit_distance, normalize_text
from .common import audio_levels, cover_title, log

from .subtitles import match_key, subsequence_overlap

MAX_MISSING = 0.5

MIN_PEAK_DB = -35.0

SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")

def speech_chunks(wav: Path, *, noise_db: float = -30.0, min_silence: float = 0.35, min_chunk: float = 0.4, pad: float = 0.15) -> list[list[float]]:
    duration = media_duration(wav)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    events = [(kind, float(value)) for kind, value in SILENCE_EVENT.findall(result.stderr)]
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in events:
        if kind == "start":
            start = value
        elif kind == "end" and start is not None:
            silences.append((start, value))
            start = None
    if start is not None:
        silences.append((start, duration))
    chunks: list[list[float]] = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        if silence_start - cursor >= min_chunk:
            chunks.append([cursor, silence_start])
        cursor = silence_end
    if duration - cursor >= min_chunk:
        chunks.append([cursor, duration])
    if not chunks:
        return [[0.0, duration]]
    merged: list[list[float]] = []
    for chunk in chunks:
        if merged and chunk[0] - merged[-1][1] < 0.3:
            merged[-1][1] = chunk[1]
        else:
            merged.append(chunk)
    return [[round(max(0.0, s - pad), 3), round(min(duration, e + pad), 3)] for s, e in merged]

def analyse_clip(ctx, clip: dict, video: Path) -> dict:
    directory = video.parent
    wav = directory / "native.wav"
    asr_path = directory / "asr.json"
    if wav.is_file():
        # A run killed mid-extraction leaves a short wav; trust it only when
        # it is as long as the clip, otherwise redo the extraction and ASR.
        try:
            wav_seconds = media_duration(wav)
        except Exception:  # noqa: BLE001 - unreadable header counts as truncated
            wav_seconds = -1.0
        if abs(wav_seconds - media_duration(video)) > 0.5:
            log(f"{clip['clip_id']}: native.wav is {wav_seconds:.1f}s for a {media_duration(video):.1f}s clip; re-extracting")
            for name in ("native.wav", "asr.json", "asr_raw.json", "chunks.json"):
                (directory / name).unlink(missing_ok=True)
    if not wav.is_file():
        partial = directory / "native.partial.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(partial)])
        partial.replace(wav)
    reference = clip.get("spoken_text", "")
    if asr_path.is_file():
        # The record names the take it was made from, but its clip directory can be renamed under it
        # (split_long_stages renumbers clips): the take is the file beside the record, never the path inside it.
        cached = {**json.loads(asr_path.read_text(encoding="utf-8")), "clip_id": clip["clip_id"], "video": str(video)}
        return cached
    mean_db, peak_db = audio_levels(wav)
    chunks = speech_chunks(wav) if reference else []
    rows: list[dict] = []
    if chunks:
        segments_path = directory / "chunks.json"
        segments_path.write_text(json.dumps(chunks), encoding="utf-8")
        raw_out = directory / "asr_raw.json"
        subprocess.run([ctx.asr_python, str(ctx.asr_helper), "--audio", str(wav), "--segments", str(segments_path), "--output", str(raw_out)], check=True, capture_output=True, text=True)
        for row in json.loads(raw_out.read_text(encoding="utf-8"))["segments"]:
            corrected, corrections = correct_protected_lexicon(row["hypothesis"], reference, ctx.protected_terms, ctx.aliases)
            rows.append({**row, "raw_hypothesis": row["hypothesis"], "hypothesis": corrected, "corrections": corrections})
    hypothesis = "".join(row["hypothesis"] for row in rows)
    reference_key = match_key(reference)    # numbers in spoken form: 50万 and 五十万 agree
    hypothesis_key = match_key(hypothesis)
    cer = round(edit_distance(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    # CER punishes what the model ADDED (an ad-lib, a chuckle, a stage direction
    # it read out) as much as what it dropped, and 12 of 14 gate failures in the
    # first 46 episodes were of that kind - the lines were spoken.  The gate
    # judges the share of the script that was never heard, in order.
    missing = round(1.0 - subsequence_overlap(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
    issues = []
    if reference_key:
        if not hypothesis_key or peak_db is None or peak_db < MIN_PEAK_DB:
            issues.append("voice_energy_missing")
        if missing > MAX_MISSING:
            issues.append(f"missing_{missing}_over_{MAX_MISSING}")
    result = {
        "clip_id": clip["clip_id"], "video": str(video), "duration": round(media_duration(video), 3),
        "reference": reference, "hypothesis": hypothesis, "cer": cer, "missing": missing, "mean_volume_db": mean_db, "max_volume_db": peak_db,
        "chunks": rows, "issues": issues, "passed": not issues,
    }
    atomic_write_json(asr_path, result)
    return result

def recheck_speech(ctx, clip: dict, analysis: dict, video: Path) -> dict:
    if clip['clip_id'] not in getattr(ctx, 'speech_checks', set()) or analysis.get('speech_recheck_policy') == 1:
        return analysis
    reference = clip.get('spoken_text', '')
    chunks = []
    for row in analysis.get('chunks') or []:
        raw = row.get('raw_hypothesis', row.get('hypothesis', ''))
        corrected, corrections = correct_protected_lexicon(raw, reference, ctx.protected_terms, ctx.aliases)
        chunks.append({**row, 'raw_hypothesis': raw, 'hypothesis': corrected, 'corrections': corrections})
    raw = ''.join(row['raw_hypothesis'] for row in chunks) if chunks else str(analysis.get('hypothesis') or '')
    hypothesis = ''.join(row['hypothesis'] for row in chunks) if chunks else correct_protected_lexicon(raw, reference, ctx.protected_terms, ctx.aliases)[0]
    expected, heard = match_key(reference), match_key(hypothesis)
    missing = round(1 - subsequence_overlap(expected, heard) / max(1, len(expected)), 4) if expected else 0
    issues = [i for i in analysis.get('issues') or [] if not i.startswith(('missing_', 'voice_energy_missing'))]
    if expected:
        if not heard or analysis.get('max_volume_db') is None or analysis['max_volume_db'] < MIN_PEAK_DB:
            issues.append('voice_energy_missing')
        if missing > MAX_MISSING:
            issues.append(f'missing_{missing}_over_{MAX_MISSING}')
        if len(heard) - len(expected) > max(12, len(expected) * 2):
            issues.append('excess_unplanned_speech')
    if re.search(r'keep\s*everything\s*above|this\s*is\s*take|spoken\s*clearly\s*and\s*completely', raw, re.I):
        issues.append('director_instruction_spoken')
    result = {**analysis, 'reference': reference, 'hypothesis': hypothesis, 'chunks': chunks or analysis.get('chunks', []),
              'missing': missing, 'issues': list(dict.fromkeys(issues)), 'passed': not issues, 'speech_recheck_policy': 1}
    atomic_write_json(video.parent / 'asr.json', result)
    return result
