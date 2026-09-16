"""Interpret existing judge answers and produce tiers and feedback. No I/O or model calls."""
from __future__ import annotations

import re
from dataclasses import dataclass
import novel_manga.models as review_models
from .contracts import STORY_FATAL


BREAKDOWN = re.compile(r"尾巴|尖耳|崩坏|畸形|穿模|六指|多余(的)?(手|臂|腿|肢)|赤膊巨人|肌肉极其夸张|非人(?!类|形态)")


SWAP = re.compile(r"错误地(渲染|绘制|画)成|被(渲染|绘制|画)成|完全一致|几乎一模一样|穿了.{0,6}的(衣服|服饰)|换脸|张冠李戴"
                  r"|高度相似|长相相似|长得一样|长相.{0,4}(一致|相同|一样)|同一张脸|角色重复|重复出现|分身|克隆")


MISSING = re.compile(r"(?<!特征)(?<!道具)缺失|(?<!设定中)(?<!设定里)(?<!名单中)(?<!名单里)(?<!列表中)(?<!列表里)未出现"
                     r"|(?<!设定中)(?<!设定里)没有出现|未出场")


LEAD_ROLES = {"主角", "女主角", "男主角"}


@dataclass(frozen=True)
class ReviewRules:
    breakdown: re.Pattern = BREAKDOWN
    entity_tiers: tuple[tuple[str, str], ...] = ()


def fix_tier(verdict: dict, bible: review_models.StoryBible, rules: ReviewRules | None = None) -> str:
    """must_fix / optional / ignore for a failed clip verdict.  A viewer notices a
    broken body, a lead with the wrong face, or a speaking character who is not
    there; a side character's shirt colour or a garbled phone screen they do not."""
    rules = rules or ReviewRules()
    if verdict.get("story_ok") is False and verdict.get("story_kind") in STORY_FATAL:
        return "must_fix"  # the picture tells the wrong story, however clean it is
    if verdict.get("scripted"):
        return "optional"  # a scripted oddity never excuses assigning an action to the wrong person
    issue = str(verdict.get("identity_issue") or "") + " " + str(verdict.get("defect_issue") or "")
    if verdict.get("visual_defects") or rules.breakdown.search(issue):
        return "must_fix"
    # Whose swapped face is a retake: the bible's leads, or - when the book has an entity index, whose role field
    # is prose - its leads and majors (雾月: 莱恩 plus the twelve most-mentioned).
    leads = ([name for name, tier in rules.entity_tiers if tier in {"lead", "major"} and len(name) >= 2]
             or [c.name for c in bible.characters if str(c.role or "") in LEAD_ROLES])
    if any(name in issue for name in leads) and SWAP.search(issue):
        return "must_fix"
    if MISSING.search(issue) and any(name in issue for name in (c.name for c in bible.characters)):
        return "must_fix"
    if not verdict.get("identity_ok", True) or not verdict.get("location_ok", True) or not verdict.get("time_of_day_ok", True):
        return "optional"
    return "ignore"  # phone text, on-screen text, people count only


def flag_line(clip_id: str, verdict: dict, tier: str) -> str:
    """One line of the episode's flags: bare for must_fix, [剧本] for an oddity the book wrote, [可选] otherwise."""
    prefix = "" if tier == "must_fix" else ("[剧本] " if verdict.get("scripted") else "[可选] ")
    return f"{clip_id}: {prefix}{verdict.get('story_issue') or verdict.get('identity_issue') or verdict.get('defect_issue') or verdict.get('feedback')}"


def compose_feedback(verdict: dict, clip: dict | None = None, manifest: dict | None = None) -> str:
    """The correction appended to a failed clip's prompt.

    Text on screen gets a fixed instruction, because the model's own suggestion tends to "fix
    the subtitle" rather than remove it.  Identity issues keep the reviewer's sentence exactly
    as written: it names who should look like what and points at the reference sheets by the
    same @图片N numbering the request uses, and an attempt to reword it into a generic rule
    dropped the repair rate from 86% to 25% (2026-09-10, 15 clips).  Defects get the sentence
    plus a fixed rule about anatomy, which is what fixed the穿模 clip in that trial.

    clip and manifest are accepted so a caller can pass them; they are unused on purpose.
    """
    parts = []
    if verdict.get("story_ok") is False and verdict.get("story_kind") in STORY_FATAL and verdict.get("story_issue"):
        # What to draw, not what was wrong: the judge's feedback is written as an instruction (who must be in frame,
        # who does what to whom); story_issue describes the mistake, and H3 rendered that description back as the
        # scene (雾月 2026-09-14: "two identical white-haired women seated left and right" came back as such).
        note = str(verdict.get("feedback") or "").strip()
        parts.append(note if note and not re.search(r"字幕|文字", note) else "按原文修正剧情：" + str(verdict["story_issue"]).strip())
    if verdict.get("text_or_watermark"):
        parts.append("除手机屏幕上指定的聊天消息外，画面中不得出现任何文字、字幕、弹幕或水印，台词只以语音出现")
    if verdict.get("chat_text_ok") is False:
        parts.append("手机屏幕上的消息文字必须是清晰端正的简体中文，内容与指定的消息逐字一致，不得乱码或出现无关文字；屏幕要正对镜头、占画面主体")
    if not verdict.get("identity_ok", True):
        note = str(verdict.get("feedback") or "").strip()
        parts.append(note if note and not re.search(r"字幕|文字", note)
                     else "每个角色必须与其角色卡一致，不得把一个角色画成另一个角色的相貌或服装")
    if verdict.get("visual_defects"):
        parts.append("人物肢体、面部和道具必须结构正常：不得多出或缺少肢体、手指，不得穿模、重影或出现多余物体；"
                     "动作幅度放小，保持角色形体稳定")
    joined = "；".join(dict.fromkeys(part.rstrip("。；;，, ") for part in parts if part))  # the same instruction once
    return joined or str(verdict.get("feedback") or "").strip()


def verify_to_verdict(answer: dict) -> dict:
    """The verifier's answer in the shape the review file, fix_tier and the board already read."""
    flags = {k: bool(answer.get(k)) for k in ("same_person_twice", "species_or_gender_wrong", "action_by_wrong_person", "actor_missing", "lead_face_swapped")}
    obvious = answer.get("verdict") == "obvious" or any(flags.values())
    subtle = answer.get("verdict") == "subtle" and not obvious
    evidence = str(answer.get("evidence") or "")[:300]
    people = answer.get("people") or []
    return {
        "visible_people": len(people) if isinstance(people, list) else 0,
        "identity_ok": not (flags["same_person_twice"] or flags["species_or_gender_wrong"] or flags["lead_face_swapped"] or subtle),
        "identity_issue": evidence if (flags["same_person_twice"] or flags["species_or_gender_wrong"] or flags["lead_face_swapped"] or subtle) else "",
        "location_ok": True, "time_of_day_ok": True, "location_issue": "",
        "text_or_watermark": bool(answer.get("ghost_text")),
        "chat_text_ok": True, "chat_text_issue": "",
        "visual_defects": False, "defect_issue": "",
        "story_ok": not obvious,
        "story_kind": "无问题" if not obvious else ("原文中有动作的人物缺席" if flags["actor_missing"] and not flags["action_by_wrong_person"] else "动作落在错误的人物身上"),
        "story_issue": evidence if obvious else "",
        "severity": "fail" if obvious else ("minor" if subtle else "pass"),
        "feedback": (str(answer.get("instruction") or "").strip() or ("按原文修正剧情：" + evidence)) if obvious else "",
        "verify": {"verdict": answer.get("verdict"), **flags, "ghost_text": bool(answer.get("ghost_text")),
                   "evidence": evidence, "instruction": str(answer.get("instruction") or ""),
                   **({"repair_advice": answer["repair_advice"]} if answer.get("repair_advice") else {}),
                   "people": [f"{p.get('who')}({p.get('gender')}{'/动物' if p.get('is_animal') else ''}) {p.get('doing')} [{p.get('frames')}]" for p in people][:8] if isinstance(people, list) else []},
    }
