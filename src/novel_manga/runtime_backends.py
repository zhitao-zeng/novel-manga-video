from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .config import Settings
from .sd_dialogue import PUNCTUATION, timed_subtitle_pages
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


def merge_punctuation_only_events(events: list[dict]) -> list[dict]:
    """Attach aligner-created punctuation pages to the preceding subtitle."""
    repaired: list[dict] = []
    for source in events:
        event = dict(source)
        text = str(event.get("text", ""))
        visible = text.replace(r"\N", "")
        if repaired and visible and all(char in PUNCTUATION for char in visible):
            repaired[-1]["text"] = str(repaired[-1]["text"]) + visible
            repaired[-1]["end"] = event["end"]
            continue
        repaired.append(event)
    return repaired


class RuntimeEvidenceBackends:
    """Provider-neutral command adapters for subtitle alignment and ASR evidence."""

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

    def align(self, unit_id: str, text: str, audio: Path) -> dict:
        if self.settings.align_command:
            result = self._invoke(
                self.settings.align_command,
                ["--unit-id", unit_id, "--audio", str(audio), "--text", text],
            )
            events = merge_punctuation_only_events(result.get("events", []))
            reconstructed = "".join(str(event.get("text", "")).replace(r"\N", "") for event in events)
            if normalize_text(reconstructed) != normalize_text(text):
                raise ValueError(f"aligner changed locked text for {unit_id}")
            if not events or any(float(item["end"]) <= float(item["start"]) for item in events):
                raise ValueError(f"aligner returned invalid events for {unit_id}")
            return {
                "unit_id": unit_id,
                "backend": result.get("backend", "external-command"),
                "evidence": "forced_alignment",
                "speech_start": float(result.get("speech_start", events[0]["start"])),
                "speech_end": float(result.get("speech_end", events[-1]["end"])),
                "events": events,
            }
        start, end = measured_speech_bounds(audio)
        return {
            "unit_id": unit_id,
            "backend": "ffmpeg-silencedetect",
            "evidence": "coarse_audio_bounds_with_character_weighted_pages",
            "speech_start": start,
            "speech_end": end,
            "events": timed_subtitle_pages(text, start, end),
        }

    def transcribe(self, unit_id: str, reference: str, audio: Path) -> dict:
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
