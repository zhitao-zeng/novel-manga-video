from pathlib import Path

import pytest

from novel_manga.config import NATIVE_DIALOGUE_POLICY, Settings
from novel_manga.models import (
    AdaptationLedgerItem,
    CameraBeat,
    CameraPlan,
    ChapterDiagnosis,
    ChapterEvent,
    Character,
    Episode,
    EpisodeContract,
    EpisodePlan,
    HandoffState,
    MotionBeat,
    PerformancePlan,
    SceneAudioPlan,
    ScriptTurn,
    Shot,
    SpeechStrategy,
    StoryBible,
    TurnDelivery,
    TurnDerivation,
    TurnDevice,
)
from novel_manga.preflight import evaluate_production_preflight
from novel_manga.production import compile_production_plan
from novel_manga.production_models import AssetRecord, SeriesAssetManifest
from novel_manga.production_runtime import EpisodeProductionRuntime, build_visual_groups
from novel_manga.render import Renderer
from novel_manga.script_planning import (
    abridged_clause_subsequence,
    evaluate_script_quality,
)


def _bible() -> StoryBible:
    return StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="preflight-style",
    )


def _assets() -> SeriesAssetManifest:
    return SeriesAssetManifest(
        style_fingerprint="preflight-style",
        characters=[
            AssetRecord(
                asset_id="character_001",
                kind="character",
                name="甲",
                spec_path="series_assets/character.json",
                primary_image="series_assets/character.jpeg",
                prompt_sha256="fixture",
            )
        ],
        locations=[
            AssetRecord(
                asset_id="location_001",
                kind="location",
                name="门前",
                spec_path="series_assets/location.json",
                primary_image="series_assets/location.jpeg",
                prompt_sha256="fixture",
            )
        ],
        voice_assignments={"narrator": "narrator", "甲": "voice-1"},
    )


def _state(knowledge: str, physical: str) -> HandoffState:
    return HandoffState(
        knowledge={"甲": knowledge},
        power={"甲": "控制门口"},
        relationship={"甲-门外者": "对立"},
        physical={"甲": physical},
        ongoing_action="none",
    )


def _shot(index: int, text: str, open_state: HandoffState, close_state: HandoffState) -> Shot:
    return Shot(
        index=index,
        narration=text,
        subtitle=text,
        visual_prompt=f"甲在门前说{text}",
        motion_prompt="甲抬手挡门",
        characters=["甲"],
        location="门前",
        source_quote="甲说：“一。”甲又说：“二。”",
        scene_job="对峙",
        change=f"第{index}句让对峙升级",
        event_ids=["event_001"],
        turns=[
            ScriptTurn(
                role="甲",
                speaker_name="甲",
                text=text,
                speaking=True,
                delivery_mode=TurnDelivery.VISIBLE_DIALOGUE,
                source_quote="甲说：“一。”甲又说：“二。”",
                derivation=TurnDerivation.VERBATIM,
            )
        ],
        performance_plan=PerformancePlan(
            objective="挡住门",
            start_state="甲手臂垂下",
            motion_beats=[
                MotionBeat(
                    phase="development",
                    seconds=1.0,
                    actor="甲",
                    target="木门",
                    action_type="confront",
                    trigger="门外异响",
                    action="甲抬手挡门",
                    end_state="甲手臂横在门前",
                )
            ],
            end_state="甲手臂横在门前",
        ),
        camera_plan=CameraPlan(
            mode="motivated_subtle",
            motivation="随挡门动作收紧",
            action_axis="门轴同侧",
            screen_direction="甲保持画面左侧",
            start_position="正面中景",
            camera_beats=[
                CameraBeat(
                    phase="development",
                    trajectory="短距离慢推",
                    framing="收至胸像",
                    parallax="门框快于远墙",
                )
            ],
            end_position="正面胸像",
        ),
        audio_plan=SceneAudioPlan(speech_strategy=SpeechStrategy.NATIVE),
        script_open_state=open_state,
        script_close_state=close_state,
    )


def _v5_plan(*, mismatch: bool = False) -> tuple[Episode, EpisodePlan]:
    source = "甲说：“一。”甲又说：“二。”"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    middle = _state("知道门外有声音", "站在门前")
    second_open = (
        _state("错误地忘记门外声音", "站在门前") if mismatch else middle
    )
    plan = EpisodePlan(
        video_title="第一章",
        hook="门外是谁",
        summary="甲挡门",
        shots=[
            _shot(1, "一。", _state("尚未知情", "站在门内"), middle),
            _shot(2, "二。", second_open, _state("确认危险", "挡在门前")),
        ],
        adaptation_ledger=[
            AdaptationLedgerItem(
                event_id="event_001",
                disposition="preserved",
                shot_indexes=[1, 2],
                rationale="挡门事件",
            )
        ],
        episode_contract=EpisodeContract(
            episode_index=1,
            development_version="v001",
            arc_position="建立",
            pressure_loop="越挡门越受质疑",
            protagonist_default_strategy="先控制风险",
            strategy_creates_problem="引来怀疑",
            pressure_step="第一次挡门",
            allowed_event_ids=["event_001"],
            retention_beat_ids=["beat_001"],
            required_close_state="确认危险",
        ),
    )
    return episode, plan


def test_preflight_compares_five_dimension_handoffs_and_builds_vlm_questions() -> None:
    episode, episode_plan = _v5_plan()
    production = compile_production_plan(
        "1_1",
        episode,
        episode_plan,
        _bible(),
        _assets(),
    )
    production.visual_groups = build_visual_groups(production, series_assets=_assets())

    report = evaluate_production_preflight(
        episode_plan,
        production,
        native_dialogue=True,
    )

    assert report["passed"] is True
    assert report["vlm_questions"]
    assert all(
        question["source"].startswith("shot_contract")
        for question in report["vlm_questions"]
    )

    _, broken_plan = _v5_plan(mismatch=True)
    broken = compile_production_plan(
        "1_1",
        episode,
        broken_plan,
        _bible(),
        _assets(),
    )
    broken.visual_groups = build_visual_groups(broken, series_assets=_assets())
    broken_report = evaluate_production_preflight(
        broken_plan,
        broken,
        native_dialogue=True,
    )
    mismatch = next(
        issue
        for issue in broken_report["issues"]
        if issue["code"] == "handoff_state_mismatch"
    )
    assert mismatch["dimensions"] == ["knowledge"]


def test_preflight_duration_is_planned_and_detects_unit_drift() -> None:
    episode, episode_plan = _v5_plan()
    production = compile_production_plan(
        "1_1",
        episode,
        episode_plan,
        _bible(),
        _assets(),
    )
    production.visual_groups = build_visual_groups(production, series_assets=_assets())
    production.units[0].planned_seconds = 9.0

    report = evaluate_production_preflight(episode_plan, production)

    assert "unit_planned_duration_inconsistent" in {
        issue["code"] for issue in report["issues"]
    }


def test_runtime_stops_at_preflight_before_entering_any_media_stage(
    tmp_path: Path,
) -> None:
    episode, episode_plan = _v5_plan(mismatch=True)
    class Media:
        pass

    settings = Settings(final_audio_policy=NATIVE_DIALOGUE_POLICY)
    runtime = EpisodeProductionRuntime(
        settings,
        Media(),
        Renderer(settings),
        None,
        None,
    )  # type: ignore[arg-type]
    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()

    with pytest.raises(RuntimeError, match="PRE gate failed"):
        runtime.run(
            novel_dir=tmp_path,
            episode_dir=episode_dir,
            episode=episode,
            episode_plan=episode_plan,
            bible=_bible(),
            series_assets=_assets(),
            final_video=episode_dir / "final.mp4",
            cover=episode_dir / "cover.jpeg",
            ending=episode_dir / "ending.jpeg",
            video_id="1_1",
            episode_count=1,
        )

    assert (episode_dir / "production_preflight_report.json").is_file()
    assert not (episode_dir / "tts_asr_report.json").exists()


def test_abridged_is_ordered_whole_clause_deletion_and_counts_as_source_anchored() -> None:
    quote = "甲说：“你先听我说，这扇门不能开，否则大家都会死。”"
    kept = ["你先听", "我说，", "否则大家都会死。"]
    assert abridged_clause_subsequence(kept, quote) is True
    assert abridged_clause_subsequence(["你听我说，", "否则大家都会死。"], quote) is False

    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=quote,
        text_count=len(quote),
        source_start=0,
        source_end=len(quote),
    )
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event="甲阻止开门",
        chapter_start_state="甲开始说话",
        chapter_end_state="甲说完警告",
        episode_state_change="警告说完",
        strongest_hook_candidate="门不能开",
        hook_source_quote=quote,
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description="甲阻止开门",
                source_quote=quote,
                importance="critical",
                narrative_role="resolution",
                characters=["甲"],
            )
        ],
    )
    turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text=text,
            speaking=True,
            source_quote=quote,
            derivation=TurnDerivation.ABRIDGED,
        )
        for text in kept
    ]
    plan = EpisodePlan(
        video_title="警告",
        hook="门不能开",
        summary="甲警告",
        shots=[
            Shot(
                index=1,
                narration=kept[0],
                subtitle=kept[0],
                visual_prompt="甲挡在门前",
                motion_prompt="甲抬手",
                characters=["甲"],
                source_quote=quote,
                event_ids=["event_001"],
                turns=turns,
            )
        ],
        adaptation_ledger=[
            AdaptationLedgerItem(
                event_id="event_001",
                disposition="preserved",
                shot_indexes=[1],
                rationale="删去中间完整子句",
            )
        ],
    )

    report = evaluate_script_quality(plan, diagnosis, episode)

    assert "abridged_turn_not_clause_subsequence" not in {
        issue.code for issue in report.issues
    }
    assert report.abridged_turn_count == 3
    assert report.derived_turn_count == 0
    assert report.source_anchored_turn_count == 3
    assert report.source_anchored_char_ratio == 1.0

    broken = plan.model_copy(deep=True)
    broken.shots[0].turns[0].text = "你听"
    invalid = evaluate_script_quality(broken, diagnosis, episode)
    assert "abridged_turn_not_clause_subsequence" in {
        issue.code for issue in invalid.issues
    }


def test_externalized_ledger_requires_an_enumerated_visible_device() -> None:
    source = "甲站在门前，抬手挡住木门。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event=source,
        chapter_start_state="甲站在门前",
        chapter_end_state="木门被挡住",
        episode_state_change="门被挡住",
        strongest_hook_candidate=source,
        hook_source_quote=source,
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description=source,
                source_quote=source,
                importance="critical",
                narrative_role="resolution",
                characters=["甲"],
            )
        ],
    )
    turn = ScriptTurn(
        role="甲",
        speaker_name="甲",
        text="门，我来挡。",
        speaking=True,
        source_quote=source,
        derivation=TurnDerivation.DERIVED,
        device=TurnDevice.SPATIAL,
        serves=["event_001"],
    )
    plan = EpisodePlan(
        video_title="挡门",
        hook="谁来挡门",
        summary=source,
        shots=[
            Shot(
                index=1,
                narration=turn.text,
                subtitle=turn.text,
                visual_prompt=source,
                motion_prompt="甲抬手挡门",
                characters=["甲"],
                source_quote=source,
                event_ids=["event_001"],
                turns=[turn],
            )
        ],
        adaptation_ledger=[
            AdaptationLedgerItem(
                event_id="event_001",
                disposition="externalized",
                shot_indexes=[1],
                rationale="用空间动作外化叙述",
            )
        ],
    )

    valid = evaluate_script_quality(plan, diagnosis, episode)
    assert "externalized_event_carrier_missing" not in {
        issue.code for issue in valid.issues
    }
    assert valid.externalization_device_coverage == 1.0

    broken = plan.model_copy(deep=True)
    broken.shots[0].turns[0].serves = []
    invalid = evaluate_script_quality(broken, diagnosis, episode)
    assert "externalized_event_carrier_missing" in {
        issue.code for issue in invalid.issues
    }
