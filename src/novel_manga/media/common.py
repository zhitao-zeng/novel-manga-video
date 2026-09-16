from __future__ import annotations
import hashlib
import re
import subprocess
import time
from pathlib import Path

def sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

def reference_digests(paths) -> list[str]:
    """Short content digests for the reference pictures sent with a clip."""
    out = []
    for path in paths:
        try:
            out.append(hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16])
        except OSError:
            out.append("")
    return out

def cover_title(source_title: str, video_title: str) -> str:
    """Prefer the source chapter title while removing only its ordinal prefix."""
    chapter_prefix = re.compile(
        r"^\s*(?:第[零〇一二三四五六七八九十百千万两\d]+[章节卷回集]|"
        r"chapter\s+\d+)\s*[:：\-—、.]?\s*",
        re.IGNORECASE,
    )
    source_candidate = chapter_prefix.sub("", source_title).strip()
    if source_candidate:
        return source_candidate
    video_candidate = video_title.rsplit("：", 1)[-1].rsplit(":", 1)[-1]
    video_candidate = chapter_prefix.sub("", video_candidate).strip()
    return video_candidate or source_title.strip() or "本集故事"


def audio_levels(path: Path) -> tuple[float | None, float | None]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    mean = re.search(r"mean_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB", result.stderr)
    peak = re.search(r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB", result.stderr)

    def parse(match: re.Match[str] | None) -> float | None:
        if match is None or match.group(1) in {"-inf", "inf"}:
            return None
        return float(match.group(1))

    return parse(mean), parse(peak)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
