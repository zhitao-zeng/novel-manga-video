from pathlib import Path

from novel_manga.config import Settings
from novel_manga.creative_direction import (
    SHORT_DRAMA_PROFILE,
    apply_creative_direction,
    infer_visual_strategy,
)
from novel_manga.models import (
    ChapterDiagnosis,
    ChapterEvent,
    Episode,
    EpisodeDramaturgy,
    EpisodePlan,
    ScriptTurn,
    Shot,
    StoryBible,
    VisualStrategy,
)
from novel_manga.production_models import AssetRecord, RuntimeUnit, SeriesAssetManifest
from novel_manga.production_runtime import EpisodeProductionRuntime
from novel_manga.script_planning import evaluate_script_quality, normalize_chronological_plan


def _bible() -> StoryBible:
    return StoryBible(
        novel_title="测试",
        genre="东方玄幻",
        visual_style="国漫",
        palette="冷青与暖金",
        characters=[],
        locations=["测试广场"],
        style_fingerprint="creative-test",
    )


def _diagnosis() -> tuple[Episode, ChapterDiagnosis]:
    rows = ["少年走上测试广场。", "石碑显出最低结果。", "他抬眼看向嘲笑的人群。"]
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text="\n".join(rows),
        text_count=sum(len(row) for row in rows),
        source_start=0,
        source_end=sum(len(row) for row in rows),
    )
    events = [
        ChapterEvent(
            event_id=f"event_{index:03d}",
            order=index,
            description=row,
            source_quote=row,
            importance="critical",
            narrative_role=("setup", "turning_point", "resolution")[index - 1],
            causes=[f"event_{index - 1:03d}"] if index > 1 else [],
        )
        for index, row in enumerate(rows, 1)
    ]
    return episode, ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event=rows[1],
        chapter_start_state=rows[0],
        chapter_end_state=rows[-1],
        episode_state_change=rows[-1],
        strongest_hook_candidate=rows[1],
        hook_source_quote=rows[1],
        ending_type="consequence",
        events=events,
    )


def _shot(index: int, text: str, event_id: str, *, characters: list[str] | None = None) -> Shot:
    return Shot(
        index=index,
        narration=text,
        subtitle=text,
        visual_prompt=text,
        motion_prompt="人物听见结果后作出一个清楚反应",
        characters=characters or [],
        location="测试广场",
        source_quote=text,
        event_ids=[event_id],
        turns=[ScriptTurn(text=text, source_quote=text)],
    )


def test_visual_strategy_uses_assets_until_blocking_or_reveal_requires_keyframe() -> None:
    solo = _shot(1, "少年低头。", "event_001", characters=["少年"])
    empty = _shot(1, "广场骤然安静。", "event_001")
    reveal = _shot(1, "测试结果出现。", "event_001", characters=["少年"])
    multi = _shot(1, "两人在人群前对峙。", "event_001", characters=["少年", "少女"])

    assert infer_visual_strategy(solo) == (VisualStrategy.DIRECT_ASSETS, [])
    assert infer_visual_strategy(empty) == (VisualStrategy.SCENE_ONLY, [])
    assert infer_visual_strategy(reveal)[0] == VisualStrategy.STORY_KEYFRAME
    assert infer_visual_strategy(multi)[0] == VisualStrategy.STORY_KEYFRAME


def test_adaptive_profile_keeps_result_first_replay_and_audits_narration_budget() -> None:
    episode, diagnosis = _diagnosis()
    cold = _shot(1, diagnosis.events[1].source_quote, "event_002")
    cause = _shot(2, diagnosis.events[0].source_quote, "event_001")
    replay = _shot(3, diagnosis.events[1].source_quote, "event_002")
    ending = _shot(4, diagnosis.events[2].source_quote, "event_003")
    plan = EpisodePlan(
        video_title="最低结果",
        hook="他为什么跌到最低？",
        summary="结果前置测试",
        shots=[cold, cause, replay, ending],
        adaptation_ledger=[
            {
                "event_id": event.event_id,
                "disposition": "preserved",
                "shot_indexes": [
                    shot.index for shot in (cold, cause, replay, ending) if event.event_id in shot.event_ids
                ],
                "rationale": "测试",
            }
            for event in diagnosis.events
        ],
        creative_profile=SHORT_DRAMA_PROFILE,
        dramaturgy=EpisodeDramaturgy(
            genre_engine="status-power-mystery",
            dramatic_question="少年为什么跌到最低？",
            cold_open=diagnosis.events[1].description,
            cold_open_source_quote=diagnosis.events[1].source_quote,
            status_before=diagnosis.chapter_start_state,
            status_after=diagnosis.chapter_end_state,
            conflict_beats=[event.description for event in diagnosis.events],
            reveal_order=[event.event_id for event in diagnosis.events],
            cliffhanger=diagnosis.chapter_end_state,
            narration_budget_ratio=0.2,
        ),
    )

    directed = apply_creative_direction(
        plan, diagnosis, _bible(), profile=SHORT_DRAMA_PROFILE
    )
    normalized = normalize_chronological_plan(directed, diagnosis, episode)
    report = evaluate_script_quality(normalized, diagnosis, episode)

    assert [shot.event_ids for shot in normalized.shots] == [
        ["event_002"],
        ["event_001"],
        ["event_002"],
        ["event_003"],
    ]
    assert report.cold_open_grounded is True
    assert report.narration_ratio == 1.0
    assert "narration_budget_exceeded" in {issue.code for issue in report.issues}


def test_adaptive_h3_scene_only_uses_empty_location_without_generating_keyframe(
    tmp_path: Path,
) -> None:
    location = tmp_path / "series_assets/locations/location_001/establishing.jpeg"
    location.parent.mkdir(parents=True)
    location.write_bytes(b"jpeg")
    assets = SeriesAssetManifest(
        style_fingerprint="test",
        characters=[],
        locations=[
            AssetRecord(
                asset_id="location_001",
                kind="location",
                name="测试广场",
                spec_path="series_assets/locations/location_001/spec.json",
                primary_image="series_assets/locations/location_001/establishing.jpeg",
                prompt_sha256="test",
            )
        ],
        voice_assignments={"narrator": "alloy"},
    )
    unit = RuntimeUnit(
        unit_id="visual_001",
        episode_id="1_1",
        scene_id="scene_001",
        shot_id="shot_001",
        shot_index=1,
        turn_index=1,
        role="narrator",
        speaker_name="旁白",
        speaking=False,
        text="广场骤然安静。",
        emotion="压抑",
        source_quote="广场骤然安静。",
        location_asset_id="location_001",
        voice="alloy",
        visual_prompt="空广场",
        motion_prompt="风吹动旗帜",
        keyframe_prompt="空广场",
        visual_strategy=VisualStrategy.SCENE_ONLY,
        audio_path="work/audio.wav",
        keyframe_path="work/keyframe.jpeg",
        raw_video_path="work/video.mp4",
        segment_path="work/segment.mp4",
    )
    runtime = EpisodeProductionRuntime(
        Settings(
            provider="command",
            video_model="MiniMax-H3-Ref2VA",
            video_command="/models/h3-video",
            local_visual_strategy="adaptive",
        ),
        None,
        None,
        None,
        None,
    )  # type: ignore[arg-type]

    assert runtime._direct_h3_assets(tmp_path, unit, assets) == (location, ())
