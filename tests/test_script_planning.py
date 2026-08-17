from __future__ import annotations

import json
from pathlib import Path

from novel_manga.ingest import read_novel
from novel_manga.models import Episode, EpisodePlan, ScriptTurn, SeriesState, Shot, StoryBible
from novel_manga.script_planning import (
    deterministic_chapter_diagnosis,
    evaluate_script_quality,
    normalize_chronological_plan,
    validate_chapter_diagnosis,
    validate_series_state,
)


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
    assert 36 < semantic_report.max_turn_char_count <= 60
    assert semantic_report.hard_overflow_turn_count == 0
    assert not any(issue.code == "spoken_turn_too_long" for issue in semantic_report.issues)


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
    assert "script_too_short" in codes
    assert "too_few_turns" in codes
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
