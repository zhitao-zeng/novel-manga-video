#!/usr/bin/env python3
"""Prepare a Qwen planning bundle for safe reuse of an admitted episode.

The script performs no model inference and no media API calls. It locks an
existing series bible, canonicalizes character aliases, proves unit-level reuse,
and seeds only byte-identical Qwen TTS source audio.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from novel_manga.models import EpisodePlan, StoryBible
from novel_manga.multivoice import (
    MultivoiceScript,
    MultivoiceShot,
    SpeechTurn,
    VoiceProfile,
)
from novel_manga.reuse import (
    canonicalize_plan_to_bible,
    flatten_plan,
    match_reusable_units,
)
from novel_manga.util import atomic_write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source-novel-dir", type=Path, required=True)
    parser.add_argument("--output-novel-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    bundle = args.bundle.resolve()
    source_novel_dir = args.source_novel_dir.resolve()
    output_novel_dir = args.output_novel_dir.resolve()
    # The admitted source episode is resolved from its production plan instead
    # of relying on a naming convention.
    candidates = sorted(source_novel_dir.glob("*/production_plan.json"))
    if len(candidates) != 1:
        raise ValueError("source novel directory must contain exactly one admitted production plan")
    source_episode_dir = candidates[0].parent

    planner_bible = StoryBible.model_validate_json(
        (bundle / "story_bible.json").read_text(encoding="utf-8")
    )
    target_raw = EpisodePlan.model_validate_json(
        (bundle / "episode_001_plan.json").read_text(encoding="utf-8")
    )
    locked_bible = StoryBible.model_validate_json(
        (source_novel_dir / "story_bible.json").read_text(encoding="utf-8")
    )
    source_plan = EpisodePlan.model_validate_json(
        (source_episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    target_plan = canonicalize_plan_to_bible(target_raw, locked_bible)
    matches = match_reusable_units(target_plan, source_plan)

    source_qwen = json.loads(
        (source_novel_dir / "qwen_multivoice_script.json").read_text(encoding="utf-8")
    )
    source_qwen_turns = {}
    for shot in source_qwen["shots"]:
        for turn_index, turn in enumerate(shot["turns"], start=1):
            source_qwen_turns[f"shot_{shot['index']:03d}_turn_{turn_index:02d}"] = turn
    source_profiles = {
        role: VoiceProfile.model_validate(profile)
        for role, profile in source_qwen["voices"].items()
    }
    target_units = {unit.unit_id: unit for unit in flatten_plan(target_plan)}
    match_by_target = {item["target_unit_id"]: item for item in matches}
    target_voices: dict[str, VoiceProfile] = {}
    for match in matches:
        target_unit = target_units[match["target_unit_id"]]
        source_speech = source_qwen_turns[match["source_unit_id"]]
        profile = source_profiles[source_speech["role"]]
        existing = target_voices.get(target_unit.turn.role)
        if existing is not None and existing != profile:
            raise ValueError(f"role {target_unit.turn.role!r} maps to multiple Qwen speakers")
        target_voices[target_unit.turn.role] = profile

    multivoice = MultivoiceScript(
        video_id=args.video_id,
        model=source_qwen["model"],
        language=source_qwen["language"],
        voices=target_voices,
        shots=[
            MultivoiceShot(
                index=shot.index,
                turns=[
                    SpeechTurn(role=turn.role, text=turn.text, pause_after=0.10)
                    for turn in shot.turns
                ],
            )
            for shot in target_plan.shots
        ],
    )

    output_novel_dir.mkdir(parents=True, exist_ok=True)
    output_episode_dir = output_novel_dir / args.video_id
    output_episode_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output_novel_dir / "planner_story_bible.json",
        planner_bible.model_dump(mode="json"),
    )
    atomic_write_json(
        output_novel_dir / "story_bible.json", locked_bible.model_dump(mode="json")
    )
    atomic_write_json(
        output_episode_dir / "episode_plan.json", target_plan.model_dump(mode="json")
    )
    atomic_write_json(
        output_novel_dir / "qwen_multivoice_script.json",
        multivoice.model_dump(mode="json"),
    )

    seeded_audio = []
    target_raw_dir = output_novel_dir / "qwen_audio_raw" / "turn_audio"
    target_raw_dir.mkdir(parents=True, exist_ok=True)
    source_raw_dir = source_novel_dir / "qwen_audio_raw" / "turn_audio"
    for target_unit_id, target_unit in target_units.items():
        match = match_by_target[target_unit_id]
        if not match["text_equal"]:
            continue
        source_audio = source_raw_dir / f"{match['source_unit_id']}.wav"
        target_audio = target_raw_dir / f"{target_unit_id}.wav"
        if not source_audio.is_file():
            raise FileNotFoundError(source_audio)
        shutil.copy2(source_audio, target_audio)
        seeded_audio.append(
            {
                "target_unit_id": target_unit_id,
                "source_unit_id": match["source_unit_id"],
                "source_sha256": sha256_file(source_audio),
                "target_sha256": sha256_file(target_audio),
            }
        )

    reuse_manifest = {
        "schema_version": 1,
        "contract": "novel-manga-safe-reuse/v1",
        "source_novel_dir": str(source_novel_dir),
        "source_episode_dir": str(source_episode_dir),
        "target_novel_dir": str(output_novel_dir),
        "target_episode_dir": str(output_episode_dir),
        "video_id": args.video_id,
        "planner_story_bible_sha256": sha256_file(output_novel_dir / "planner_story_bible.json"),
        "locked_story_bible_sha256": sha256_file(output_novel_dir / "story_bible.json"),
        "target_plan_sha256": sha256_file(output_episode_dir / "episode_plan.json"),
        "unit_count": len(matches),
        "visible_dialogue_count": sum(bool(item["speaking"]) for item in matches),
        "visual_reuse_count": len(matches),
        "exact_audio_seed_count": len(seeded_audio),
        "qwen_tts_generation_count": len(matches) - len(seeded_audio),
        "matches": matches,
        "seeded_audio": seeded_audio,
    }
    atomic_write_json(output_novel_dir / "reuse_manifest.json", reuse_manifest)
    print(
        json.dumps(
            {
                key: reuse_manifest[key]
                for key in (
                    "unit_count",
                    "visible_dialogue_count",
                    "visual_reuse_count",
                    "exact_audio_seed_count",
                    "qwen_tts_generation_count",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
