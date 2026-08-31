from __future__ import annotations

import json
from pathlib import Path

from novel_manga.ingest import read_novel
from novel_manga.models import (
    ChapterDiagnosis,
    ChapterEvent,
    Episode,
    EpisodePlan,
    ScriptTurn,
    SeriesState,
    Shot,
    StoryBible,
)
from novel_manga.script_planning import (
    deterministic_chapter_diagnosis,
    effective_script_policy,
    evaluate_script_quality,
    normalize_chronological_plan,
    validate_chapter_diagnosis,
    validate_series_state,
)


def test_density_reference_is_stable_and_never_relaxed_into_a_shipping_gate(
    monkeypatch,
) -> None:
    strict = effective_script_policy(2705, "normal", "short-drama-adaptive-v1")
    monkeypatch.setenv("NOVEL_SCRIPT_STRICTNESS", "relaxed")
    legacy_env_ignored = effective_script_policy(
        2705, "normal", "short-drama-adaptive-v1"
    )

    assert strict.min_script_chars == 595
    assert legacy_env_ignored == strict


def _synthetic_long_episode() -> Episode:
    first = "方才明明正在逛博物馆，怎么一眨眼的功夫就到这个地方来了？"
    rows = [first]
    for index in range(1, 24):
        rows.append(
            f"事件{index}发生后，众人根据眼前的环境、彼此的反应和手中的物件继续确认处境，"
            "每个人的立场都发生了可以追溯的变化，但没有人能够立刻解释异常的真正原因；"
            "他们只能先记住当前线索，再决定下一步行动。"
        )
    source = "第一章 魂瓶\n" + "\n".join(rows)
    return Episode(
        index=1,
        source_title="第一章 魂瓶",
        source_text=source,
        text_count=len("".join(source.split())),
        source_start=0,
        source_end=len(source),
    )


def test_script_quality_rejects_long_spoken_turn_without_requiring_more_shots() -> None:
    episode = _synthetic_long_episode()
    diagnosis = deterministic_chapter_diagnosis(episode)
    quote = diagnosis.events[0].source_quote
    shots = []
    for index, event in enumerate(diagnosis.events, 1):
        text = (
            "这一整段口播把动作原因后果和人物反应全部塞在一起所以听起来完全不像短剧节奏"
            "而且还在自然停顿之后继续解释已经说过的信息导致一口气根本无法清楚说完"
        )
        shots.append(
            Shot(
                index=index,
                narration=text[:80],
                subtitle=text[:80],
                visual_prompt="测试",
                motion_prompt="测试",
                source_quote=event.source_quote[:120],
                event_ids=[event.event_id],
                turns=[ScriptTurn(text=text, source_quote=event.source_quote)],
            )
        )
    plan = EpisodePlan(
        video_title=episode.source_title,
        hook=diagnosis.events[0].description,
        summary="节奏门禁测试",
        shots=shots,
        adaptation_ledger=[
            {
                "event_id": event.event_id,
                "disposition": "preserved",
                "shot_indexes": [index],
                "rationale": "测试",
            }
            for index, event in enumerate(diagnosis.events, 1)
        ],
    )

    report = evaluate_script_quality(plan, diagnosis, episode)

    assert report.passed is False
    assert report.max_turn_char_count > 60
    assert report.hard_overflow_turn_count == len(diagnosis.events)
    issue = next(item for item in report.issues if item.code == "spoken_turn_too_long")
    assert issue.shot_indexes == list(range(1, len(diagnosis.events) + 1))

    complete_utterance = "他听见结果后先攥紧手指，随后抬眼看向那些曾经讨好过他的族人，最后才把那口气咽下去。"
    semantic_plan = plan.model_copy(deep=True)
    for shot in semantic_plan.shots:
        shot.turns = [ScriptTurn(text=complete_utterance, source_quote=shot.source_quote)]
    semantic_report = evaluate_script_quality(semantic_plan, diagnosis, episode)
    assert semantic_report.max_turn_char_count > 20
    assert semantic_report.hard_overflow_turn_count == len(diagnosis.events)

    short_plan = plan.model_copy(deep=True)
    for shot in short_plan.shots:
        shot.turns = [ScriptTurn(text="结果出来了，先别笑。", source_quote=shot.source_quote)]
    short_report = evaluate_script_quality(short_plan, diagnosis, episode)
    assert short_report.max_turn_char_count <= 14
    assert short_report.hard_overflow_turn_count == 0
    assert not any(issue.code == "spoken_turn_too_long" for issue in short_report.issues)


def test_silent_action_is_not_checked_as_spoken_stage_direction_or_paraphrase() -> None:
    source = '第一章\n"他们为何如此势利？"少年抬头看向人群。'
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
        core_event="少年抬头",
        chapter_start_state="少年低头",
        chapter_end_state="少年抬头看人群",
        episode_state_change="少年开始正视人群",
        strongest_hook_candidate="少年为何被排斥",
        hook_source_quote='"他们为何如此势利？"',
        ending_type="decision",
        events=[
            ChapterEvent(
                event_id="event_001",
                order=1,
                description="少年抬头看人群",
                source_quote='"他们为何如此势利？"少年抬头看向人群。',
                importance="critical",
                narrative_role="resolution",
            )
        ],
    )
    plan = EpisodePlan(
        video_title="第一章",
        hook="少年抬头",
        summary="无声反应",
        shots=[
            Shot(
                index=1,
                narration="（无声）",
                subtitle="（无声）",
                visual_prompt="少年抬头看向人群",
                motion_prompt="少年抬头",
                source_quote='"他们为何如此势利？"少年抬头看向人群。',
                event_ids=["event_001"],
                turns=[
                    ScriptTurn(
                        role="action",
                        speaker_name="",
                        text="（无声）",
                        delivery_mode="silent_action",
                        source_quote='"他们为何如此势利？"',
                        derivation="derived",
                        device="spatial",
                    )
                ],
            )
        ],
    )

    report = evaluate_script_quality(plan, diagnosis, episode)
    codes = {issue.code for issue in report.issues}

    assert "turn_text_contains_stage_direction" not in codes
    assert "derived_turn_paraphrases_dialogue" not in codes


def test_short_drama_quality_blocks_slideshow_objective() -> None:
    source_rows = (
        "楚焱站在碑前。",
        "楚媚笑着说你输了。",
        "楚焱低头离开。",
    )
    source = "".join(source_rows)
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
        density="balanced",
        core_event="楚焱受辱后离开",
        chapter_start_state="楚焱等待测验",
        chapter_end_state="楚焱离开",
        episode_state_change="楚焱受到公开羞辱",
        strongest_hook_candidate=source_rows[1],
        hook_source_quote=source_rows[1],
        ending_type="emotion",
        events=[
            ChapterEvent(
                event_id=f"event_{index:03d}",
                order=index,
                description=row,
                source_quote=row,
                importance="critical",
                narrative_role=("setup", "turning_point", "resolution")[index - 1],
                characters=["楚焱"] if index != 2 else ["楚焱", "楚媚"],
                causes=[] if index == 1 else [f"event_{index - 1:03d}"],
                state_change=row,
            )
            for index, row in enumerate(source_rows, 1)
        ],
    )
    plan = EpisodePlan(
        video_title="陨落",
        hook="楚焱受辱",
        summary="楚焱被动受辱后离开",
        creative_profile="short-drama-adaptive-v1",
        dramaturgy={
            "genre_engine": "公开羞辱",
            "dramatic_question": "楚焱会反击吗？",
            "cold_open": source_rows[1],
            "cold_open_source_quote": source_rows[1],
            "status_before": "等待测验",
            "status_after": "受辱离开",
            "conflict_beats": ["测验", "羞辱", "离开"],
            "cliffhanger": "没有可见钩子",
            "narration_budget_ratio": 0.5,
        },
        shots=[
            Shot(
                index=index,
                narration=row,
                subtitle=row,
                visual_prompt=(
                    "楚焱和楚媚站着不动"
                    if index == 2
                    else "楚焱站着不动"
                ),
                motion_prompt="站着不动",
                characters=["楚焱"] if index != 2 else ["楚焱", "楚媚"],
                source_quote=row,
                event_ids=[f"event_{index:03d}"],
                turns=[
                    ScriptTurn(
                        text=row,
                        source_quote=row,
                        derivation="verbatim",
                    )
                ],
            )
            for index, row in enumerate(source_rows, 1)
        ],
        adaptation_ledger=[
            {
                "event_id": f"event_{index:03d}",
                "disposition": "preserved",
                "shot_indexes": [index],
                "rationale": "测试",
            }
            for index in range(1, 4)
        ],
    )

    report = evaluate_script_quality(plan, diagnosis, episode)
    codes = {issue.code for issue in report.issues}

    assert "shot_change_missing" in codes
    assert "protagonist_has_no_active_action" in codes
    assert "named_conflict_ratio_too_low" in codes
    assert "verbatim_turn_ratio_too_high" in codes
    assert "visible_cliffhanger_missing" in codes


def test_chronological_normalizer_keeps_raw_draft_separate() -> None:
    episode = _synthetic_long_episode()
    diagnosis = deterministic_chapter_diagnosis(episode)
    first = diagnosis.events[0]
    last = diagnosis.events[-1]
    early = Shot(
        index=2,
        narration="开篇",
        subtitle="开篇",
        visual_prompt="开篇",
        motion_prompt="开篇",
        source_quote=first.source_quote[:120],
        event_ids=[first.event_id],
        turns=[ScriptTurn(text="开篇", source_quote=first.source_quote)],
    )
    duplicate = early.model_copy(update={"index": 3})
    ending = early.model_copy(
        update={
            "index": 1,
            "narration": "结尾",
            "subtitle": "结尾",
            "source_quote": last.source_quote[:120],
            "event_ids": [last.event_id],
            "turns": [ScriptTurn(text="结尾", source_quote=last.source_quote)],
        }
    )
    plan = EpisodePlan(
        video_title="提前剧透",
        hook="提前揭晓答案",
        summary="测试",
        shots=[ending, early, duplicate],
        adaptation_ledger=[
            {
                "event_id": first.event_id,
                "disposition": "preserved",
                "shot_indexes": [2, 3],
                "rationale": "测试",
            },
            {
                "event_id": last.event_id,
                "disposition": "preserved",
                "shot_indexes": [1],
                "rationale": "测试",
            },
        ],
    )

    normalized = normalize_chronological_plan(plan, diagnosis, episode)

    assert [shot.narration for shot in plan.shots] == ["结尾", "开篇", "开篇"]
    assert [shot.narration for shot in normalized.shots] == ["开篇", "结尾"]
    assert normalized.video_title == episode.source_title
    assert normalized.hook == first.description
    assert normalized.adaptation_ledger[0].shot_indexes == [1]
    assert normalized.adaptation_ledger[1].shot_indexes == [2]


def test_diagnosis_quote_grounding_accepts_only_formatting_differences(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    source.write_text("第一章 门外\n王扬一愣：\u201c许编？\u201d", encoding="utf-8")
    novel = read_novel(source, novel_id="quote", title="测试")
    episode = novel.episodes[0]
    diagnosis = deterministic_chapter_diagnosis(episode)
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="国漫",
        palette="冷色",
        characters=[],
        locations=["门外"],
        style_fingerprint="test",
    )
    diagnosis = diagnosis.model_copy(
        update={
            "hook_source_quote": "王扬一愣:许编?",
            "events": [
                diagnosis.events[-1].model_copy(
                    update={"event_id": "event_001", "order": 1, "source_quote": "王扬一愣:许编?"}
                )
            ],
        }
    )

    grounded = validate_chapter_diagnosis(diagnosis, episode, bible)

    assert grounded.hook_source_quote == "王扬一愣：\u201c许编？\u201d"
    assert grounded.events[0].source_quote == "王扬一愣：\u201c许编？\u201d"

    paraphrased = grounded.model_copy(
        update={"hook_source_quote": "王扬认出了出版社编辑"}
    )
    try:
        validate_chapter_diagnosis(paraphrased, episode, bible)
    except ValueError as error:
        assert "SOURCE_EVIDENCE" in str(error)
    else:
        raise AssertionError("semantic paraphrases must not be accepted as source evidence")


def test_old_maoxing_summary_script_is_rejected_before_media() -> None:
    episode = _synthetic_long_episode()
    diagnosis = deterministic_chapter_diagnosis(episode)
    texts = [
        "五个现代人围观博物馆魂瓶后，竟在古代江边同时醒来；其中一人仍昏迷。",
        "许编认出王扬；王扬冲到水边，倒影却是一张十七八岁的苍白少年脸。",
        "穿越了！我们穿越了！看了不知道多少本穿越小说，现在终于穿越了啊哈哈哈！这博物馆没白逛！哈哈哈哈哈！我还穿得这么帅！！！",
        "王扬发现众人说的是中古音，由此判断他们穿越到了汉唐之间，而非幻想世界。",
        "王博士，怎么办？我家里还有老婆孩子，我女儿才五岁，不回去不行啊！",
        "他们想起博物馆里正是五个人围着展台；草篓中，竟藏着那只青色魂瓶。",
        "魂瓶是古人的随葬明器，据说有收魂、安魂的作用。",
        "壮汉怕被强行带回现代，突然抱着魂瓶冲向河边，将它砸碎，又把碎片踢入急流。",
        "你最喜欢哪部电影？",
        "鬼啊！鬼啊！恶鬼上身啦！",
        "快追！他不是现代人！",
    ]
    quote = "方才明明正在逛博物馆，怎么一眨眼的功夫就到这个地方来了？"
    plan = EpisodePlan(
        video_title="第一章 魂瓶：五人穿越，谁在撒谎",
        hook="五个现代人穿越，其中一人不是穿越者。",
        summary="摘要式剧本",
        shots=[
            Shot(
                index=index,
                narration=text,
                subtitle=text,
                visual_prompt=text,
                motion_prompt="轻微推镜",
                source_quote=quote,
                turns=[ScriptTurn(text=text, source_quote=quote)],
            )
            for index, text in enumerate(texts, 1)
        ],
    )

    report = evaluate_script_quality(plan, diagnosis, episode)

    assert report.passed is False
    assert report.script_char_count == 312
    codes = {issue.code for issue in report.issues}
    assert "density_script_below_reference" in codes
    density_issue = next(
        issue
        for issue in report.issues
        if issue.code == "density_script_below_reference"
    )
    assert density_issue.severity == "warning"
    assert density_issue.gate_level == "craft"
    assert report.craft_warning_count >= 1
    assert report.structural_blocker_count >= 1
    assert report.density_reference_min_shots == 22
    assert report.density_reference_max_shots == 36
    assert "density_turns_below_reference" in codes
    assert "critical_events_missing" in codes


def test_series_state_rejects_new_fact_without_current_chapter_evidence(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    source.write_text("第一章 门外\n林晚关上了门。", encoding="utf-8")
    episode = read_novel(source, novel_id="state", title="测试").episodes[0]
    state = SeriesState.model_validate(
        {
            "current_episode": 1,
            "timeline": [
                {
                    "statement": "林晚知道母亲仍然活着",
                    "source_episode": 1,
                    "source_quote": "母亲仍然活着",
                }
            ],
        }
    )

    try:
        validate_series_state(state, episode, None)
    except ValueError as error:
        assert "not grounded" in str(error)
    else:
        raise AssertionError("ungrounded future fact must be rejected")


def test_series_state_carries_cross_episode_information_with_evidence(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    source.write_text("第一章 门外\n林晚关上了门。", encoding="utf-8")
    episode = read_novel(source, novel_id="information", title="测试").episodes[0]
    state = SeriesState.model_validate(
        {
            "current_episode": 1,
            "information_states": [
                {
                    "fact_key": "door_closed",
                    "statement": "林晚已经关门",
                    "viewer_awareness": "knows",
                    "character_awareness": {"林晚": "knows", "门外人": "unaware"},
                    "dramatic_use": "viewer_leads",
                    "evidence": {
                        "statement": "林晚已经关门",
                        "source_episode": 1,
                        "source_quote": "林晚关上了门。",
                    },
                }
            ],
        }
    )

    validated = validate_series_state(state, episode, None)

    assert validated.information_states[0].character_awareness["门外人"] == "unaware"
