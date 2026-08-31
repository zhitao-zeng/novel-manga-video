import json
import time

import pytest
from pydantic import ValidationError

from novel_manga.config import Settings
from novel_manga.models import (
    BeatScriptShot,
    ChapterDiagnosis,
    ChapterEvent,
    Character,
    Episode,
    EpisodePlan,
    RetentionBeat,
    RetentionBeatDirection,
    RetentionBeatScript,
    RetentionPlan,
    ScriptTurn,
    ScriptQualityReport,
    ShowrunnerPlan,
    Shot,
    ShotIntent,
    StoryBible,
)
from novel_manga.planner import (
    EpisodePlanningFailed,
    OpenAICompatiblePlanner,
    _compile_retention_beat_episode,
    _downgrade_unremovable_review_deletion_claims,
    _prune_redundant_derived_shots,
    _validate_retention_beat_direction,
    _validate_retention_beat_script,
)


SOURCE = "甲说：“一。”甲又说：“二。”甲最后说：“三。”"


def _retention_beat(index: int) -> RetentionBeat:
    functions = ("hook", "question", "pressure", "cliffhanger")
    return RetentionBeat(
        beat_id=f"beat_{index:03d}",
        function=functions[index - 1],
        target_start_ratio=(index - 1) * 0.25,
        target_end_ratio=index * 0.25,
        audience_question="甲会怎么做？",
        promise=f"推进第{index}次压力",
        emotional_shift="压力升级",
        event_ids=["event_001"],
        source_quote=SOURCE,
    )


def _script(beat: RetentionBeat, *, multi_turn: bool = False) -> RetentionBeatScript:
    turn_rows = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text=text,
            speaking=True,
            source_quote=SOURCE,
        )
        for text in (("一。", "二。", "三。") if multi_turn else ("一。",))
    ]
    shots = [
        BeatScriptShot(
            local_index=1,
            scene_job="施压",
            change="甲作出连续回应",
            blocking="甲依次说出三句",
            characters=["甲"],
            location="门前",
            source_quote=SOURCE,
            event_ids=["event_001"],
            shot_intent=ShotIntent(
                dramatic_function=(
                    "cliffhanger" if beat.function == "cliffhanger" else "pressure"
                ),
                retention_beat_id=beat.beat_id,
            ),
            turns=turn_rows,
        ),
        BeatScriptShot(
            local_index=2,
            scene_job="反应",
            change="门前关系更紧张",
            blocking="甲抬手",
            characters=["甲"],
            location="门前",
            source_quote=SOURCE,
            event_ids=["event_001"],
            shot_intent=ShotIntent(
                dramatic_function="reaction",
                retention_beat_id=beat.beat_id,
            ),
            turns=[turn_rows[0]],
        ),
        BeatScriptShot(
            local_index=3,
            scene_job="落点",
            change="甲停在门前",
            blocking="甲停住",
            characters=["甲"],
            location="门前",
            source_quote=SOURCE,
            event_ids=["event_001"],
            shot_intent=ShotIntent(
                dramatic_function=(
                    "cliffhanger" if beat.function == "cliffhanger" else "advance"
                ),
                retention_beat_id=beat.beat_id,
            ),
            turns=[turn_rows[-1]],
        ),
    ]
    return RetentionBeatScript(
        beat_id=beat.beat_id,
        open_state="甲站在门内",
        close_state="甲停在门前",
        shots=shots,
    )


def _directed_row(source_index: int, start: int, end: int) -> dict:
    return {
        "source_shot_index": source_index,
        "turn_start": start,
        "turn_end": end,
        "shot_scale": "中近景",
        "visual_prompt": "甲站在门前",
        "motion_prompt": "甲抬手并停住",
        "performance_plan": {
            "objective": "让动作改变关系",
            "start_state": "甲手臂垂下",
            "motion_beats": [
                {
                    "phase": "development",
                    "seconds": 1.0,
                    "actor": "甲",
                    "target": "木门",
                    "action_type": "confront",
                    "trigger": "听见门外声音",
                    "action": "甲抬手挡门",
                    "end_state": "甲手臂横在门前",
                }
            ],
            "end_state": "甲手臂横在门前",
        },
        "camera_plan": {
            "mode": "motivated_subtle",
            "motivation": "随挡门动作收紧",
            "action_axis": "门轴同侧",
            "screen_direction": "甲保持画面左侧",
            "start_position": "正面中景",
            "camera_beats": [
                {
                    "phase": "development",
                    "trajectory": "短距离慢推",
                    "framing": "收至胸像",
                    "parallax": "门框快于远墙",
                }
            ],
            "end_position": "正面胸像",
        },
        "visual_strategy": "direct-assets",
        "script_open_state": {
            "knowledge": {"甲": "知道门外有声音"},
            "power": {"甲": "控制门口"},
            "relationship": {"甲-门外者": "对立"},
            "physical": {"甲": "站在门前"},
            "ongoing_action": "none",
        },
        "script_close_state": {
            "knowledge": {"甲": "知道门外有声音"},
            "power": {"甲": "控制门口"},
            "relationship": {"甲-门外者": "对立"},
            "physical": {"甲": "站在门前"},
            "ongoing_action": "none",
        },
        "audio_plan": {"speech_strategy": "native"},
    }


def _direction(beat: RetentionBeat, script: RetentionBeatScript) -> RetentionBeatDirection:
    rows = []
    for shot in script.shots:
        if shot.local_index == 1 and len(shot.turns) == 3:
            rows.extend([_directed_row(1, 1, 1), _directed_row(1, 2, 3)])
        else:
            rows.append(_directed_row(shot.local_index, 1, len(shot.turns)))
    return RetentionBeatDirection(beat_id=beat.beat_id, shots=rows)


def test_direction_can_split_only_at_turn_boundaries_without_rewriting_turns() -> None:
    beats = [_retention_beat(index) for index in range(1, 5)]
    scripts = [
        _script(beat, multi_turn=index == 1)
        for index, beat in enumerate(beats, start=1)
    ]
    directions = [_direction(beat, script) for beat, script in zip(beats, scripts, strict=True)]
    for beat, script, direction in zip(beats, scripts, directions, strict=True):
        _validate_retention_beat_direction(
            direction.model_dump(mode="json"),
            beat=beat,
            script=script,
            native_dialogue=True,
        )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event="甲挡门",
        chapter_start_state="甲站在门内",
        chapter_end_state="甲停在门前",
        episode_state_change="甲决定挡门",
        strongest_hook_candidate="门外有人",
        hook_source_quote=SOURCE,
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description="甲挡门",
                source_quote=SOURCE,
                importance="critical",
                narrative_role="resolution",
                characters=["甲"],
            )
        ],
    )
    showrunner = ShowrunnerPlan(
        retention=RetentionPlan(
            beats=beats,
            ending_open_loop="门外是谁？",
        )
    )

    plan = _compile_retention_beat_episode(
        episode=episode,
        diagnosis=diagnosis,
        showrunner=showrunner,
        scripts=scripts,
        directions=directions,
        creative_profile="short-drama-adaptive-v1",
        native_dialogue=True,
    )

    assert [turn.text for turn in plan.shots[0].turns] == ["一。"]
    assert [turn.text for turn in plan.shots[1].turns] == ["二。", "三。"]
    assert [shot.index for shot in plan.shots] == list(range(1, len(plan.shots) + 1))
    assert plan.showrunner_plan is not None
    assert plan.showrunner_plan.retention.beats[0].shot_indexes == [1, 2, 3, 4]
    assert plan.adaptation_ledger[0].shot_indexes == list(range(1, len(plan.shots) + 1))
    assert plan.dramaturgy is not None
    assert plan.dramaturgy.cold_open == "一。"


def test_direction_rejects_turn_gaps_and_cannot_smuggle_script_fields() -> None:
    beat = _retention_beat(1)
    script = _script(beat, multi_turn=True)
    broken = RetentionBeatDirection(
        beat_id=beat.beat_id,
        shots=[
            _directed_row(1, 1, 1),
            _directed_row(1, 3, 3),
            _directed_row(2, 1, 1),
            _directed_row(3, 1, 1),
        ],
    )

    with pytest.raises(ValueError, match="gap or overlap"):
        _validate_retention_beat_direction(
            broken.model_dump(mode="json"),
            beat=beat,
            script=script,
            native_dialogue=True,
        )

    payload = _directed_row(1, 1, 1)
    payload["turns"] = [{"text": "被导演偷改"}]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RetentionBeatDirection.model_validate(
            {"beat_id": beat.beat_id, "shots": [payload]}
        )


def test_direction_converts_global_ranges_when_only_end_exceeds_local_count() -> None:
    beat = _retention_beat(1)
    script = _script(beat, multi_turn=True)
    script.shots[1].turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text="一。",
            speaking=True,
            source_quote=SOURCE,
        )
        for _ in range(5)
    ]
    raw = _direction(beat, script).model_dump(mode="json")
    source_two = next(
        row for row in raw["shots"] if row["source_shot_index"] == 2
    )
    source_three = next(
        row for row in raw["shots"] if row["source_shot_index"] == 3
    )
    source_two.update({"turn_start": 4, "turn_end": 8})
    source_three.update({"turn_start": 9, "turn_end": 9})

    direction = _validate_retention_beat_direction(
        raw,
        beat=beat,
        script=script,
        native_dialogue=True,
    )

    assert [
        (row.source_shot_index, row.turn_start, row.turn_end)
        for row in direction.shots
        if row.source_shot_index in {2, 3}
    ] == [(2, 1, 5), (3, 1, 1)]


def test_direction_extends_same_speaker_compiler_split_tail() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    script.shots[1].turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text="一。",
            speaking=True,
            source_quote=SOURCE,
        )
        for _ in range(5)
    ]
    raw = _direction(beat, script).model_dump(mode="json")
    source_two = next(
        row for row in raw["shots"] if row["source_shot_index"] == 2
    )
    source_two["turn_end"] = 4

    direction = _validate_retention_beat_direction(
        raw,
        beat=beat,
        script=script,
        native_dialogue=True,
    )

    repaired = next(
        row for row in direction.shots if row.source_shot_index == 2
    )
    assert (repaired.turn_start, repaired.turn_end) == (1, 5)


def test_direction_converts_source_index_placeholders_for_single_speaker() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    script.shots[1].turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text="一。",
            speaking=True,
            source_quote=SOURCE,
        )
        for _ in range(2)
    ]
    script.shots[2].turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text="一。",
            speaking=True,
            source_quote=SOURCE,
        )
        for _ in range(3)
    ]
    raw = _direction(beat, script).model_dump(mode="json")
    for row in raw["shots"]:
        row["turn_start"] = row["source_shot_index"]
        row["turn_end"] = row["source_shot_index"]

    direction = _validate_retention_beat_direction(
        raw,
        beat=beat,
        script=script,
        native_dialogue=True,
    )

    assert [
        (row.source_shot_index, row.turn_start, row.turn_end)
        for row in direction.shots
    ] == [(1, 1, 1), (2, 1, 2), (3, 1, 3)]


def test_direction_keeps_final_duplicate_only_for_silent_action() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    script.shots[2].turns = [
        ScriptTurn(
            role="action",
            speaker_name="",
            text="甲抬手挡门。",
            delivery_mode="silent_action",
            source_quote=SOURCE,
            derivation="derived",
            device="consequence",
            serves=["event_001"],
        )
    ]
    raw = _direction(beat, script).model_dump(mode="json")
    final_row = next(
        row for row in raw["shots"] if row["source_shot_index"] == 3
    )
    raw["shots"].append({**final_row, "shot_scale": "全景"})

    direction = _validate_retention_beat_direction(
        raw,
        beat=beat,
        script=script,
        native_dialogue=True,
    )

    rows = [
        row for row in direction.shots if row.source_shot_index == 3
    ]
    assert len(rows) == 1
    assert rows[0].shot_scale == "全景"


def test_review_deletion_prunes_only_redundant_derived_shot() -> None:
    turn = ScriptTurn(
        role="action",
        speaker_name="",
        text="甲抬手。",
        delivery_mode="silent_action",
        source_quote=SOURCE,
        derivation="derived",
        device="spatial",
        serves=["event_001"],
    )
    shots = [
        Shot(
            index=index,
            narration=turn.text,
            subtitle=turn.text,
            visual_prompt="甲站在门前",
            motion_prompt="甲抬手",
            source_quote=SOURCE,
            event_ids=["event_001"],
            turns=[turn],
        )
        for index in range(1, 4)
    ]
    plan = EpisodePlan(
        video_title="测试",
        hook="门外是谁",
        summary="甲挡门",
        shots=shots,
        adaptation_ledger=[
            {
                "event_id": "event_001",
                "disposition": "preserved",
                "shot_indexes": [1, 2, 3],
                "rationale": "测试",
            }
        ],
    )
    review = ScriptQualityReport(
        passed=False,
        script_char_count=0,
        shot_count=3,
        turn_count=0,
        critical_event_coverage=1,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": "derived_serves_invalid",
                "severity": "blocking",
                "message": "第二镜可删除",
                "shot_indexes": [2],
                "event_ids": ["event_001"],
            }
        ],
    )

    pruned, removed = _prune_redundant_derived_shots(plan, review)

    assert removed == [2]
    assert [shot.index for shot in pruned.shots] == [1, 2]
    assert pruned.adaptation_ledger[0].shot_indexes == [1, 2]

    warning_review = review.model_copy(
        update={
            "passed": True,
            "issues": [
                review.issues[0].model_copy(
                    update={
                        "code": "derived_serves_mismatch",
                        "severity": "warning",
                    }
                )
            ],
        }
    )
    _, warning_removed = _prune_redundant_derived_shots(
        plan,
        warning_review,
    )
    assert warning_removed == [2]

    unique = plan.model_copy(deep=True)
    unique.shots[1].event_ids = ["event_002"]
    normalized = _downgrade_unremovable_review_deletion_claims(
        unique,
        review,
    )
    assert normalized.issues[0].code == "review_deletion_claim_not_supported"
    assert normalized.issues[0].severity == "warning"


def test_direction_translates_injury_detail_across_provider_fields() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    raw = _direction(beat, script).model_dump(mode="json")
    raw["shots"][0]["visual_prompt"] = "掌心被指甲深深刺入"
    raw["shots"][0]["motion_prompt"] = "指甲持续刺入掌心"
    raw["shots"][0]["camera_plan"]["end_position"] = "聚焦指甲"
    raw["shots"][0]["script_close_state"]["physical"] = {
        "甲": "指甲刺入掌心"
    }

    direction = _validate_retention_beat_direction(
        raw,
        beat=beat,
        script=script,
        native_dialogue=True,
    )

    surface = json.dumps(direction.model_dump(mode="json"), ensure_ascii=False)
    assert "指甲" not in surface
    assert "刺入" not in surface


def test_direction_rejects_visual_flashback_that_leaves_current_timeline() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    raw = _direction(beat, script).model_dump(mode="json")
    raw["shots"][0]["visual_prompt"] = "画面进入回忆画面，出现少年版甲"

    with pytest.raises(ValueError, match="leaves the current timeline"):
        _validate_retention_beat_direction(
            raw,
            beat=beat,
            script=script,
            native_dialogue=True,
        )


def test_direction_failure_preserves_the_accepted_beat_script(tmp_path) -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event="甲挡门",
        chapter_start_state="甲站在门内",
        chapter_end_state="甲停在门前",
        episode_state_change="甲决定挡门",
        strongest_hook_candidate="门外有人",
        hook_source_quote=SOURCE,
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description="甲挡门",
                source_quote=SOURCE,
                importance="critical",
                narrative_role="resolution",
                characters=["甲"],
            )
        ],
    )
    showrunner = ShowrunnerPlan(
        retention=RetentionPlan(
            beats=[_retention_beat(index) for index in range(1, 5)],
            ending_open_loop="门外是谁？",
        )
    )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="direction-test",
    )
    planner = OpenAICompatiblePlanner(
        Settings(
            planner_backend="openai-compatible",
            llm_base_url="http://127.0.0.1:1/v1",
            llm_api_key="fixture",
            creative_profile="short-drama-adaptive-v1",
        )
    )
    planner._planning_started = time.monotonic()
    planner._plan_retention_beat_script = lambda **kwargs: script.model_copy(  # type: ignore[method-assign]
        update={"beat_id": kwargs["beat"].beat_id}
    )

    def fail_direction(**kwargs):
        raise ValueError("direction could not fit the accepted turns")

    planner._plan_retention_beat_direction = fail_direction  # type: ignore[method-assign]
    draft_root = tmp_path / "drafts"

    with pytest.raises(EpisodePlanningFailed) as failure:
        planner._plan_episode_by_retention_beats(
            novel=None,  # type: ignore[arg-type]
            episode=episode,
            bible=bible,
            diagnosis=diagnosis,
            showrunner=showrunner,
            previous_state=None,
            draft_root=draft_root,
        )

    assert failure.value.failed_stage == "retention_beat_direction"
    assert failure.value.failed_beat_id == beat.beat_id
    assert (draft_root / "beats/beat_001/script_accepted.json").is_file()
    assert not (draft_root / "beats/beat_001/direction_accepted.json").exists()


def test_derived_carrier_allows_anonymous_offscreen_listener_but_not_new_named_character() -> None:
    beat = _retention_beat(1)
    carrier_source = SOURCE + "\n甲过去曾经很强，如今却站在门前。"
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=carrier_source,
        text_count=len(carrier_source),
        source_start=0,
        source_end=len(carrier_source),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="carrier-test",
    )
    anonymous = _script(beat)
    anonymous.shots[0].turns[0] = ScriptTurn(
        role="姐妹",
        speaker_name="姐妹",
        text="他以前很强？",
        speaking=False,
        delivery_mode="offscreen_dialogue",
        source_quote="甲过去曾经很强，如今却站在门前。",
        derivation="derived",
        device="listener_qa",
        serves=["event_001"],
    )

    accepted = _validate_retention_beat_script(
        anonymous.model_dump(mode="json"),
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )
    assert accepted.shots[0].turns[0].speaker_name == "姐妹"

    missing_crowd = anonymous.model_dump(mode="json")
    missing_crowd["shots"][0]["turns"][0].update(
        {
            "role": "",
            "speaker_name": "",
            "device": "crowd_proxy",
        }
    )
    accepted_crowd = _validate_retention_beat_script(
        missing_crowd,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )
    assert accepted_crowd.shots[0].turns[0].speaker_name == "无名族人"

    sound_payload = anonymous.model_dump(mode="json")
    sound_payload["shots"][0]["blocking"] += "，身后传来人群叫喊声"
    sound_payload["shots"][0]["turns"] = [
        {
            "role": "action",
            "speaker_name": "",
            "text": "（甲站在门前）",
            "speaking": False,
            "delivery_mode": "silent_action",
            "source_quote": "甲过去曾经很强，如今却站在门前。",
            "derivation": "derived",
            "device": "spatial",
            "serves": ["event_001"],
        },
        {
            "speaker_name": "群声",
            "text": "（身后传来人群叫喊声）",
            "speaking": True,
            "delivery_mode": "offscreen_dialogue",
            "source_quote": "甲过去曾经很强，如今却站在门前。",
            "derivation": "derived",
            "serves": ["event_001"],
        },
    ]
    accepted_sound = _validate_retention_beat_script(
        sound_payload,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )
    assert len(accepted_sound.shots[0].turns) == 1

    named = anonymous.model_copy(deep=True)
    named.shots[0].turns[0] = named.shots[0].turns[0].model_copy(
        update={"role": "柳无尘", "speaker_name": "柳无尘"}
    )
    with pytest.raises(ValueError, match="invents a named character"):
        _validate_retention_beat_script(
            named.model_dump(mode="json"),
            beat=beat,
            episode=episode,
            bible=bible,
            native_dialogue=True,
        )


def test_beat_script_drops_trailing_shot_that_belongs_to_next_beat() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    script.shots.append(
        BeatScriptShot(
            local_index=4,
            scene_job="越界反应",
            change="下一段压力提前出现",
            blocking="门外人群提前作出反应",
            characters=["甲"],
            location="门前",
            source_quote=SOURCE,
            event_ids=["event_002"],
            shot_intent=ShotIntent(
                dramatic_function="reaction",
                retention_beat_id=beat.beat_id,
            ),
            turns=[
                ScriptTurn(
                    text="甲抬手。",
                    delivery_mode="silent_action",
                    source_quote=SOURCE,
                )
            ],
        )
    )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="beat-boundary-test",
    )

    accepted = _validate_retention_beat_script(
        script.model_dump(mode="json"),
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )

    assert [shot.local_index for shot in accepted.shots] == [1, 2, 3]
    assert all(shot.event_ids == ["event_001"] for shot in accepted.shots)


def test_native_beat_script_compiles_inner_voice_to_visible_silent_action() -> None:
    beat = _retention_beat(1)
    script = _script(beat)
    script.shots[0].turns = [
        ScriptTurn(
            role="甲",
            speaker_name="甲",
            text="他们为什么逼我？",
            delivery_mode="inner_voice",
            source_quote=SOURCE,
            device="inner_voice",
        )
    ]
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="inner-voice-compile-test",
    )

    accepted = _validate_retention_beat_script(
        script.model_dump(mode="json"),
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )

    turn = accepted.shots[0].turns[0]
    assert turn.delivery_mode == "silent_action"
    assert turn.text == "（甲依次说出三句）"
    assert turn.device == "spatial"
    assert turn.serves == ["event_001"]

    visible_raw = _script(beat).model_dump(mode="json")
    visible_raw["shots"][0]["turns"][0].update(
        {
            "text": "我过去很强。",
            "delivery_mode": "visible_dialogue",
            "derivation": "derived",
            "device": "inner_voice",
            "serves": ["event_001"],
            "source_quote": "甲过去很强。",
        }
    )
    visible_episode = episode.model_copy(
        update={"source_text": SOURCE + "\n甲过去很强。"}
    )
    visible = _validate_retention_beat_script(
        visible_raw,
        beat=beat,
        episode=visible_episode,
        bible=bible,
        native_dialogue=True,
    )
    assert visible.shots[0].turns[0].delivery_mode == "visible_dialogue"
    assert visible.shots[0].turns[0].device == "listener_qa"


def test_concrete_past_facts_require_current_timeline_carrier() -> None:
    source = "甲四岁练气，十岁达到九段。"
    beat = _retention_beat(1).model_copy(update={"source_quote": source})
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=source,
        text_count=len(source),
        source_start=0,
        source_end=len(source),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="玄幻",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="fact-carrier-test",
    )
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event=source,
        chapter_start_state="甲站在门前",
        chapter_end_state="甲的过去被揭示",
        episode_state_change="观众知道甲的过去",
        strongest_hook_candidate=source,
        hook_source_quote=source,
        ending_type="secret",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description=source,
                source_quote=source,
                importance="critical",
                narrative_role="turning_point",
                characters=["甲"],
            )
        ],
    )
    raw = {
        "beat_id": beat.beat_id,
        "open_state": "甲站在门前",
        "close_state": "甲的过去被揭示",
        "shots": [
            {
                "local_index": 1,
                "scene_job": "揭示",
                "change": "甲的过去被揭示",
                "blocking": "甲站在门前低头",
                "characters": ["甲"],
                "location": "门前",
                "source_quote": source,
                "event_ids": ["event_001"],
                "shot_intent": {
                    "dramatic_function": "reveal",
                    "retention_beat_id": beat.beat_id,
                },
                "turns": [
                    {
                        "text": "（甲低头）",
                        "delivery_mode": "silent_action",
                        "source_quote": source,
                        "derivation": "derived",
                        "device": "spatial",
                        "serves": ["event_001"],
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="concrete facts lack"):
        _validate_retention_beat_script(
            raw,
            beat=beat,
            episode=episode,
            bible=bible,
            native_dialogue=True,
            diagnosis=diagnosis,
        )

    narration_raw = json.loads(json.dumps(raw, ensure_ascii=False))
    narration_raw["shots"][0]["blocking"] = "人群中有族人低声议论甲"
    narration_raw["shots"][0]["turns"] = [
        {
            "text": "四岁练气，十岁九段。",
            "delivery_mode": "narration",
            "source_quote": source,
            "derivation": "abridged",
            "device": "narration",
        }
    ]
    carrier = _validate_retention_beat_script(
        narration_raw,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
        diagnosis=diagnosis,
    )
    assert carrier.shots[0].turns[0].speaker_name == "无名族人"
    assert carrier.shots[0].turns[0].delivery_mode == "offscreen_dialogue"
    assert carrier.shots[0].turns[0].device == "crowd_proxy"

    raw["shots"][0]["turns"] = [
        {
            "role": "甲",
            "speaker_name": "甲",
            "text": "四岁练气，十岁九段。",
            "delivery_mode": "visible_dialogue",
            "source_quote": source,
            "derivation": "abridged",
            "device": None,
            "serves": [],
        }
    ]
    accepted = _validate_retention_beat_script(
        raw,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
        diagnosis=diagnosis,
    )
    assert accepted.shots[0].turns[0].device == "listener_qa"
    assert accepted.shots[0].turns[0].derivation == "derived"
    assert accepted.shots[0].turns[0].serves == ["event_001"]


def test_beat_script_repairs_empty_action_and_misanchored_exact_line() -> None:
    beat = _retention_beat(1)
    beat.new_information_fact_ids = ["fact_001"]
    raw = _script(beat).model_dump(mode="json")
    raw["released_fact_ids"] = []
    raw["shots"][0]["characters"] = []
    raw["shots"][0]["source_quote"] = "模型概括的引用"
    raw["shots"][0]["shot_intent"]["dramatic_function"] = "turning_point"
    raw["shots"][0]["turns"][0].update(
        {
            "text": "",
            "role": "甲",
            "speaker_name": "甲",
            "speaking": False,
            "delivery_mode": "silent_action",
            "derivation": "derived",
            "device": None,
            "serves": ["event_001"],
        }
    )
    raw["shots"][1]["turns"][0].update(
        {
            "text": "甲又说：“二。”",
            "source_quote": "一。",
            "derivation": "abridged",
        }
    )
    raw["shots"][2]["turns"][0].update(
        {"role": "", "speaker_name": ""}
    )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="script-normalization-test",
    )
    diagnosis = ChapterDiagnosis(
        source_chapter="第一章",
        density="sparse",
        core_event="甲说话",
        chapter_start_state="甲开口前",
        chapter_end_state="甲说完话",
        episode_state_change="甲完成表达",
        strongest_hook_candidate="甲会说什么",
        hook_source_quote=SOURCE,
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description="甲连续说出三句话",
                source_quote=SOURCE,
                importance="critical",
                narrative_role="resolution",
                characters=["甲"],
            )
        ],
    )

    accepted = _validate_retention_beat_script(
        raw,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
        diagnosis=diagnosis,
    )

    empty_action = accepted.shots[0].turns[0]
    reanchored = accepted.shots[1].turns[0]
    assert empty_action.text == "（甲依次说出三句）"
    assert empty_action.device == "spatial"
    assert accepted.shots[0].characters == ["甲"]
    assert accepted.shots[0].source_quote == SOURCE
    assert accepted.shots[0].shot_intent.dramatic_function == "reveal"
    assert reanchored.derivation == "verbatim"
    assert reanchored.text in reanchored.source_quote
    assert accepted.shots[2].turns[0].speaker_name == "甲"
    assert accepted.released_fact_ids == ["fact_001"]


def test_beat_script_infers_missing_speaker_from_speech_action() -> None:
    beat = _retention_beat(1)
    raw = _script(beat).model_dump(mode="json")
    raw["shots"][0]["blocking"] = "甲开口宣布结果，乙站在旁边听。"
    raw["shots"][0]["change"] = "甲宣布结果，乙作出反应。"
    raw["shots"][0]["turns"][0].update(
        {"role": "", "speaker_name": ""}
    )
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text=SOURCE,
        text_count=len(SOURCE),
        source_start=0,
        source_end=len(SOURCE),
    )
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[
            Character(name="甲", appearance="黑发青年", wardrobe="蓝袍"),
            Character(name="乙", appearance="白发青年", wardrobe="灰袍"),
        ],
        locations=["门前"],
        style_fingerprint="speaker-action-test",
    )

    accepted = _validate_retention_beat_script(
        raw,
        beat=beat,
        episode=episode,
        bible=bible,
        native_dialogue=True,
    )

    assert accepted.shots[0].turns[0].speaker_name == "甲"
    assert accepted.shots[0].turns[0].role == "甲"
