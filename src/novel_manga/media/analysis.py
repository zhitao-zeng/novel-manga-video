from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path
from ..util import atomic_write_json, media_duration, run
from .common import audio_levels, log

from . import speech

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
        if 'reference' in cached and cached['reference'] != reference:
            rows = speech.corrected_rows(cached.get('chunks', []), reference, ctx.protected_terms, ctx.aliases)
            if not rows and cached.get('hypothesis'):
                # Existing aggregate records can be evaluated, but contain no new timing evidence.
                rows = [{'hypothesis': cached['hypothesis']}]
            checked = speech.evaluate(reference, rows, cached.get('mean_volume_db'), cached.get('max_volume_db'))
            retained = [issue for issue in cached.get('issues', [])
                        if not speech.replaced_by_speech_recheck(issue) and issue != speech.QualityIssue.UNSCRIPTED_SPEECH.code]
            checked['issues'] = list(dict.fromkeys([*retained, *checked['issues']]))
            checked['passed'] = not checked['issues']
            checked['chunks'] = rows if cached.get('chunks') else cached.get('chunks', [])
            cached.update(checked)
            if 'speech_recheck_policy' in cached:
                cached = speech.recheck(reference, cached, ctx.protected_terms, ctx.aliases)
            atomic_write_json(asr_path, cached)
        return cached
    mean_db, peak_db = audio_levels(wav)
    # Listened to even with no line to compare against: the old guard meant a wordless shot
    # was never transcribed, so nothing downstream could ever notice it talking. Keep
    # transcribing even quiet clips: the speech result and volume jointly classify the output.
    chunks = speech_chunks(wav)
    rows: list[dict] = []
    if chunks:
        segments_path = directory / "chunks.json"
        segments_path.write_text(json.dumps(chunks), encoding="utf-8")
        raw_out = directory / "asr_raw.json"
        subprocess.run([ctx.asr_python, str(ctx.asr_helper), "--audio", str(wav), "--segments", str(segments_path), "--output", str(raw_out)], check=True, capture_output=True, text=True)
        for row in json.loads(raw_out.read_text(encoding="utf-8"))["segments"]:
            rows.append(row)
    rows = speech.corrected_rows(rows, reference, ctx.protected_terms, ctx.aliases)
    result = {"clip_id": clip["clip_id"], "video": str(video), "duration": round(media_duration(video), 3),
              **speech.evaluate(reference, rows, mean_db, peak_db)}
    atomic_write_json(asr_path, result)
    return result

def recheck_speech(ctx, clip: dict, analysis: dict, video: Path) -> dict:
    if clip['clip_id'] not in getattr(ctx, 'speech_checks', set()) or analysis.get('speech_recheck_policy') == 1:
        return analysis
    result = speech.recheck(clip.get('spoken_text', ''), analysis, ctx.protected_terms, ctx.aliases)
    atomic_write_json(video.parent / 'asr.json', result)
    return result
