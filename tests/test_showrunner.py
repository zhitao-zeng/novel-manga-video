from novel_manga.creative_direction import SHORT_DRAMA_PROFILE, apply_creative_direction
from novel_manga.models import (
    ChapterDiagnosis,
    ChapterEvent,
    Episode,
    EpisodeDramaturgy,
    EpisodePlan,
    ScriptTurn,
    Shot,
    StoryBible,
)
from novel_manga.planner import OpenAICompatiblePlanner
from novel_manga.script_planning import evaluate_script_quality


def _episode_and_diagnosis() -> tuple[Episode, ChapterDiagnosis]:
    rows = [
        "萧炎走上测试广场。",
        "石碑显出斗之力三段。",
        "人群的嘲笑声突然响起。",
        "萧炎抬眼看向发光的戒指。",
    ]
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text="\n".join(rows),
        text_count=sum(map(len, rows)),
        source_start=0,
        source_end=sum(map(len, rows)),
    )
    events = [
        ChapterEvent(
            event_id=f"event_{index:03d}",
            order=index,
            description=row,
            source_quote=row,
            importance="critical",
            narrative_role=("setup", "turning_point", "climax", "resolution")[index - 1],
            characters=["萧炎"],
            causes=[f"event_{index - 1:03d}"] if index > 1 else [],
            state_change="萧炎从隐忍转为警觉" if index == 4 else "",
        )
        for index, row in enumerate(rows, 1)
    ]
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event=rows[1],
        chapter_start_state=rows[0],
        chapter_end_state=rows[-1],
        episode_state_change="萧炎从隐忍转为警觉",
        strongest_hook_candidate=rows[1],
        hook_source_quote=rows[1],
        ending_type="secret",
        events=events,
    )
    return episode, diagnosis


def _directed_plan() -> tuple[Episode, ChapterDiagnosis, EpisodePlan]:
    episode, diagnosis = _episode_and_diagnosis()
    order = [2, 1, 3, 4]
    shots = [
        Shot(
            index=index,
            narration=diagnosis.events[event_index - 1].description,
            subtitle=diagnosis.events[event_index - 1].description,
            visual_prompt=diagnosis.events[event_index - 1].description,
            motion_prompt="人物只在结果或声音触发时作出反应",
            characters=["萧炎"],
            location="测试广场",
            source_quote=diagnosis.events[event_index - 1].source_quote,
            event_ids=[f"event_{event_index:03d}"],
            turns=[
                ScriptTurn(
                    text=diagnosis.events[event_index - 1].description,
                    source_quote=diagnosis.events[event_index - 1].source_quote,
                )
            ],
        )
        for index, event_index in enumerate(order, 1)
    ]
    plan = EpisodePlan(
        video_title="三段之后",
        hook="戒指为什么发光？",
        summary="测试",
        shots=shots,
        adaptation_ledger=[
            {
                "event_id": event.event_id,
                "disposition": "preserved",
                "shot_indexes": [
                    shot.index for shot in shots if event.event_id in shot.event_ids
                ],
                "rationale": "当前章关键因果",
            }
            for event in diagnosis.events
        ],
        creative_profile=SHORT_DRAMA_PROFILE,
        dramaturgy=EpisodeDramaturgy(
            genre_engine="status-power-mystery",
            dramatic_question="萧炎为什么跌到三段？",
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
    bible = StoryBible(
        novel_title="斗破测试",
        genre="东方玄幻",
        visual_style="国漫",
        palette="冷青暖金",
        characters=[
            {
                "name": "萧炎",
                "appearance": "黑发少年",
                "wardrobe": "深色练功服",
            }
        ],
        locations=["测试广场"],
        style_fingerprint="showrunner-test",
    )
    return episode, diagnosis, apply_creative_direction(
        plan, diagnosis, bible, profile=SHORT_DRAMA_PROFILE
    )


def test_showrunner_fallback_connects_retention_intent_state_and_audio() -> None:
    episode, diagnosis, plan = _directed_plan()
    assert plan.showrunner_plan is not None
    assert plan.showrunner_plan.planning_mode == "inferred_fallback"
    assert {beat.function for beat in plan.showrunner_plan.retention.beats} >= {
        "hook",
        "question",
        "payoff",
        "cliffhanger",
    }
    assert plan.showrunner_plan.character_state_deltas[0].character_name == "萧炎"
    assert all(shot.shot_intent.retention_beat_id for shot in plan.shots)
    assert all(shot.audio_plan.audio_beats for shot in plan.shots)

    report = evaluate_script_quality(plan, diagnosis, episode)
    assert report.retention_beat_coverage == 1.0
    assert report.information_fact_grounding == 1.0
    assert report.character_delta_grounding == 1.0
    assert report.shot_intent_coverage == 1.0
    assert report.audio_beat_coverage == 1.0
    assert report.expected_shots_from_retention == (
        len(plan.showrunner_plan.retention.beats) * 4
    )
    assert "density_below_retention_projection" in {
        issue.code for issue in report.issues
    }


def test_showrunner_normalizes_reveal_function_to_payoff() -> None:
    episode, diagnosis, plan = _directed_plan()
    assert plan.showrunner_plan is not None
    raw = plan.showrunner_plan.model_dump(mode="json")
    raw["retention"]["beats"][2]["function"] = "reveal"
    raw["information_states"][0]["source_event_ids"] = [
        "event_001",
        "event_003",
    ]
    diagnosis.events[2].importance = "supporting"
    for beat in raw["retention"]["beats"]:
        beat["shot_indexes"] = []
    raw["retention"]["beats"][-1]["event_ids"].append("event_002")
    bible = StoryBible(
        novel_title="斗破测试",
        genre="东方玄幻",
        visual_style="国漫",
        palette="冷青暖金",
        characters=[
            {
                "name": "萧炎",
                "appearance": "黑发少年",
                "wardrobe": "深色练功服",
            }
        ],
        locations=["测试广场"],
        style_fingerprint="showrunner-test",
    )
    planner = object.__new__(OpenAICompatiblePlanner)

    showrunner = planner._validate_showrunner_data(
        raw,
        episode,
        diagnosis,
        bible,
        {"event_001"},
    )

    assert showrunner.retention.beats[2].function == "payoff"
    assert showrunner.information_states[0].source_event_ids == ["event_001"]


def test_quality_gate_rejects_middle_attention_vacuum_and_ungrounded_fact() -> None:
    episode, diagnosis, plan = _directed_plan()
    assert plan.showrunner_plan is not None
    broken = plan.model_copy(deep=True)
    assert broken.showrunner_plan is not None
    beats = broken.showrunner_plan.retention.beats
    starts = [0.0, 0.04, 0.08, 0.12, 0.8]
    broken.showrunner_plan.retention.beats = [
        beat.model_copy(
            update={
                "target_start_ratio": starts[index],
                "target_end_ratio": max(starts[index], beat.target_end_ratio),
            }
        )
        for index, beat in enumerate(beats)
    ]
    broken.showrunner_plan.information_states[0] = (
        broken.showrunner_plan.information_states[0].model_copy(
            update={"source_quote": "当前章里不存在的秘密"}
        )
    )

    report = evaluate_script_quality(broken, diagnosis, episode)
    codes = {issue.code for issue in report.issues}
    assert "attention_gap_too_large" in codes
    assert "information_fact_not_grounded" in codes
