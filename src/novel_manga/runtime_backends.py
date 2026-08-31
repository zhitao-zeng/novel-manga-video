from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .config import Settings
from .util import media_duration


SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")


def normalize_text(text: str) -> str:
    return "".join(
        character
        for character in text.casefold()
        if character.isalnum() or "\u3400" <= character <= "\u9fff"
    )


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def correct_protected_lexicon(
    hypothesis: str,
    reference: str,
    canonical_terms: list[str],
    aliases: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, str | int]]]:
    """Correct only named terms that the script contract explicitly expects."""

    corrected = hypothesis
    corrections: list[dict[str, str | int]] = []
    for source, canonical in (aliases or {}).items():
        if canonical not in reference or source not in corrected or source == canonical:
            continue
        count = corrected.count(source)
        corrected = corrected.replace(source, canonical)
        corrections.append(
            {
                "source": source,
                "canonical": canonical,
                "distance": edit_distance(normalize_text(source), normalize_text(canonical)),
                "count": count,
            }
        )
    for canonical in sorted(set(canonical_terms), key=len, reverse=True):
        if len(canonical) < 2 or canonical not in reference or canonical in corrected:
            continue
        expected = reference.find(canonical) / max(1, len(reference))
        center = round(expected * len(corrected))
        radius = max(5, len(canonical) * 3)
        best: tuple[int, int, int, str] | None = None
        best_start = 0
        best_width = 0
        for width in range(max(1, len(canonical) - 1), len(canonical) + 2):
            start_min = max(0, center - radius)
            start_max = min(len(corrected) - width, center + radius)
            for start in range(start_min, start_max + 1):
                candidate = corrected[start : start + width]
                normalized = normalize_text(candidate)
                if not normalized:
                    continue
                distance = edit_distance(normalized, normalize_text(canonical))
                rank = (distance, abs(start - center), abs(width - len(canonical)), candidate)
                if best is None or rank < best:
                    best = rank
                    best_start = start
                    best_width = width
        threshold = max(1, len(normalize_text(canonical)) // 3)
        if best is None or best[0] > threshold:
            continue
        source = corrected[best_start : best_start + best_width]
        corrected = (
            corrected[:best_start] + canonical + corrected[best_start + best_width :]
        )
        corrections.append(
            {
                "source": source,
                "canonical": canonical,
                "distance": best[0],
                "count": 1,
            }
        )
    return corrected, corrections


def measured_speech_bounds(audio: Path) -> tuple[float, float]:
    seconds = media_duration(audio)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio),
            "-af",
            "silencedetect=noise=-38dB:d=0.08",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    events = [(kind, float(value)) for kind, value in SILENCE_EVENT.findall(result.stderr)]
    start = 0.0
    end = seconds
    if len(events) >= 2 and events[0][0] == "start" and events[0][1] <= 0.02 and events[1][0] == "end":
        start = events[1][1]
    for index, (kind, value) in enumerate(events):
        if kind == "start" and index + 1 < len(events) and events[index + 1][0] == "end":
            if events[index + 1][1] >= seconds - 0.08:
                end = value
    if end <= start + 0.1:
        return 0.0, seconds
    return round(start, 6), round(end, 6)


class RuntimeEvidenceBackends:
    """Provider-neutral command adapter for ASR evidence."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _invoke(command: str, arguments: list[str]) -> dict:
        with tempfile.TemporaryDirectory(prefix="novel-evidence-") as directory:
            output = Path(directory) / "result.json"
            subprocess.run(
                shlex.split(command) + arguments + ["--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            if not output.is_file():
                raise RuntimeError("evidence command did not create its JSON output")
            return json.loads(output.read_text(encoding="utf-8"))

    def transcribe(self, unit_id: str, reference: str, audio: Path) -> dict:
        segments: list[dict] = []
        speaker_count: int | None = None
        speaker_ids: list[str] = []
        if self.settings.admission_mode == "preview" and not self.settings.asr_command:
            hypothesis = reference
            backend = "mock-exact-preview"
        else:
            if not self.settings.asr_command:
                return {
                    "unit_id": unit_id,
                    "reference": reference,
                    "hypothesis": "",
                    "cer": math.inf,
                    "status": "inconclusive",
                    "backend": None,
                    "error": "NOVEL_ASR_COMMAND is missing",
                }
            try:
                result = self._invoke(
                    self.settings.asr_command,
                    ["--unit-id", unit_id, "--audio", str(audio), "--text", reference],
                )
            except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as error:
                return {
                    "unit_id": unit_id,
                    "reference": reference,
                    "hypothesis": "",
                    "cer": 999.0,
                    "status": "inconclusive",
                    "backend": None,
                    "error": f"{type(error).__name__}: ASR backend failed",
                }
            hypothesis = str(result.get("hypothesis", result.get("text", "")))
            backend = str(result.get("backend", "external-command"))
            raw_segments = result.get("segments", result.get("events", []))
            if isinstance(raw_segments, list):
                segments = [row for row in raw_segments if isinstance(row, dict)]
            raw_speakers = result.get("speakers", result.get("speaker_ids", []))
            if isinstance(raw_speakers, list):
                speaker_ids = list(
                    dict.fromkeys(str(value) for value in raw_speakers)
                )
            if result.get("speaker_count") is not None:
                speaker_count = int(result["speaker_count"])
            elif speaker_ids:
                speaker_count = len(speaker_ids)
        reference_normalized = normalize_text(reference)
        hypothesis_normalized = normalize_text(hypothesis)
        errors = edit_distance(reference_normalized, hypothesis_normalized)
        return {
            "unit_id": unit_id,
            "reference": reference,
            "hypothesis": hypothesis,
            "reference_normalized": reference_normalized,
            "hypothesis_normalized": hypothesis_normalized,
            "errors": errors,
            "reference_chars": len(reference_normalized),
            "cer": round(errors / max(1, len(reference_normalized)), 6),
            "status": "passed",
            "backend": backend,
            "segments": segments,
            "speaker_count": speaker_count,
            "speaker_ids": speaker_ids,
        }

def aggregate_asr(rows: list[dict]) -> dict:
    errors = sum(int(row.get("errors", 0)) for row in rows if math.isfinite(float(row.get("cer", math.inf))))
    reference_chars = sum(
        int(row.get("reference_chars", len(normalize_text(str(row.get("reference", ""))))))
        for row in rows
    )
    incomplete = any(row.get("status") != "passed" for row in rows)
    return {
        "recognizer": next((row.get("backend") for row in rows if row.get("backend")), None),
        "turn_count": len(rows),
        "total_errors": errors,
        "reference_chars": reference_chars,
        "cer": 999.0 if incomplete else round(errors / max(1, reference_chars), 6),
        "turns": rows,
    }
