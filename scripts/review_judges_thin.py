"""Prepare evidence and make one existing review request; no production dispatch."""
from __future__ import annotations

from pathlib import Path
import novel_manga.model_client as model_client
import novel_manga.models as review_models
from novel_manga.models import Character
import novel_manga.review.contracts as review_contracts
import novel_manga.review.prompts as review_prompts
import novel_manga.review.policy as review_policy
import review_evidence_thin as review_evidence


def judge_character_cards(character: Character, views: list[Path]) -> dict:
    parts = [model_client.image_part(view, review_contracts.CARD_SIDE) for view in views]
    parts.append({"type": "text", "text": (
        f"这{len(views)}张图是同一角色的定妆卡（第1张全身/转身，第2张表情特写，若有）。角色设定：{review_prompts.describe(character)}。\n"
        "回答：photoreal 是画面像真人照片或真实人物肖像的程度（0到1；动画/CG 角色应低于0.4，真人质感高于0.6）；"
        "matches_description 只看性别、年龄段、发型、服装款式和主色是否符合设定，识别物、道具、姿势和表情不计入（定妆卡不必手持道具），不符合时在 mismatch 写出具体哪项；"
        "same_person 是各张是否同一人；text_or_extra_people 是画面里是否有文字、水印、Logo 或多余的人物。只输出JSON。"
    )})
    return model_client.ask_json(parts, review_contracts.CHARACTER_CARD_SCHEMA, name="character_card")


def judge_location_card(location: str, expected_time: str, view: Path, *, location_policy: str = "empty") -> dict:
    people_rule = ("应为没有任何人物的空场景" if location_policy == "empty" else "主体应空无一人，远处少量模糊的背景行人可以接受")
    people_question = ("has_people 画面里是否有人或人形剪影" if location_policy == "empty" else "has_people 近景或中景是否有清晰的人物（远处模糊的背景行人不算）")
    parts = [model_client.image_part(view, review_contracts.CARD_SIDE), {"type": "text", "text": (
        f"这是地点卡，{people_rule}。地点设定：{location}。" + (f"预期时间与光源：{expected_time}。" if expected_time else "")
        + f"回答：{people_question}；time_of_day 画面表现的时间；text 是否有可读文字、牌匾字样、水印；"
        "matches_description 场景内容是否符合设定。只输出JSON。"
    )}]
    return model_client.ask_json(parts, review_contracts.LOCATION_CARD_SCHEMA, name="location_card")


def judge_clip(clip: dict, video: Path, bible: review_models.StoryBible, location_time: dict, hypothesis: str, work_dir: Path) -> dict:
    if review_evidence.review_mode(work_dir) == "verify":
        return judge_clip_verify(clip, video, bible, location_time, hypothesis, work_dir)
    parts, evidence = review_evidence.collect_clip_evidence(clip, video, bible, work_dir)
    parts.append({"type": "text", "text": review_prompts.classic_prompt(clip, location_time, hypothesis, evidence)})
    return model_client.ask_json(parts, review_contracts.CLIP_SCHEMA, name="clip_review", max_tokens=600)


def script_check(clip: dict, verdict: dict, segments: dict[str, str]) -> dict | None:
    """Ask, in text only, whether the oddity the judge flagged is what the script asked for.

    The judge compares frames with the cards and is never shown the event, so in a horror story it calls
    a scripted mouth in a palm a breakdown - and its correction then tells the renderer to remove it
    (雾月 2026-09-13: 59 of 345 must_fix clips were the book's own images: "掌心的皮肤蠕动着裂开",
    "数百张面目狰狞的面庞", "化作了数十只黑色的小手").  Returns None when there is nothing to check
    against or the model failed; the caller keeps the tier it had.
    """
    event = review_prompts.scripted_event(clip)
    source = "\n".join(segments.get(str(s), "") for s in clip.get("segment_ids") or []).strip()
    complaint = "\n".join(str(verdict.get(k)) for k in ("identity_issue", "defect_issue", "story_issue") if verdict.get(k))
    if not (event or source) or not complaint:
        return None
    text = (review_contracts.SCRIPT_CHECK_RULES + f"\n\n剧本事件：{event or '（无）'}\n原文：{source[:1500] or '（无）'}\n"
            f"审查员指出的问题：\n{complaint[:900]}")
    try:
        answer = model_client.ask_json([{"type": "text", "text": text}], review_contracts.SCRIPT_CHECK_SCHEMA, name="script_check", max_tokens=300)
    except Exception as error:  # noqa: BLE001 - advisory: a failed check changes nothing
        model_client.log(f"script check failed: {type(error).__name__}: {str(error)[:120]}")
        return None
    return {"scripted": bool(answer.get("scripted")), "evidence": str(answer.get("evidence") or "")[:200],
            "note": str(answer.get("note") or "")[:160]}


def instruction_for(evidence: str, event: str = "", passage: str = "") -> str:
    """One imperative sentence for the video model - who is in frame, who does what to whom, who appears exactly once -
    composed from what the verifier saw.  Never a description of the mistake: the renderer draws what it reads."""
    prompt = ("下面是一段短剧视频被判错的原因、这一段的剧本事件和原文。写一句给视频生成模型的修正指令：只说画面里该有谁、谁对谁做什么、"
              "谁只能出现一次或不该出现，点名到人；不要复述错误，不要用“不是”“错误”“上一次”这类描述过去的话，不超过 80 字。只输出 JSON。\n"
              f"判错原因：{evidence}\n剧本事件：{event or '（无）'}\n原文：{passage[:600] or '（无）'}")
    try:
        return str(model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.INSTRUCTION_SCHEMA, name="instruction", max_tokens=200).get("instruction") or "").strip()
    except Exception:  # noqa: BLE001 - the caller keeps its old note
        return ""


def judge_clip_verify(clip: dict, video: Path, bible: review_models.StoryBible, location_time: dict, hypothesis: str, work_dir: Path) -> dict:
    parts, evidence = review_evidence.collect_clip_evidence(clip, video, bible, work_dir, verify=True)
    parts.append({"type": "text", "text": review_prompts.verify_prompt(clip, location_time, evidence)})
    answer = model_client.ask_json(parts, review_contracts.VERIFY_SCHEMA, name="clip_verify", max_tokens=900)
    return review_policy.verify_to_verdict(answer)
