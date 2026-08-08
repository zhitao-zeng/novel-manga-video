from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image

from .config import Settings
from .util import atomic_write_json


def _rate(value: str) -> float:
    numerator, denominator = value.split("/")
    return float(numerator) / max(1.0, float(denominator))


def inspect_media(video: Path, cover: Path, ending: Path, ass: Path, settings: Settings, report: Path) -> dict:
    checks: dict[str, dict] = {}
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video),
    ], capture_output=True, text=True)
    if probe.returncode != 0:
        result = {"passed": False, "checks": {"playable": {"passed": False, "detail": probe.stderr[-500:]}}}
        atomic_write_json(report, result)
        return result
    data = json.loads(probe.stdout)
    video_streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    stream = video_streams[0] if video_streams else {}
    fps = _rate(stream.get("avg_frame_rate", "0/1"))
    checks["playable"] = {"passed": bool(video_streams), "detail": "ffprobe decoded container metadata"}
    checks["resolution"] = {
        "passed": stream.get("width") == settings.width and stream.get("height") == settings.height,
        "detail": f"{stream.get('width')}x{stream.get('height')}",
    }
    checks["fps"] = {"passed": abs(fps - settings.fps) < 0.05, "detail": round(fps, 3)}
    checks["video_codec"] = {"passed": stream.get("codec_name") == "h264", "detail": stream.get("codec_name")}
    checks["audio"] = {"passed": bool(audio_streams), "detail": audio_streams[0].get("codec_name") if audio_streams else "missing"}
    checks["subtitles"] = {
        "passed": ass.is_file() and ass.read_text(encoding="utf-8").count("Dialogue:") > 0,
        "detail": "burned ASS source retained",
    }
    for label, image_path in (("cover", cover), ("ending_screen", ending)):
        try:
            with Image.open(image_path) as image:
                ok = image.format == "JPEG" and image.size == (settings.width, settings.height)
                detail = f"{image.format} {image.width}x{image.height}"
        except Exception as error:
            ok, detail = False, str(error)
        checks[label] = {"passed": ok, "detail": detail}

    black = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(video), "-vf", "blackdetect=d=1.0:pix_th=0.10", "-an", "-f", "null", "-",
    ], capture_output=True, text=True)
    durations = [float(item) for item in re.findall(r"black_duration:([0-9.]+)", black.stderr)]
    max_black = max(durations, default=0.0)
    checks["black_frames"] = {"passed": max_black < 1.0, "detail": {"max_black_seconds": max_black}}

    volume = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(video),
        "-af", (
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:"
            "stop_periods=-1:stop_duration=0.05:stop_threshold=-45dB,volumedetect"
        ),
        "-vn", "-sn", "-dn", "-f", "null", "-",
    ], capture_output=True, text=True)
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", volume.stderr)
    max_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", volume.stderr)
    mean_db = float(mean_match.group(1)) if mean_match else -100.0
    max_db = float(max_match.group(1)) if max_match else -100.0
    checks["audio_level"] = {
        "passed": -38.0 <= mean_db <= -8.0 and -20.0 <= max_db <= 0.0,
        "detail": {"active_voice_mean_db": mean_db, "max_db": max_db, "silence_removed_below_db": -45},
    }

    silence = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(video), "-af", "silencedetect=n=-45dB:d=2", "-vn", "-f", "null", "-",
    ], capture_output=True, text=True)
    silence_durations = [float(item) for item in re.findall(r"silence_duration:\s*([0-9.]+)", silence.stderr)]
    max_silence = max(silence_durations, default=0.0)
    checks["long_silence"] = {"passed": max_silence < 10.0, "detail": {"max_silence_seconds": max_silence}}

    freeze = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(video), "-vf", "freezedetect=n=-50dB:d=6", "-an", "-f", "null", "-",
    ], capture_output=True, text=True)
    freeze_durations = [float(item) for item in re.findall(r"freeze_duration:\s*([0-9.]+)", freeze.stderr)]
    max_freeze = max(freeze_durations, default=0.0)
    checks["long_freeze"] = {"passed": max_freeze < 8.0, "detail": {"max_freeze_seconds": max_freeze}}

    result = {"passed": all(item["passed"] for item in checks.values()), "checks": checks}
    atomic_write_json(report, result)
    return result
