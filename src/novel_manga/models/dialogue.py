from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



class TurnDelivery(StrEnum):
    """How an utterance is heard and whether it should drive visible lips."""

    NARRATION = "narration"
    VISIBLE_DIALOGUE = "visible_dialogue"
    OFFSCREEN_DIALOGUE = "offscreen_dialogue"
    INNER_VOICE = "inner_voice"
    TITLE_CARD = "title_card"
    SILENT_ACTION = "silent_action"



class TurnDerivation(StrEnum):
    """How a turn's text relates to the chapter text it is grounded in.

    ``VERBATIM`` copies a quoted line out of the chapter.  ``DERIVED`` stages a
    narrated passage as dialogue, action or reaction; it stays bound to the
    narration it came from and may not introduce facts that narration does not
    already carry.  Without this distinction the only legal move for a narrated
    passage is to become explanatory narration, which the short-drama profile
    then budgets away, and the chapter's causal tissue disappears.
    """

    VERBATIM = "verbatim"
    ABRIDGED = "abridged"
    DERIVED = "derived"



class TurnDevice(StrEnum):
    LISTENER_QA = "listener_qa"
    CROWD_PROXY = "crowd_proxy"
    HALF_LINE = "half_line"
    EVIDENCE_OBJECT = "evidence_object"
    SPATIAL = "spatial"
    CONSEQUENCE = "consequence"
    INNER_VOICE = "inner_voice"
    NARRATION = "narration"



class ScriptTurn(BaseModel):
    """One semantic utterance; subtitle pagination is deliberately separate."""

    role: str = "narrator"
    speaker_name: str = "旁白"
    text: str = Field(min_length=1, max_length=500)
    speaking: bool = False
    delivery_mode: TurnDelivery | None = None
    emotion: str = "克制自然"
    source_quote: str = Field(default="", max_length=500)
    derivation: TurnDerivation = TurnDerivation.VERBATIM
    device: TurnDevice | None = None
    serves: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_narrator_role(cls, data):
        if isinstance(data, dict):
            role = str(data.get("role", "")).strip()
            speaker = str(data.get("speaker_name", "")).strip()
            delivery = str(data.get("delivery_mode", "")).strip()
            if delivery == TurnDelivery.SILENT_ACTION and role in {
                "",
                "narrator",
                "旁白",
            }:
                return {**data, "role": "action", "speaker_name": ""}
            if (
                role in {"", "narrator", "旁白"}
                and speaker not in {"", "narrator", "旁白"}
                and delivery
                in {
                    TurnDelivery.VISIBLE_DIALOGUE,
                    TurnDelivery.OFFSCREEN_DIALOGUE,
                    "visible_dialogue",
                    "offscreen_dialogue",
                }
            ):
                return {**data, "role": speaker}
            if role == "旁白":
                return {**data, "role": "narrator"}
        return data

    @model_validator(mode="after")
    def validate_speaker(self) -> "ScriptTurn":
        if "derivation" not in self.model_fields_set and self.role == "narrator":
            # Narration is a retelling by construction; only a character line
            # can meaningfully claim to be a verbatim copy of a quoted line.
            self.derivation = TurnDerivation.DERIVED
        if self.delivery_mode is None:
            if self.role == "narrator":
                self.delivery_mode = TurnDelivery.NARRATION
            elif self.speaking:
                self.delivery_mode = TurnDelivery.VISIBLE_DIALOGUE
            else:
                self.delivery_mode = TurnDelivery.OFFSCREEN_DIALOGUE
        # speaking and delivery_mode encode the same fact twice, and planners
        # routinely disagree with themselves across the two.  delivery_mode is
        # the richer field, so let it decide and reconcile the boolean, rather
        # than spending a revision round on a contradiction that carries no
        # information.
        if self.delivery_mode is not None:
            self.speaking = self.delivery_mode == TurnDelivery.VISIBLE_DIALOGUE
        if self.role == "narrator" and self.speaking:
            raise ValueError("narrator turns cannot be visible speaking turns")
        if (
            self.delivery_mode != TurnDelivery.SILENT_ACTION
            and self.role != "narrator"
            and not self.speaker_name.strip()
        ):
            raise ValueError("character voice turns require speaker_name")
        if self.role == "narrator" and self.delivery_mode not in {
            TurnDelivery.NARRATION,
            TurnDelivery.TITLE_CARD,
        }:
            raise ValueError("narrator turns must use narration or title_card delivery")
        if self.speaking and self.delivery_mode != TurnDelivery.VISIBLE_DIALOGUE:
            raise ValueError("visible speaking turns must use visible_dialogue delivery")
        if not self.speaking and self.role != "narrator" and self.delivery_mode not in {
            TurnDelivery.OFFSCREEN_DIALOGUE,
            TurnDelivery.INNER_VOICE,
            TurnDelivery.SILENT_ACTION,
        }:
            raise ValueError(
                "non-visible character turns must use offscreen_dialogue, inner_voice, or silent_action"
            )
        invalid_serves = [
            value
            for value in self.serves
            if not re.fullmatch(r"(?:event|fact)_\d{3}", value)
        ]
        if invalid_serves:
            raise ValueError(f"invalid serves ids: {invalid_serves}")
        if (
            self.device == TurnDevice.NARRATION
            and self.delivery_mode != TurnDelivery.NARRATION
        ):
            raise ValueError("narration device requires narration delivery")
        if (
            self.device == TurnDevice.INNER_VOICE
            and self.delivery_mode != TurnDelivery.INNER_VOICE
        ):
            raise ValueError("inner_voice device requires inner_voice delivery")
        return self


# Resolve the existing forward references within this model group.
ScriptTurn.model_rebuild()
