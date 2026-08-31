from __future__ import annotations

import math
import re
from pathlib import Path

from novel_manga.voxcpm_voice import resolve_voice_profile


MIN_SPEECH_SPEED = 0.5
MAX_SPEECH_SPEED = 2.0
INDEXTTS_SYNTHESIS_TEXT_POLICY = "indextts-punctuation-pause-v1"


def indextts_synthesis_text(text: str) -> str:
    """Translate typography-only long pauses into stable spoken punctuation."""

    return re.sub(r"[—–]{2,}", "，", str(text))


def indextts_synthesis_identity(text: str) -> dict[str, str]:
    """Return a cache field only when the spoken text needs translation."""

    return (
        {"tts_synthesis_text_policy": INDEXTTS_SYNTHESIS_TEXT_POLICY}
        if indextts_synthesis_text(text) != str(text)
        else {}
    )


def speed_to_duration_factor(speed: float) -> float:
    """Translate the pipeline's speed multiplier to IndexTTS duration control.

    The public pipeline contract uses values above one for faster speech, while
    IndexTTS uses values above one for a longer/slower target duration.
    """

    value = float(speed)
    if not math.isfinite(value) or not MIN_SPEECH_SPEED <= value <= MAX_SPEECH_SPEED:
        raise ValueError(
            f"speech speed must be between {MIN_SPEECH_SPEED} and {MAX_SPEECH_SPEED}"
        )
    return 1.0 / value


def find_reference_audio(reference_dir: Path, voice: str | None) -> Path:
    """Resolve one stable pipeline voice to a mounted IndexTTS prompt audio."""

    requested = str(voice or "alloy").strip()
    profile = resolve_voice_profile(requested)
    stems = tuple(dict.fromkeys((requested, requested.casefold(), profile.key)))
    for stem in stems:
        for suffix in (".wav", ".flac", ".mp3"):
            candidate = reference_dir / f"{stem}{suffix}"
            if candidate.is_file() and candidate.stat().st_size > 44:
                return candidate
    expected = reference_dir / f"{profile.key}.wav"
    raise FileNotFoundError(
        f"IndexTTS reference audio is missing for voice {requested!r}; "
        f"mount a non-empty file at {expected}"
    )
