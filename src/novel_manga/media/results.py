"""The existing media dictionaries with explicit partial-take and analysis contracts."""
from __future__ import annotations
from typing import TypedDict


class SelectedTake(TypedDict, total=False):
    video: str
    postmix_video: str
    passed: bool
    duration: float
    chunks: list[dict]
    issues: list[str]
    speech_issues: list[str]
    generated_this_run: bool


class ClipAnalysis(SelectedTake):
    clip_id: str
    chunks: list[dict]


class ClipResult(TypedDict, total=False):
    clip_id: str
    attempts: list[ClipAnalysis]
    selected: SelectedTake | None
    error: str
    retake_error: str
    blocked: list[str]


class AssemblyResult(TypedDict, total=False):
    final_video: str
    cover: str
    ending: str
    ass: str
    duration: float
    subtitle_events: int
    media_qc_passed: bool
    max_hold_seconds: float
    thin_passed: bool
    media_qc: dict
    silent_outro_seconds: float
    pending_publish: bool
    timing: dict[str, float]


def selected_video(selected: SelectedTake) -> str | None:
    return selected.get('postmix_video') or selected.get('video')


def has_analysis(selected: SelectedTake) -> bool:
    """A real empty ASR list is valid; a missing list requires the existing analyser."""
    return isinstance(selected.get('chunks'), list)
