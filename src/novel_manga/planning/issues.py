"""Planning diagnostics carry machine identity and scope independently of their text."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class PlanningCode(Enum):
    NO_STAGES = ('no_stages', 'chapter')
    METHOD_CONTRACT = ('method_contract', 'chapter')
    QUOTE_TOO_SHORT = ('quote_too_short', 'stage')
    QUOTE_NOT_SOURCE = ('quote_not_source', 'stage')
    UNKNOWN_CHARACTERS = ('unknown_characters', 'clip')
    UNKNOWN_LOCATION = ('unknown_location', 'clip')
    VISIBLE_SPEAKER = ('visible_speaker', 'stage')
    OFFSCREEN_SPEAKER_MISSING = ('offscreen_speaker_missing', 'stage')
    OFFSCREEN_SPEAKER_UNKNOWN = ('offscreen_speaker_unknown', 'stage')
    SINGING_SPEAKER = ('singing_speaker', 'stage')
    SINGING_TEXT = ('singing_text', 'stage')
    CHAT_SPEAKER = ('chat_speaker', 'stage')
    DELIVERY_MODE = ('delivery_mode', 'stage')
    VISUAL_CONTENT = ('visual_content', 'stage')
    DURATION_BELOW_MINIMUM = ('duration_below_minimum', 'chapter')
    DURATION_ABOVE_MAXIMUM = ('duration_above_maximum', 'chapter')
    SKIPPED_SEGMENTS = ('skipped_segments', 'chapter')
    MISSING_CHAT = ('missing_chat', 'chapter')
    UNCITED_SEGMENT = ('uncited_segment', 'segment')
    BEAT_LINE_LOST = ('beat_line_lost', 'chapter')
    SCENE_OPENING_COPIED = ('scene_opening_copied', 'stage')
    HANDOFF_LINE_LOST = ('handoff_line_lost', 'chapter')
    RETAINED_LINE_LOST = ('retained_line_lost', 'chapter')
    STRICT_SPEECH_BELOW_MINIMUM = ('strict_speech_below_minimum', 'chapter')
    STRICT_SPEECH_ABOVE_MAXIMUM = ('strict_speech_above_maximum', 'chapter')

    def __init__(self, code, scope):
        self.code, self.scope = code, scope


@dataclass(frozen=True)
class PlanningIssue:
    code: PlanningCode
    detail: str
    stage: str | None = None
    segment_id: str | None = None
    field: str | None = None

    @property
    def message(self) -> str:
        return f'{self.stage}: {self.detail}' if self.stage else self.detail


@dataclass
class ValidationResult:
    issues: list[PlanningIssue]
    warnings: list[str]
    shots: list[dict]

    @property
    def errors(self) -> list[str]:
        return [issue.message for issue in self.issues]
