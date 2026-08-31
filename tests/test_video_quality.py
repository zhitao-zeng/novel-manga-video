import json
import subprocess
from pathlib import Path

from novel_manga.production_models import (
    ProductionPlan,
    RuntimeScene,
    RuntimeShot,
    RuntimeUnit,
    RuntimeVisualGroup,
)
from novel_manga.video_quality import evaluate_generated_video_quality


def _plan() -> ProductionPlan:
    unit = RuntimeUnit(
        unit_id="shot_001_turn_01",
        episode_id="episode_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="林晚",
        speaker_name="林晚",
        speaking=True,
        delivery_mode="visible_dialogue",
        text="别开门。",
        emotion="紧张",
        source_quote="别开门。",
        character_asset_ids=["character_001", "character_002"],
        location_asset_id="location_001",
        voice="native",
        visual_prompt="林晚挡住周宇",
        motion_instruction="林晚向前半步挡住周宇",
        motion_prompt="林晚向前半步挡住周宇并说：别开门。",
        keyframe_prompt="两人同框",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/raw.mp4",
        segment_path="work/segment.mp4",
    )
    return ProductionPlan(
        video_id="episode_1",
        source_title="第一章",
        source_text_sha256="source",
        style_fingerprint="style",
        scenes=[
            RuntimeScene(
                scene_id="scene_001",
                index=1,
                location_asset_id="location_001",
                narrative_job="冲突",
                shot_ids=["shot_001"],
            )
        ],
        shots=[
            RuntimeShot(
                shot_id="shot_001",
                scene_id="scene_001",
                index=1,
                narrative_job="冲突",
                location_asset_id="location_001",
                source_quote="别开门。",
                unit_ids=[unit.unit_id],
            )
        ],
        units=[unit],
        visual_groups=[
            RuntimeVisualGroup(
                group_id="visual_001",
                scene_id="scene_001",
                shot_ids=["shot_001"],
                unit_ids=[unit.unit_id],
                location_asset_id="location_001",
                character_asset_ids=["character_001", "character_002"],
                spatial_anchor="门口行动轴",
                combined_text="别开门。",
                keyframe_prompt="两人同框",
                motion_prompt="林晚向前半步挡住周宇",
                keyframe_path="work/keyframe.jpeg",
                raw_video_path="work/raw.mp4",
                segment_path="work/segment.mp4",
            )
        ],
    )


def _manifest() -> dict:
    return {
        "groups": [
            {
                "group_id": "visual_001",
                "generation_duration": 4.0,
                "references": [
                    {
                        "asset_id": "character_001",
                        "role": "character_identity_costume",
                    },
                    {
                        "asset_id": "character_002",
                        "role": "character_identity_costume",
                    },
                    {"asset_id": "location_001", "role": "location_space_lighting"},
                ],
            }
        ]
    }


def _make_clip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x96:r=25:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    path.with_suffix(path.suffix + ".request.json").write_text(
        json.dumps({"workflow": "video-model-native-dialogue-v1"}),
        encoding="utf-8",
    )


def test_video_side_gate_requires_action_and_exact_visible_cast(tmp_path: Path) -> None:
    _make_clip(tmp_path / "work/raw.mp4")
    review = {
        "groups": [
            {
                "group_id": "visual_001",
                "action_observed": True,
                "visible_asset_ids": ["character_001", "character_002"],
                "unexpected_named_characters": [],
                "unexpected_objects": [],
                "screen_direction_ok": True,
                "identity_consistency_score": 0.2,
            }
        ]
    }

    passed = evaluate_generated_video_quality(
        episode_dir=tmp_path,
        manifest=_manifest(),
        plan=_plan(),
        director_review=review,
        report_path=tmp_path / "passed.json",
    )
    assert passed["passed"] is True
    assert passed["multi_character_same_frame_ratio"] == 1.0
    assert passed["identity_consistency_monitor"]["blocking"] is False

    review["groups"][0].update(
        action_observed=False,
        visible_asset_ids=["character_001"],
        unexpected_objects=["凭空出现的书"],
    )
    failed = evaluate_generated_video_quality(
        episode_dir=tmp_path,
        manifest=_manifest(),
        plan=_plan(),
        director_review=review,
        report_path=tmp_path / "failed.json",
    )
    assert failed["passed"] is False
    assert "visual_001:planned-action-not-observed" in failed["failures"]
    assert "visual_001:visible-cast-mismatch" in failed["failures"]
    assert "visual_001:unexpected-object" in failed["failures"]
