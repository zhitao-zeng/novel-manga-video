"""Load chapter evidence before applying shared dialogue rules."""
from pathlib import Path
from novel_manga.story.dialogue import (
    POLICY, apply_bindings, clip_bindings, final_dialogue_issues, subject_map,
    confirmed_bindings as resolve_bindings,
)


def confirmed_bindings(directory: Path, shots: list[dict]) -> dict:
    from story_identity import read, current_context
    return resolve_bindings(shots, read(directory / 'source_speaker_contract.json', []),
                            current_context(directory), read(directory / 'segments.json', []))


def apply_confirmed_speakers(directory: Path, shots: list[dict]) -> dict:
    return apply_bindings(shots, confirmed_bindings(directory, shots))
