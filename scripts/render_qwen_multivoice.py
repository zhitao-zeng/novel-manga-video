#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
import soundfile as sf

from novel_manga.multivoice import MultivoiceScript, load_multivoice_script, subtitle_pages


def run(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        detail = result.stderr[-6000:] if result.stderr else "no stderr"
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _stub_unused_torchaudio_25hz(model_dir: Path) -> None:
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("tokenizer_type") != "qwen3_tts_tokenizer_12hz":
        raise ValueError("torchaudio compatibility shim is valid only for the Qwen3-TTS 12 Hz model")

    def stub(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        return module

    torchaudio = stub("torchaudio")
    compliance = stub("torchaudio.compliance")
    kaldi = stub("torchaudio.compliance.kaldi")
    compliance.kaldi = kaldi
    torchaudio.compliance = compliance
    sys.modules["torchaudio"] = torchaudio
    sys.modules["torchaudio.compliance"] = compliance
    sys.modules["torchaudio.compliance.kaldi"] = kaldi


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    return np.asarray(samples, dtype=np.float32), int(sample_rate)


def synthesize(
    script: MultivoiceScript,
    model_dir: Path,
    output_dir: Path,
    seed: int,
) -> dict:
    audio_dir = output_dir / "turn_audio"
    shot_dir = output_dir / "shot_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    shot_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "generation_progress.json"

    turn_jobs: list[tuple[int, int, object, object, Path]] = []
    for shot in script.shots:
        for turn_index, turn in enumerate(shot.turns, 1):
            profile = script.voices[turn.role]
            path = audio_dir / f"shot_{shot.index:03d}_turn_{turn_index:02d}.wav"
            turn_jobs.append((shot.index, turn_index, turn, profile, path))

    missing = [job for job in turn_jobs if not job[4].is_file() or job[4].stat().st_size < 1000]
    generation_rows: list[dict] = []
    model_load_seconds = 0.0
    if missing:
        _stub_unused_torchaudio_25hz(model_dir)
        import torch
        from qwen_tts import Qwen3TTSModel

        started = time.monotonic()
        model = Qwen3TTSModel.from_pretrained(
            str(model_dir),
            device_map="cuda:0",
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
        )
        model_load_seconds = time.monotonic() - started
        print(f"model_loaded seconds={model_load_seconds:.3f} missing_turns={len(missing)}", flush=True)

        for ordinal, (shot_index, turn_index, turn, profile, path) in enumerate(missing, 1):
            torch.manual_seed(seed + shot_index * 100 + turn_index)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed + shot_index * 100 + turn_index)
            started = time.monotonic()
            wavs, sample_rate = model.generate_custom_voice(
                text=turn.text,
                speaker=profile.speaker,
                language=script.language,
                instruct=profile.instruct,
            )
            if not wavs or len(wavs[0]) == 0:
                raise RuntimeError(f"empty Qwen TTS output for shot {shot_index} turn {turn_index}")
            sf.write(path, wavs[0], sample_rate, subtype="PCM_16")
            elapsed = time.monotonic() - started
            duration = len(wavs[0]) / sample_rate
            row = {
                "shot_index": shot_index,
                "turn_index": turn_index,
                "role": turn.role,
                "speaker": profile.speaker,
                "text": turn.text,
                "duration_seconds": round(duration, 3),
                "generation_seconds": round(elapsed, 3),
                "path": str(path),
            }
            generation_rows.append(row)
            atomic_json(progress_path, {
                "completed_missing_turns": ordinal,
                "total_missing_turns": len(missing),
                "latest": row,
            })
            print(
                f"generated {ordinal}/{len(missing)} shot={shot_index} turn={turn_index} "
                f"role={turn.role} speaker={profile.speaker} audio={duration:.3f}s wall={elapsed:.3f}s",
                flush=True,
            )

        del model
        torch.cuda.empty_cache()

    timings: list[dict] = []
    for shot in script.shots:
        pieces: list[np.ndarray] = []
        cursor = 0.0
        sample_rate: int | None = None
        for turn_index, turn in enumerate(shot.turns, 1):
            path = audio_dir / f"shot_{shot.index:03d}_turn_{turn_index:02d}.wav"
            samples, current_rate = _read_mono(path)
            if sample_rate is None:
                sample_rate = current_rate
            if current_rate != sample_rate:
                raise ValueError(f"sample rate changed within shot {shot.index}: {current_rate} vs {sample_rate}")
            start = cursor
            duration = len(samples) / sample_rate
            end = start + duration
            timings.append({
                "shot_index": shot.index,
                "turn_index": turn_index,
                "role": turn.role,
                "speaker": script.voices[turn.role].speaker,
                "text": turn.text,
                "start_in_shot": round(start, 6),
                "end_in_shot": round(end, 6),
            })
            pieces.append(samples)
            pause_samples = round(turn.pause_after * sample_rate)
            if pause_samples:
                pieces.append(np.zeros(pause_samples, dtype=np.float32))
            cursor = end + turn.pause_after
        assert sample_rate is not None
        combined = np.concatenate(pieces)
        sf.write(shot_dir / f"shot_{shot.index:03d}.wav", combined, sample_rate, subtype="PCM_16")

    result = {
        "video_id": script.video_id,
        "model": str(model_dir),
        "model_load_seconds": round(model_load_seconds, 3),
        "speaker_count": script.speaker_count,
        "turn_count": script.turn_count,
        "voices": {role: profile.model_dump() for role, profile in script.voices.items()},
        "generated_turns_this_run": generation_rows,
        "timings": timings,
    }
    atomic_json(output_dir / "audio_report.json", result)
    return result


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def write_ass(path: Path, events: list[dict]) -> Path:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H78000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows: list[str] = []
    for event in events:
        pages = subtitle_pages(event["text"])
        page_seconds = max(0.2, (event["end"] - event["start"]) / len(pages))
        for page_index, page in enumerate(pages):
            start = event["start"] + page_index * page_seconds
            end = event["end"] if page_index == len(pages) - 1 else min(event["end"], start + page_seconds)
            safe_page = page.replace("{", "（").replace("}", "）")
            rows.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,{event['role']},0,0,0,,{safe_page}"
            )
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def render(script: MultivoiceScript, episode_dir: Path, output_dir: Path) -> dict:
    raw_video_dir = episode_dir / "work" / "raw_video"
    intro = episode_dir / "work" / "intro.mp4"
    outro = episode_dir / "work" / "outro.mp4"
    shot_audio_dir = output_dir / "shot_audio"
    segment_dir = output_dir / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    for required in (intro, outro):
        if not required.is_file():
            raise FileNotFoundError(required)

    audio_report = json.loads((output_dir / "audio_report.json").read_text(encoding="utf-8"))
    timing_lookup: dict[int, list[dict]] = {}
    for item in audio_report["timings"]:
        timing_lookup.setdefault(int(item["shot_index"]), []).append(item)

    segment_paths: list[Path] = []
    segment_durations: dict[int, float] = {}
    for shot in script.shots:
        raw_video = raw_video_dir / f"shot_{shot.index:03d}.mp4"
        shot_audio = shot_audio_dir / f"shot_{shot.index:03d}.wav"
        segment = segment_dir / f"shot_{shot.index:03d}.mp4"
        if not raw_video.is_file() or not shot_audio.is_file():
            raise FileNotFoundError(raw_video if not raw_video.is_file() else shot_audio)
        duration = media_duration(shot_audio) + 0.08
        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw_video), "-i", str(shot_audio),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25,format=yuv420p",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "25",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(segment),
        ])
        actual_duration = media_duration(segment)
        segment_paths.append(segment)
        segment_durations[shot.index] = actual_duration
        print(
            f"rendered shot={shot.index}/{len(script.shots)} duration={actual_duration:.3f}s",
            flush=True,
        )

    concat_file = output_dir / "concat.txt"
    sequence = [intro, *segment_paths, outro]
    concat_file.write_text(
        "\n".join(f"file '{path.resolve()}'" for path in sequence) + "\n",
        encoding="utf-8",
    )
    joined = output_dir / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])
    print(f"joined duration={media_duration(joined):.3f}s", flush=True)

    cursor = media_duration(intro)
    subtitle_events: list[dict] = []
    for shot in script.shots:
        for timing in timing_lookup[shot.index]:
            subtitle_events.append({
                **timing,
                "start": cursor + float(timing["start_in_shot"]),
                "end": cursor + float(timing["end_in_shot"]),
            })
        cursor += segment_durations[shot.index]

    ass = write_ass(output_dir / "subtitles.ass", subtitle_events)
    final_video = output_dir / f"{script.video_id}_qwen_multivoice.mp4"
    escaped_ass = str(ass.resolve()).replace("'", r"\'").replace(":", r"\:")
    run([
        "ffmpeg", "-y", "-i", str(joined), "-vf", f"ass='{escaped_ass}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-r", "25",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(final_video),
    ])
    print(f"final_video path={final_video} duration={media_duration(final_video):.3f}s", flush=True)
    report = {
        "video": str(final_video),
        "sha256": hashlib.sha256(final_video.read_bytes()).hexdigest(),
        "duration_seconds": round(media_duration(final_video), 3),
        "subtitle_events": len(subtitle_events),
        "segment_durations": segment_durations,
    }
    atomic_json(output_dir / "render_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a cached Qwen3-TTS multi-voice manga-drama probe")
    parser.add_argument("--stage", choices=("audio", "render", "all"), default="all")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260805)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = load_multivoice_script(args.script)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.stage in {"audio", "all"}:
        if args.model_dir is None:
            raise ValueError("--model-dir is required for the audio stage")
        synthesize(script, args.model_dir.resolve(), args.output_dir.resolve(), args.seed)
    if args.stage in {"render", "all"}:
        render(script, args.episode_dir.resolve(), args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
