#!/usr/bin/env python
"""Automatic judges for the three look-before-you-pay checkpoints of the thin pipeline.

Each judge answers a fixed questionnaire with the local Qwen3.8 VLM (strict JSON,
temperature 0) and writes a report; remediation is bounded and done by the caller
(``thin_batch.py --unattended``): one redraw / one regeneration, then the item is
flagged in the delivery report and the run continues.  Nothing here blocks.

    thin_review.py bible   --novel-dir outputs/X [--source novel.md] [--fill]
    thin_review.py cards   --novel-dir outputs/X [--include-backups]
    thin_review.py episode --episode-dir outputs/X/X_3 [--video-name clip.stale.mp4]

bible   : which named characters the novel keeps mentioning that the bible lacks
          (LLM name extraction per chapter, merged); --fill adds entries for them.
cards   : per character card pair: photoreal score, matches the bible description,
          same person on both views, stray text / extra people.  Per location card:
          people present, time of day vs visual_grammar.location_time, text.
episode : per clip, 4 frames + the cast's cards + the expected lines and the ASR
          transcript: identity mix-ups, people count, location/time, text, defects.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import endpoint_order  # noqa: E402

from novel_manga.models import Character, StoryBible  # noqa: E402
from novel_manga.util import atomic_write_json, media_duration  # noqa: E402

POLICY = "thin-review-v1.13-truncation-retry"
BASE_URL = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
MODEL = os.environ.get("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project")
PHOTOREAL_LIMIT = 0.6
MIN_MENTIONS = 3
FRAMES_PER_CLIP = 4
FRAME_WIDTH = 960
CARD_SIDE = 1024
MAX_IMAGES = 8  # vLLM --limit-mm-per-prompt


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---- model access ----
def image_part(path: Path, max_side: int) -> dict:
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = min(1.0, max_side / max(image.size))
        if scale < 1.0:
            image = image.resize((round(image.width * scale), round(image.height * scale)))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}}


def ask_json(parts: list[dict], schema: dict, *, name: str, max_tokens: int = 700, timeout: float = 600.0) -> dict:
    """One strict-JSON question to the local VLM; ``parts`` is OpenAI multimodal content."""
    headers = {}
    if os.environ.get("QWEN38_LOCAL_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["QWEN38_LOCAL_API_KEY"]
    payload = {
        "model": MODEL, "temperature": 0, "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "messages": [{"role": "user", "content": parts}],
    }
    response = None
    last: Exception | None = None
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for base_url in endpoint_order(name + str(len(parts))):  # spread judges over the Qwen instances
            try:
                response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as error:
                last = error
            except httpx.HTTPStatusError as error:
                if error.response.status_code in (502, 503, 504):
                    last = error
                    continue
                raise
        else:
            assert last is not None
            raise last
    choice = response.json()["choices"][0]
    content = choice["message"].get("content") or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if choice.get("finish_reason") == "length" and max_tokens < 8000:
            # Long name lists hit the token cap and the JSON is cut off; ask
            # once more with twice the room.
            return ask_json(parts, schema, name=name, max_tokens=max_tokens * 2, timeout=timeout)
        match = re.search(r"\{.*\}", content, re.S)
        return json.loads(match.group(0)) if match else {}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}


# ---- judge 1: bible completeness ----
GENERIC_NAMES = {
    "少年", "少女", "老者", "老人", "青年", "男子", "女子", "男人", "女人", "父亲", "母亲", "爹", "娘", "众人", "弟子", "长老",
    "中年男子", "中年人", "中年女子", "女孩", "男孩", "孩子", "小孩", "管家", "族人", "侍女", "下人", "仆人", "护卫", "士兵", "路人",
}
VAGUE = re.compile(r"未详|不详|未知|未提及|未描述|建议|待定|无描述")
# "颜色/身份 + 泛称" appellations (紫裙少女, 月白老者, 墨管家): often an existing
# character before the text names them, so never auto-added; a suggested entry
# is written for a human to accept.
APPELLATION = re.compile(r"(老者|老人|青年|少年|少女|男子|女子|男人|女人|管家|长老|宗主|使者|弟子|侍女|公子|小姐|大汉|汉子|老头|老妇|妇人|姑娘|先生|夫人|老爷|少爷)$")
NAME_SCHEMA = obj({
    "characters": {"type": "array", "items": obj({
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["具名角色", "称呼或身份", "群体"]},
        "mentions": {"type": "integer"},
        "speaks_or_close_up": {"type": "boolean"},
    })},
})


def extract_names(chapter_text: str) -> list[dict]:
    prompt = (
        "列出这段小说里出现的所有人物：具名角色（如 楚焱）、以称呼或身份指代但反复出现的个体（如 墨管家、月白青年），以及群体（如 族人、围观少年）。"
        "每个给出在本段出现的大致次数，以及是否有台词或近景出场（speaks_or_close_up）。同一人物的不同叫法合并为最常用的一个；"
        "如果一个称呼在段中后来被点出姓名，只保留姓名。只输出JSON。\n\n" + chapter_text
    )
    return ask_json([{"type": "text", "text": prompt}], NAME_SCHEMA, name="names", max_tokens=1500).get("characters", [])


def name_matches(name: str, known: list[str]) -> bool:
    compact = re.sub(r"\s+", "", name)
    return any(compact == k or (len(compact) >= 2 and (compact in k or k in compact)) for k in known)


def excerpts(text: str, name: str, limit: int = 12) -> str:
    sentences = [s for s in re.split(r"(?<=[。！？])", text) if name in s]
    return "".join(sentences[:limit])[:1600]


FILL_SCHEMA = obj({"characters": {"type": "array", "items": obj({
    "name": {"type": "string"}, "same_as": {"type": "string"}, "confidence": {"type": "number"},
    "role": {"type": "string"}, "gender": {"type": "string"}, "age": {"type": "string"},
    "appearance": {"type": "string"}, "wardrobe": {"type": "string"}, "hair": {"type": "string"},
    "palette": {"type": "string"}, "base_costume": {"type": "string"}, "signature_prop": {"type": "string"},
})}})


def review_bible(novel_dir: Path, source: Path, fill: bool, chapters: int | None = None) -> dict:
    """Named characters the novel keeps mentioning that the bible lacks.

    Generic words (少年, 老者, 父亲…) never count: the planner casts such
    people as anonymous roles or background.  A candidate must be a proper
    name, or a recurring appellation that speaks or gets close-ups.  Filling
    is conservative: an entry is added only when the model is confident it is
    not one of the existing characters under another name and can describe
    appearance and wardrobe concretely; everything else is reported for a
    human to decide.
    """
    from novel_manga.ingest import read_novel
    bible_path = novel_dir / "story_bible.json"
    bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    novel = read_novel(source, novel_id=novel_dir.name, title=bible.novel_title)
    counts: dict[str, dict] = {}
    for episode in novel.episodes[:chapters] if chapters else novel.episodes:
        for row in extract_names(episode.source_text):
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if row.get("kind") == "群体" or not name or name in GENERIC_NAMES:
                continue
            entry = counts.setdefault(name, {"mentions": 0, "chapters": [], "kind": row.get("kind"), "speaks": False})
            entry["mentions"] += int(row.get("mentions", 0))
            entry["chapters"].append(episode.index)
            entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
            if row.get("kind") == "具名角色":
                entry["kind"] = "具名角色"
        log(f"bible: chapter {episode.index} scanned, {len(counts)} names so far")
    known = [c.name for c in bible.characters]
    missing = {
        name: info for name, info in counts.items()
        if info["mentions"] >= MIN_MENTIONS and not name_matches(name, known) and (info["kind"] == "具名角色" or info["speaks"])
    }
    report = {"policy": POLICY, "bible_characters": known, "extracted": counts, "missing": missing, "filled": [], "needs_human": {}, "suggestions": {}}
    if fill and missing:
        bible, report["filled"], report["needs_human"], report["suggestions"] = fill_characters(bible, bible_path, missing, novel.text)
    atomic_write_json(novel_dir / "bible_review.json", report)
    log(f"bible: missing {list(missing) or 'none'}; added {report['filled'] or 'nothing'}; for a human: {list(report['needs_human']) or 'nothing'}")
    return report


def fill_characters(bible: StoryBible, bible_path: Path, missing: dict, text: str) -> tuple[StoryBible, list[str], dict, dict]:
    """Ask for casting entries for the missing names; add the confident proper
    names, keep appellations and vague entries as suggestions for a human."""
    known = [c.name for c in bible.characters]
    filled: list[str] = []
    needs_human: dict[str, str] = {}
    suggestions: dict[str, dict] = {}
    if True:
        existing = "\n".join(f"- {c.name}：{c.gender}，{c.age}，{c.appearance[:60]}" for c in bible.characters)
        prompt = (
            "圣经里已有这些角色：\n" + existing + "\n\n下面是原文里反复出现但圣经缺失的人物及其原文摘录。为每个人物判断：same_as 填写它其实是哪个已有角色（或本列表中另一个人物）的另一种叫法，不是则填空字符串；"
            "confidence 是你对“这是一个需要单独定妆的独立人物”的把握（0到1）。然后写一条可跨集复用的选角条目（性别、年龄段、外貌、服装、发型、配色、基础服装、识别物），"
            "只写原文能支持的信息，外貌和服装必须是具体可画的描述，原文没写的做克制设计，不要写“未详”。只输出JSON。\n\n"
            + "\n\n".join(f"【{name}】（出现 {info['mentions']} 次，章 {sorted(set(info['chapters']))}）\n{excerpts(text, name)}" for name, info in missing.items())
        )
        rows = ask_json([{"type": "text", "text": prompt}], FILL_SCHEMA, name="fill", max_tokens=3000).get("characters", [])
        added: list[Character] = []
        for row in rows:
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if name not in missing:
                continue
            reason = ""
            if row.get("same_as"):
                reason = f"疑为 {row['same_as']} 的别称"
            elif float(row.get("confidence", 0)) < 0.6:
                reason = f"把握不足（{row.get('confidence')}）"
            elif VAGUE.search(row.get("appearance", "")) or VAGUE.search(row.get("wardrobe", "")) or not row.get("appearance") or not row.get("wardrobe"):
                reason = "外貌或服装描述不具体"
            elif name_matches(name, known):
                reason = "与已有角色重名"
            if not reason and APPELLATION.search(name):
                reason = "称呼类人物，可能是已有角色的另一叫法；条目已生成，需人工确认后加入"
            if reason:
                needs_human[name] = reason
                suggestions[name] = {**{k: v for k, v in row.items() if k in Character.model_fields}, "same_as": row.get("same_as", "")}
                continue
            added.append(Character(**{k: v for k, v in row.items() if k in Character.model_fields}))
            known.append(name)
        for name in missing:
            if name not in needs_human and name not in {c.name for c in added}:
                needs_human[name] = "模型未给出条目"
        if added:
            bible = bible.model_copy(update={"characters": [*bible.characters, *added]})
            atomic_write_json(bible_path, bible.model_dump(mode="json"))
        filled = [c.name for c in added]
    return bible, filled, needs_human, suggestions


LOCATION_SCHEMA = obj({"locations": {"type": "array", "items": obj({
    "name": {"type": "string"}, "description": {"type": "string"}, "scene_count": {"type": "integer"},
})}})
GENERIC_PLACES = re.compile(r"^(路上|远处|门外|门口|外面|里面|附近|某处|空中|天上|地上|前方|后方|这里|那里|途中|路边)$")


def extract_locations(chapter_text: str, known_locations: list[str]) -> list[dict]:
    recent = "、".join(known_locations[-30:])
    prompt = (
        "列出这段小说里发生场景的地点：能画成一张空场景图的具体地方（如 楚家广场、山崖之巅、赤岩城药材店），每个给一句可画的视觉描述"
        "（空间、主要物件、时间与光线）和本段在此发生的场景数。不要列泛指的地点（路上、远处、门口）。"
        + (f"已有的地点名：{recent}。如果本段的地点就是其中之一，name 必须原样使用已有名字。" if recent else "")
        + "只输出JSON。\n\n" + chapter_text
    )
    return ask_json([{"type": "text", "text": prompt}], LOCATION_SCHEMA, name="locations", max_tokens=1200).get("locations", [])


def load_aliases(novel_dir: Path) -> dict:
    path = novel_dir / "bible_aliases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def grow_bible(novel_dir: Path, chapter_text: str, chapter_index: int) -> dict:
    """Before a chapter is planned: append the chapter's new proper-named
    characters and new locations to the bible (ids are positions, so only
    appending is allowed).  Appellations become suggestions for the volume
    review.  Everything is recorded in bible_growth.json."""
    bible_path = novel_dir / "story_bible.json"
    bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    known = [c.name for c in bible.characters]
    # Names already judged in earlier chapters (an alias of someone, or held
    # for a human) stay excluded: the model's per-chapter verdict is not stable
    # enough to let a nickname become a second card of the same person later.
    aliases = load_aliases(novel_dir)
    growth_path = novel_dir / "bible_growth.json"
    growth = json.loads(growth_path.read_text(encoding="utf-8")) if growth_path.is_file() else {}
    held = {name for entry in growth.values() for name in [*entry.get("suggestions", {}), *entry.get("needs_human", {})]}
    counts: dict[str, dict] = {}
    for row in extract_names(chapter_text):
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        if row.get("kind") == "群体" or not name or name in GENERIC_NAMES or name_matches(name, known) or name in aliases or name in held:
            continue
        entry = counts.setdefault(name, {"mentions": 0, "chapters": [chapter_index], "kind": row.get("kind"), "speaks": False})
        entry["mentions"] += int(row.get("mentions", 0))
        entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
    missing = {name: info for name, info in counts.items() if info["mentions"] >= 2 and (info["kind"] == "具名角色" or info["speaks"])}
    filled: list[str] = []
    needs_human: dict = {}
    suggestions: dict = {}
    if missing:
        bible, filled, needs_human, suggestions = fill_characters(bible, bible_path, missing, chapter_text)
        for name, entry in suggestions.items():
            target = str(entry.get("same_as") or "")
            if target and name_matches(target, known):
                aliases[name] = next(k for k in known if name_matches(target, [k]))
        if aliases:
            atomic_write_json(novel_dir / "bible_aliases.json", aliases)
    known_locations = [full.split("：", 1)[0].strip() for full in bible.locations]
    added_locations: list[str] = []
    for row in extract_locations(chapter_text, known_locations):
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        if len(name) < 2 or GENERIC_PLACES.match(name) or int(row.get("scene_count", 0)) < 1 or name_matches(name, known_locations):
            continue
        description = re.sub(r"\s+", " ", str(row.get("description", ""))).strip()
        added_locations.append(f"{name}：{description}" if description else name)
        known_locations.append(name)
    if added_locations:
        bible = bible.model_copy(update={"locations": [*bible.locations, *added_locations]})
        atomic_write_json(bible_path, bible.model_dump(mode="json"))
    growth[str(chapter_index)] = {"characters": filled, "locations": [x.split("：", 1)[0] for x in added_locations], "suggestions": suggestions, "needs_human": needs_human}
    atomic_write_json(growth_path, growth)
    log(f"bible ch{chapter_index}: +{len(filled)} characters {filled or ''} +{len(added_locations)} locations {[x.split('：', 1)[0] for x in added_locations] or ''}; suggestions {list(suggestions) or 'none'}; bible now {len(bible.characters)}/{len(bible.locations)}")
    return {"characters": filled, "locations": added_locations, "suggestions": suggestions}


# ---- judge 2: cards ----
CHARACTER_CARD_SCHEMA = obj({
    "photoreal": {"type": "number"},
    "matches_description": {"type": "boolean"},
    "mismatch": {"type": "string"},
    "same_person": {"type": "boolean"},
    "text_or_extra_people": {"type": "boolean"},
    "note": {"type": "string"},
})
LOCATION_CARD_SCHEMA = obj({
    "has_people": {"type": "boolean"},
    "time_of_day": {"type": "string", "enum": ["白天", "夜晚", "黄昏或清晨", "室内不确定"]},
    "text": {"type": "boolean"},
    "matches_description": {"type": "boolean"},
    "note": {"type": "string"},
})


def describe(character: Character) -> str:
    return f"{character.name}：{character.gender}，{character.age}；外貌：{character.appearance}；发型：{character.hair}；服装：{character.wardrobe}；识别物：{character.signature_prop}"


def judge_character_cards(character: Character, views: list[Path]) -> dict:
    parts = [image_part(view, CARD_SIDE) for view in views]
    parts.append({"type": "text", "text": (
        f"这{len(views)}张图是同一角色的定妆卡（第1张全身/转身，第2张表情特写，若有）。角色设定：{describe(character)}。\n"
        "回答：photoreal 是画面像真人照片或真实人物肖像的程度（0到1；动画/CG 角色应低于0.4，真人质感高于0.6）；"
        "matches_description 只看性别、年龄段、发型、服装款式和主色是否符合设定，识别物、道具、姿势和表情不计入（定妆卡不必手持道具），不符合时在 mismatch 写出具体哪项；"
        "same_person 是各张是否同一人；text_or_extra_people 是画面里是否有文字、水印、Logo 或多余的人物。只输出JSON。"
    )})
    return ask_json(parts, CHARACTER_CARD_SCHEMA, name="character_card")


def judge_location_card(location: str, expected_time: str, view: Path) -> dict:
    parts = [image_part(view, CARD_SIDE), {"type": "text", "text": (
        f"这是地点卡，应为没有任何人物的空场景。地点设定：{location}。" + (f"预期时间与光源：{expected_time}。" if expected_time else "")
        + "回答：has_people 画面里是否有人或人形剪影；time_of_day 画面表现的时间；text 是否有可读文字、牌匾字样、水印；"
        "matches_description 场景内容是否符合设定。只输出JSON。"
    )}]
    return ask_json(parts, LOCATION_CARD_SCHEMA, name="location_card")


def time_conflicts(expected: str, seen: str) -> bool:
    if not expected or seen in {"室内不确定"}:
        return False
    wants_night = any(token in expected for token in ("夜", "月", "烛", "灯"))
    wants_day = any(token in expected for token in ("白日", "白天", "日光", "烈日", "正午", "清晨", "晨"))
    if wants_night and not wants_day:
        return seen == "白天"
    if wants_day and not wants_night:
        return seen == "夜晚"
    return False


def review_cards(novel_dir: Path, include_backups: bool = False, only_ids: set[str] | None = None) -> dict:
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    grammar_path = novel_dir / "visual_grammar.json"
    location_time = json.loads(grammar_path.read_text(encoding="utf-8")).get("location_time", {}) if grammar_path.is_file() else {}
    assets = novel_dir / "series_assets"
    report = {"policy": POLICY, "characters": {}, "locations": {}, "flags": [], "missing_cards": []}
    for index, character in enumerate(bible.characters, start=1):
        asset_id = f"character_{index:03d}"
        if only_ids is not None and asset_id not in only_ids:
            continue
        card_dir = assets / "characters" / asset_id
        views = [card_dir / name for name in ("turnaround.jpeg", "expressions.jpeg") if (card_dir / name).is_file()]
        if not views:
            if card_dir.is_dir():  # existed before: deleted for regeneration and not rebuilt yet
                report["missing_cards"].append(f"{asset_id} {character.name}")
            continue
        verdict = judge_character_cards(character, views)
        actions = []
        if float(verdict.get("photoreal", 0)) >= PHOTOREAL_LIMIT:
            actions.append("redraw_stylized")
        if not verdict.get("same_person", True) or not verdict.get("matches_description", True) or verdict.get("text_or_extra_people"):
            actions.append("regenerate")
        report["characters"][asset_id] = {"name": character.name, "views": [v.name for v in views], **verdict, "actions": actions}
        if actions:
            report["flags"].append(f"{asset_id} {character.name}: {', '.join(actions)} ({verdict.get('mismatch') or verdict.get('note', '')})")
        log(f"cards: {asset_id} {character.name} photoreal={verdict.get('photoreal')} match={verdict.get('matches_description')} same={verdict.get('same_person')} {'FLAG ' + ','.join(actions) if actions else 'ok'}")
        if include_backups:
            backups = [card_dir / name for name in ("turnaround.photoreal-rejected.jpeg", "expressions.photoreal-rejected.jpeg") if (card_dir / name).is_file()]
            if backups:
                old = judge_character_cards(character, backups)
                report["characters"][asset_id]["backups"] = {"views": [b.name for b in backups], **old}
                log(f"cards: {asset_id} backups photoreal={old.get('photoreal')} (were rejected by Seedance)")
    for index, location in enumerate(bible.locations, start=1):
        asset_id = f"location_{index:03d}"
        if only_ids is not None and asset_id not in only_ids:
            continue
        view = assets / "locations" / asset_id / "establishing.jpeg"
        short = location.split("：", 1)[0].strip()
        if not view.is_file():
            if view.parent.is_dir():
                report["missing_cards"].append(f"{asset_id} {short}")
            continue
        expected = location_time.get(short, "")
        verdict = judge_location_card(location, expected, view)
        actions = []
        if verdict.get("has_people") or verdict.get("text") or time_conflicts(expected, verdict.get("time_of_day", "")):
            actions.append("regenerate")
        report["locations"][asset_id] = {"name": short, "expected_time": expected, **verdict, "actions": actions}
        if actions:
            report["flags"].append(f"{asset_id} {short}: regenerate ({verdict.get('note', '')})")
        log(f"cards: {asset_id} {short} people={verdict.get('has_people')} time={verdict.get('time_of_day')} text={verdict.get('text')} {'FLAG' if actions else 'ok'}")
    if report["missing_cards"]:
        report["flags"].append("待重建的卡（所属章节建卡时生成）：" + "、".join(report["missing_cards"]))
        log(f"cards: not rebuilt yet: {report['missing_cards']}")
    atomic_write_json(assets / "cards_review.json", report)
    return report


# ---- judge 3: episode ----
CLIP_SCHEMA = obj({
    "visible_people": {"type": "integer"},
    "identity_ok": {"type": "boolean"},
    "identity_issue": {"type": "string"},
    "location_ok": {"type": "boolean"},
    "time_of_day_ok": {"type": "boolean"},
    "location_issue": {"type": "string"},
    "text_or_watermark": {"type": "boolean"},
    "chat_text_ok": {"type": "boolean"},
    "chat_text_issue": {"type": "string"},
    "visual_defects": {"type": "boolean"},
    "defect_issue": {"type": "string"},
    "severity": {"type": "string", "enum": ["pass", "minor", "fail"]},
    "feedback": {"type": "string"},
})


def clip_frames(video: Path, output_dir: Path, count: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    seconds = media_duration(video)
    frames = []
    for index in range(count):
        position = seconds * (0.1 + 0.8 * index / max(1, count - 1))
        target = output_dir / f"frame_{index + 1}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{position:.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "4", str(target)], check=True)
        frames.append(target)
    return frames


def judge_clip(clip: dict, video: Path, bible: StoryBible, location_time: dict, hypothesis: str, work_dir: Path) -> dict:
    by_name = {c.name: c for c in bible.characters}
    cast = [name for name in clip.get("cast", []) if name in by_name]
    cards = []
    for name in cast[:MAX_IMAGES - 3]:
        path = next((Path(ref["path"]) for ref in clip.get("references", []) if ref.get("name") == name and ref["path"].endswith("turnaround.jpeg")), None)
        if path is not None and (bible_root(work_dir) / path).is_file():  # a card being rebuilt is simply not shown
            cards.append((name, path))
    # Clips with on-screen chat get more frames: stray text tends to flash briefly.
    frame_count = min(6 if clip.get("chat_lines") else FRAMES_PER_CLIP, MAX_IMAGES - len(cards))
    frames = clip_frames(video, work_dir, frame_count)
    parts = []
    legend = []
    for number, (name, path) in enumerate(cards, start=1):
        parts.append(image_part(bible_root(work_dir) / path, CARD_SIDE))
        legend.append(f"图{number}=角色卡：{name}")
    for number, frame in enumerate(frames, start=len(cards) + 1):
        parts.append(image_part(frame, FRAME_WIDTH))
        legend.append(f"图{number}=视频第{number - len(cards)}帧")
    location = clip.get("location", "")
    expected_time = location_time.get(location, "")
    background = [name for name in clip.get("background_only", []) if name in by_name]
    lines = "；".join(f"{row.get('speaker_name') or '旁白'}：{row['text']}" for row in clip.get("lines", []))
    chats = "；".join(f"{row.get('speaker_name')}：「{row['text']}」" for row in clip.get("chat_lines", []))
    chat_screen_path = bible_root(work_dir) / "chat_screen.json"
    if chats and chat_screen_path.is_file():
        screen = json.loads(chat_screen_path.read_text(encoding="utf-8"))
        chats += f"（群名「{screen.get('group_name', '')}」、发送者昵称和界面文字也允许出现）" if screen.get("group_name") else "（发送者昵称和界面文字也允许出现）"
    text = (
        "这是一段动画短剧视频的抽帧，前面几张是本段人物的角色卡（身份依据）。\n" + "，".join(legend) + "。\n"
        f"本段设定：地点 {location}" + (f"（{expected_time}）" if expected_time else "") + f"；出场人物 {'、'.join(cast) or '无具名角色'}"
        + "".join(f"\n- {describe(by_name[n])}" for n in cast)
        + (f"\n允许出现在远处背景、不入近景不说话的角色：{'、'.join(background)}（他们出现在背景里是正常的，不算多出）" if background else "")
        + f"\n预期台词：{lines or '无'}\n语音识别出的台词：{hypothesis or '无'}\n"
        + (f"手机屏幕上应显示的群消息（这些文字允许出现）：{chats}\n" if chats else "")
        + "回答：visible_people 帧里清晰可见的人数（最多的一帧）；identity_ok 每个具名角色是否与其角色卡一致、没有两个角色长成同一人、没有角色被画成另一个角色的服装发型、近景里没有多出的具名角色（远处模糊背景里的人不算），不一致时在 identity_issue 写清是谁、哪一帧；"
        "location_ok 与 time_of_day_ok 是否符合地点和时间设定；text_or_watermark 画面是否出现手机屏幕聊天消息以外的文字、字幕、水印、Logo；"
        "chat_text_ok：若本段有应显示的群消息，帧里手机屏幕上的文字是否是清晰的简体中文且内容与预期一致（允许只显示部分或截断，不允许乱码、错字连篇或无关文字），没有预期消息时填 true，不一致时在 chat_text_issue 写清；visual_defects 是否有明显崩坏（多手、面部扭曲、肢体错位、人物穿模）；"
        "severity：identity 不一致、屏幕消息乱码或不符、文字水印或严重崩坏为 fail；仅地点时间存疑或轻微瑕疵为 minor；否则 pass。feedback 为一句给视频模型的修正指令（fail 时必填，指明谁应该长什么样、避免什么）。只输出JSON。"
    )
    parts.append({"type": "text", "text": text})
    return ask_json(parts, CLIP_SCHEMA, name="clip_review", max_tokens=600)


def compose_feedback(verdict: dict) -> str:
    """The correction appended to a failed clip's prompt.  Text on screen gets a
    fixed instruction (the model's own suggestion tends to 'fix the subtitle'
    instead of removing it); identity and defect issues use the reviewer's
    sentence, which names who should look like what."""
    parts = []
    if verdict.get("text_or_watermark"):
        parts.append("除手机屏幕上指定的聊天消息外，画面中不得出现任何文字、字幕、弹幕或水印，台词只以语音出现")
    if verdict.get("chat_text_ok") is False:
        parts.append("手机屏幕上的消息文字必须是清晰端正的简体中文，内容与指定的消息逐字一致，不得乱码或出现无关文字；屏幕要正对镜头、占画面主体")
    if not verdict.get("identity_ok", True) or verdict.get("visual_defects"):
        note = str(verdict.get("feedback") or "").strip()
        if note and not re.search(r"字幕|文字", note):
            parts.append(note)
        elif not verdict.get("identity_ok", True):
            parts.append("每个角色必须与其角色卡一致，不得把一个角色画成另一个角色的相貌或服装")
    return "；".join(parts) or str(verdict.get("feedback") or "").strip()


def bible_root(work_dir: Path) -> Path:
    # work_dir = <novel>/<episode>/work/review/<clip>; references are relative to <novel>
    return work_dir.parents[3]


def review_episode(episode_dir: Path, video_name: str = "clip.mp4") -> dict:
    novel_dir = episode_dir.parent
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    grammar_path = novel_dir / "visual_grammar.json"
    location_time = json.loads(grammar_path.read_text(encoding="utf-8")).get("location_time", {}) if grammar_path.is_file() else {}
    plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    report_path = episode_dir / "thin_media_report.json"
    media = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    selected = {row["clip_id"]: row.get("selected") or {} for row in media.get("clips", [])}
    report = {"policy": POLICY, "episode": episode_dir.name, "video_name": video_name, "clips": {}, "flags": [], "feedback": {}}
    for clip in plan["clips"]:
        if clip["kind"] != "video":
            continue
        clip_id = clip["clip_id"]
        if video_name == "clip.mp4" and selected.get(clip_id, {}).get("video"):
            video = Path(selected[clip_id]["video"])
            hypothesis = selected[clip_id].get("hypothesis", "")
        else:
            attempts = sorted((episode_dir / "work" / "clips" / clip_id).glob("attempt_*"))
            video = next((a / video_name for a in attempts if (a / video_name).is_file()), None)
            if video is None:
                continue
            asr = video.parent / ("stale_asr.json" if "stale" in video_name else "asr.json")
            hypothesis = json.loads(asr.read_text(encoding="utf-8")).get("hypothesis", "") if asr.is_file() else ""
        try:
            verdict = judge_clip(clip, video, bible, location_time, hypothesis, episode_dir / "work" / "review" / clip_id)
        except Exception as error:  # noqa: BLE001 - a judge failure is reported, never fatal
            log(f"episode {episode_dir.name} {clip_id}: review error {type(error).__name__}: {str(error)[:120]}")
            report["clips"][clip_id] = {"video": str(video), "severity": "review_error", "error": f"{type(error).__name__}: {str(error)[:300]}"}
            continue
        report["clips"][clip_id] = {"video": str(video), **verdict}
        if verdict.get("severity") == "fail":
            report["flags"].append(f"{clip_id}: {verdict.get('identity_issue') or verdict.get('defect_issue') or verdict.get('feedback')}")
            report["feedback"][clip_id] = compose_feedback(verdict)
        log(f"episode {episode_dir.name} {clip_id}: {verdict.get('severity')} people={verdict.get('visible_people')} identity={verdict.get('identity_ok')} loc={verdict.get('location_ok')}/{verdict.get('time_of_day_ok')} text={verdict.get('text_or_watermark')} defects={verdict.get('visual_defects')}" + (f" | {verdict.get('identity_issue') or verdict.get('defect_issue')}" if verdict.get("severity") != "pass" else ""))
    suffix = "" if video_name == "clip.mp4" else "." + video_name.replace(".mp4", "")
    atomic_write_json(episode_dir / f"episode_review{suffix}.json", report)
    return report


# ---- bounded remediation for the card review ----
def remediate_cards(novel_dir: Path, report: dict) -> dict:
    """Stylized redraw for near-photoreal cards, deletion (regenerated by the next
    ``--assets-only`` run) for cards that do not match their description.  Each
    card is touched at most once: a parked backup or a marker file means the
    fix was already tried and the flag stays for the delivery report."""
    from novel_manga.config import Settings
    from render_clips_thin import FramedPhanRouter, stylize_card
    from thin_profile import frame_spec, load_profile
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    provider = FramedPhanRouter(settings, frame_spec(load_profile(novel_dir)))
    assets = novel_dir / "series_assets"
    done = {"stylized": [], "deleted": [], "already_tried": []}

    def marker_for(path: Path) -> Path:
        # Not *.jpeg: the runner's purge of unreadable images deleted the old
        # marker names and the one-fix-per-card guard silently vanished.
        return path.parent / f".regenerated.{path.stem}.txt"

    def tried_before(path: Path) -> bool:
        # One fix per card for the whole series: a parked backup (stylized) or a
        # regeneration marker means the card was touched already, whatever the
        # reason this time; changing it again would make the character look
        # different from episode to episode.
        return path.with_suffix(".photoreal-rejected.jpeg").exists() or marker_for(path).exists() or (path.parent / f".regenerated.{path.name}").exists()

    def delete_for_regeneration(path: Path, label: str) -> None:
        marker = marker_for(path)
        if tried_before(path):
            done["already_tried"].append(label)
            return
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        for suffix in ("", ".task.json", ".request.json"):
            path.with_suffix(path.suffix + suffix).unlink(missing_ok=True)
            # A leftover stylize backup next to a deliberately deleted card reads
            # as "redraw in flight" to the runner, which would wait for it.
            backup.with_suffix(backup.suffix + suffix).unlink(missing_ok=True)
        marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        done["deleted"].append(label)

    for asset_id, row in report.get("characters", {}).items():
        card_dir = assets / "characters" / asset_id
        for view in row.get("views", []):
            path = card_dir / view
            label = f"{asset_id}/{view}"
            if not path.is_file():
                continue
            if "redraw_stylized" in row["actions"]:
                if tried_before(path):
                    done["already_tried"].append(label)
                    continue
                log(f"cards: stylizing {label}")
                stylize_card(provider, path)
                done["stylized"].append(label)
            elif "regenerate" in row["actions"]:
                delete_for_regeneration(path, label)
    for asset_id, row in report.get("locations", {}).items():
        if "regenerate" in row.get("actions", []):
            delete_for_regeneration(assets / "locations" / asset_id / "establishing.jpeg", f"{asset_id}/establishing.jpeg")
    log(f"cards: stylized {done['stylized'] or 'none'}, deleted for regeneration {done['deleted'] or 'none'}, already tried {done['already_tried'] or 'none'}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("bible"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--source", type=Path); p.add_argument("--fill", action="store_true")
    p = sub.add_parser("cards"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--include-backups", action="store_true")
    p = sub.add_parser("episode"); p.add_argument("--episode-dir", type=Path, required=True); p.add_argument("--video-name", default="clip.mp4")
    p = sub.add_parser("grow"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--source", type=Path); p.add_argument("--chapter", type=int, required=True)
    args = parser.parse_args()
    if args.command == "grow":
        from novel_manga.ingest import read_novel
        novel_dir = args.novel_dir.resolve()
        source = args.source or Path(json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))["source"])
        novel = read_novel(Path(source).resolve(), novel_id=novel_dir.name)
        print(json.dumps(grow_bible(novel_dir, novel.episodes[args.chapter - 1].source_text, args.chapter), ensure_ascii=False, indent=1))
        return 0
    if args.command == "bible":
        novel_dir = args.novel_dir.resolve()
        source = args.source or Path(json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))["source"])
        report = review_bible(novel_dir, Path(source).resolve(), args.fill)
        print(json.dumps({"missing": report["missing"], "filled": report["filled"]}, ensure_ascii=False, indent=1))
    elif args.command == "cards":
        report = review_cards(args.novel_dir.resolve(), args.include_backups)
        print(json.dumps({"flags": report["flags"]}, ensure_ascii=False, indent=1))
    else:
        report = review_episode(args.episode_dir.resolve(), args.video_name)
        print(json.dumps({"flags": report["flags"], "feedback": report["feedback"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
