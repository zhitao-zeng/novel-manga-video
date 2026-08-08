from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class VoiceProfile(BaseModel):
    speaker: str = Field(min_length=1)
    instruct: str = ""


class SpeechTurn(BaseModel):
    role: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    pause_after: float = Field(default=0.18, ge=0.0, le=2.0)


class MultivoiceShot(BaseModel):
    index: int = Field(ge=1)
    turns: list[SpeechTurn] = Field(min_length=1)


class MultivoiceScript(BaseModel):
    video_id: str = Field(min_length=1)
    model: str = "Qwen3-TTS-12Hz-1.7B-CustomVoice"
    language: str = "Chinese"
    voices: dict[str, VoiceProfile] = Field(min_length=1)
    shots: list[MultivoiceShot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "MultivoiceScript":
        indexes = [shot.index for shot in self.shots]
        expected = list(range(1, len(self.shots) + 1))
        if indexes != expected:
            raise ValueError(f"shot indexes must be consecutive: {indexes}")
        unknown = sorted({turn.role for shot in self.shots for turn in shot.turns} - self.voices.keys())
        if unknown:
            raise ValueError(f"speech turns reference undefined roles: {unknown}")
        return self

    @property
    def speaker_count(self) -> int:
        return len({profile.speaker.casefold() for profile in self.voices.values()})

    @property
    def turn_count(self) -> int:
        return sum(len(shot.turns) for shot in self.shots)


def load_multivoice_script(path: str | Path) -> MultivoiceScript:
    return MultivoiceScript.model_validate_json(Path(path).read_text(encoding="utf-8"))


def subtitle_pages(text: str, chars_per_line: int = 18) -> list[str]:
    clean = "".join(text.split())
    chunks = [clean[i:i + chars_per_line] for i in range(0, len(clean), chars_per_line)] or [""]
    return [r"\N".join(chunks[i:i + 2]) for i in range(0, len(chunks), 2)]


def write_script(path: str | Path, script: MultivoiceScript) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(script.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
