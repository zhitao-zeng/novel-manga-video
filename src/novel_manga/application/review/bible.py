"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
from novel_manga.models.bible import Character
import novel_manga.story.identity as story_names
import novel_manga.story.name_rules as name_rules
import novel_manga.review.contracts as review_contracts
from novel_manga.util import atomic_write_json


VAGUE = re.compile(r"未详|不详|未知|未提及|未描述|建议|待定|无描述")


GENERIC_PLACES = re.compile(r"^(路上|远处|门外|门口|外面|里面|附近|某处|空中|天上|地上|前方|后方|这里|那里|途中|路边)$")


EXISTING_BUDGET = 15000


def extract_names(chapter_text: str) -> list[dict]:
    prompt = (
        "列出这段小说里出现的所有人物：具名角色（如 主角的名字）、以称呼或身份指代但反复出现的个体（如 张管家、穿白衬衫的青年），以及群体（如 同学们、围观路人）。"
        "每个给出在本段出现的大致次数，以及是否有台词或近景出场（speaks_or_close_up）。同一人物的不同叫法合并为最常用的一个；"
        "如果一个称呼在段中后来被点出姓名，只保留姓名。只输出JSON。\n\n" + chapter_text
    )
    return model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.NAME_SCHEMA, name="names", max_tokens=1500).get("characters", [])


def excerpts(text: str, name: str, limit: int = 12) -> str:
    sentences = [s for s in re.split(r"(?<=[。！？])", text) if name in s]
    return "".join(sentences[:limit])[:1600]


def reading_roster(novel_dir: Path) -> list[str]:
    """Everyone the lean reading decided this book has, names and aliases alike.

    Empty when the book has not been read, and then growth keeps its own judgement.
    """
    path = Path(novel_dir) / "reading_cast.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = [str(n).strip() for n in data.get("characters") or [] if str(n).strip()]
    names += [str(a).strip() for a in (data.get("aliases") or {}) if str(a).strip()]
    return names



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
    bible = review_models_StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    novel = read_novel(source, novel_id=novel_dir.name, title=bible.novel_title)
    counts: dict[str, dict] = {}
    for episode in novel.episodes[:chapters] if chapters else novel.episodes:
        for row in extract_names(episode.source_text):
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if row.get("kind") == "群体" or not name or name in name_rules.GENERIC_NAMES:
                continue
            entry = counts.setdefault(name, {"mentions": 0, "chapters": [], "kind": row.get("kind"), "speaks": False})
            entry["mentions"] += int(row.get("mentions", 0))
            entry["chapters"].append(episode.index)
            entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
            if row.get("kind") == "具名角色":
                entry["kind"] = "具名角色"
        model_client.log(f"bible: chapter {episode.index} scanned, {len(counts)} names so far")
    known = [c.name for c in bible.characters]
    missing = {
        name: info for name, info in counts.items()
        if info["mentions"] >= review_contracts.MIN_MENTIONS and not story_names.name_matches(name, known) and (info["kind"] == "具名角色" or info["speaks"])
    }
    # "or it speaks" lets a one-scene driver into the bible with a card of its own, because growth
    # only ever sees one chapter and cannot tell that he never comes back.  A reading of the whole
    # book can, so when there is one, it decides.
    roster = reading_roster(novel_dir)
    if roster:
        turned_down = [name for name in missing if not story_names.name_matches(name, roster)]
        missing = {name: info for name, info in missing.items() if name not in turned_down}
        if turned_down:
            model_client.log(f"bible: reading says these are extras, not adding: {turned_down}")
    report = {"policy": review_contracts.POLICY, "bible_characters": known, "extracted": counts, "missing": missing, "filled": [], "needs_human": {}, "suggestions": {}}
    if fill and missing:
        bible, report["filled"], report["needs_human"], report["suggestions"] = fill_characters(bible, bible_path, missing, novel.text)
    atomic_write_json(novel_dir / "bible_review.json", report)
    model_client.log(f"bible: missing {list(missing) or 'none'}; added {report['filled'] or 'nothing'}; for a human: {list(report['needs_human']) or 'nothing'}")
    return report


def fill_characters(bible: review_models_StoryBible, bible_path: Path, missing: dict, text: str) -> tuple[review_models_StoryBible, list[str], dict, dict]:
    """Ask for casting entries for the missing names; add the confident proper
    names, keep appellations and vague entries as suggestions for a human."""
    known = [c.name for c in bible.characters]
    filled: list[str] = []
    needs_human: dict[str, str] = {}
    suggestions: dict[str, dict] = {}
    if True:
        # The whole cast used to go in here.  诸天 grew to 1310 characters = 81,575 characters of
        # prompt against a 65,536 context, so every fill request 400ed and bible growth stopped
        # for good.  Only plausible same_as candidates are worth showing: the ones this chapter
        # names, then the earliest entries (ids are positions, appended only, so the opening cast
        # is the recurring one), inside EXISTING_BUDGET.  role cannot rank them - in 诸天 only 8 of
        # 1310 entries say 配角 and many hold a whole appearance paragraph instead.
        lines = [(c.name, f"- {c.name}：{c.gender}，{c.age}，{c.appearance[:60]}") for c in bible.characters]
        plausible = {name for name, _ in lines if len(name) >= 2 and (
            name in text or any(len(wanted) >= 2 and (wanted in name or name in wanted) for wanted in missing))}
        ordered = [line for name, line in lines if name in plausible]
        ordered += [line for name, line in lines if name not in plausible]
        kept, used = [], 0
        for line in ordered:
            if used + len(line) + 1 > EXISTING_BUDGET:
                break
            kept.append(line)
            used += len(line) + 1
        existing = "\n".join(kept)
        prompt = (
            "圣经里已有这些角色：\n" + existing + "\n\n下面是原文里反复出现但圣经缺失的人物及其原文摘录。为每个人物判断：same_as 填写它其实是哪个已有角色（或本列表中另一个人物）的另一种叫法，不是则填空字符串；"
            "confidence 是你对“这是一个需要单独定妆的独立人物”的把握（0到1）。然后写一条可跨集复用的选角条目（性别、年龄段、外貌、服装、发型、配色、基础服装、识别物），"
            "只写原文能支持的信息，外貌和服装必须是具体可画的描述，原文没写的做克制设计，不要写“未详”。"
            "外貌只写稳定特征（体型、脸型、肤色、发型、年龄感），不写当下的状态：受伤、流血、伤痕、表情、哭泣、脏污、正在做的事都不算外貌。只输出JSON。\n\n"
            + "\n\n".join(f"【{name}】（出现 {info['mentions']} 次，章 {sorted(set(info['chapters']))}）\n{excerpts(text, name)}" for name, info in missing.items())
        )
        rows = model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.FILL_SCHEMA, name="fill", max_tokens=3000).get("characters", [])
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
            elif story_names.name_matches(name, known):
                reason = "与已有角色重名"
            if not reason and name_rules.APPELLATION.search(name):
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


def extract_locations(chapter_text: str, known_locations: list[str]) -> list[dict]:
    recent = "、".join(known_locations[-30:])
    prompt = (
        "列出这段小说里发生场景的地点：能画成一张空场景图的具体地方（如 楚家广场、山崖之巅、赤岩城药材店），每个给一句可画的视觉描述"
        "（空间、主要物件、时间与光线）和本段在此发生的场景数。不要列泛指的地点（路上、远处、门口）。"
        # The description is pasted straight into the empty-scene card prompt, which forbids people; and the
        # plate is reused by every shot in that place, so one scene's state would be baked into all of them.
        "描述写这个地方长期不变的样子，当成一张没有人的背景板来写：不要写任何人、人群、人影或剪影，"
        "也不要写只属于本段这一场戏的临时状态（摊开的行李、摆出来的东西、正在发生的事）。"
        "写到碑文、匾额、招牌、书页这类本来有字的东西时，写成看不出字形的样子（风化的刻痕、磨平的笔画、模糊的符号），不要写可辨读的文字——地点卡的判定会把可读文字判成缺陷。"
        "另给每个地点判断 time_of_day（本段在此发生时通常是白天、夜晚、黄昏、清晨，说不清写不定）和 main_light"
        "（主光源，如 煤气灯、壁炉、窗外日光、篝火、洞顶天光、手机屏幕；按原文写，没写就按地点常识）。"
        + (f"已有的地点名：{recent}。如果本段的地点就是其中之一，name 必须原样使用已有名字。" if recent else "")
        + "只输出JSON。\n\n" + chapter_text
    )
    return model_client.ask_json([{"type": "text", "text": prompt}], review_contracts.LOCATION_SCHEMA, name="locations", max_tokens=1200).get("locations", [])


def load_aliases(novel_dir: Path) -> dict:
    path = novel_dir / "bible_aliases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def scan_chapter(chapter_text: str, known_locations: list[str], names: bool = True) -> dict:
    """The model calls of bible growth that do not depend on the bible's state:
    the chapter's proper names and its locations.  Safe to run for several
    chapters at once; grow_bible() then commits them in order under the lock.
    With names=False the caller supplies the names (the entity ledger does)."""
    return {"names": extract_names(chapter_text) if names else [], "locations": extract_locations(chapter_text, known_locations)}


def grow_bible(novel_dir: Path, chapter_text: str, chapter_index: int, scan: dict | None = None) -> dict:
    """Grow the bible under a novel-wide lock.

    Several planners run at once (one per block of chapters); growth is a
    read-modify-write of story_bible.json, bible_aliases.json and
    bible_growth.json, and two of them interleaving would drop one set of new
    characters - which then come back later as duplicates.  The lock is held
    across the model call too; growth is rare (every fifth chapter per block)
    so serialising it costs nothing noticeable.
    """
    with open(novel_dir / "story_bible.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _grow_bible_unlocked(novel_dir, chapter_text, chapter_index, scan)


def _grow_bible_unlocked(novel_dir: Path, chapter_text: str, chapter_index: int, scan: dict | None = None) -> dict:
    """Before a chapter is planned: append the chapter's new proper-named
    characters and new locations to the bible (ids are positions, so only
    appending is allowed).  Appellations become suggestions for the volume
    review.  Everything is recorded in bible_growth.json."""
    bible_path = novel_dir / "story_bible.json"
    bible = review_models_StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    known = [c.name for c in bible.characters]
    # Names already judged in earlier chapters (an alias of someone, or held
    # for a human) stay excluded: the model's per-chapter verdict is not stable
    # enough to let a nickname become a second card of the same person later.
    aliases = load_aliases(novel_dir)
    growth_path = novel_dir / "bible_growth.json"
    growth = json.loads(growth_path.read_text(encoding="utf-8")) if growth_path.is_file() else {}
    held = {name for entry in growth.values() for name in [*entry.get("suggestions", {}), *entry.get("needs_human", {})]}
    counts: dict[str, dict] = {}
    name_rows = scan["names"] if scan else extract_names(chapter_text)
    for row in name_rows:
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        if row.get("kind") == "群体" or not name or name in name_rules.GENERIC_NAMES or story_names.name_matches(name, known) or name in aliases or name in held:
            continue
        entry = counts.setdefault(name, {"mentions": 0, "chapters": [chapter_index], "kind": row.get("kind"), "speaks": False})
        entry["mentions"] += int(row.get("mentions", 0))
        entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
    missing = {name: info for name, info in counts.items() if info["mentions"] >= 2 and (info["kind"] == "具名角色" or info["speaks"])}
    # "or it speaks" lets anything with two mentions and a close-up in - a door knocker, a
    # hound - because this runs on one chapter and cannot tell whether it ever comes back.
    # A reading of the whole book can, so when there is one it decides who the book has.
    roster = reading_roster(novel_dir)
    if roster:
        turned_down = [name for name in missing if not story_names.name_matches(name, roster)]
        missing = {name: info for name, info in missing.items() if name not in turned_down}
        if turned_down:
            model_client.log(f"bible ch{chapter_index}: 读书名单里没有，不建档：{turned_down}")
    filled: list[str] = []
    needs_human: dict = {}
    suggestions: dict = {}
    if missing:
        bible, filled, needs_human, suggestions = fill_characters(bible, bible_path, missing, chapter_text)
        for name, entry in suggestions.items():
            target = str(entry.get("same_as") or "")
            if target and story_names.name_matches(target, known):
                aliases[name] = next(k for k in known if story_names.name_matches(target, [k]))
        if aliases:
            atomic_write_json(novel_dir / "bible_aliases.json", aliases)
    known_locations = [full.split("：", 1)[0].strip() for full in bible.locations]
    added_locations: list[str] = []
    location_rows = scan["locations"] if scan else extract_locations(chapter_text, known_locations)
    for row in location_rows:
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        if len(name) < 2 or GENERIC_PLACES.match(name) or int(row.get("scene_count", 0)) < 1 or story_names.name_matches(name, known_locations):
            continue
        description = re.sub(r"\s+", " ", str(row.get("description", ""))).strip()
        added_locations.append(f"{name}：{description}" if description else name)
        known_locations.append(name)
    if added_locations:
        bible = bible.model_copy(update={"locations": [*bible.locations, *added_locations]})
        atomic_write_json(bible_path, bible.model_dump(mode="json"))
    timed = fill_location_time(novel_dir, location_rows, known_locations)
    if timed:
        model_client.log(f"bible ch{chapter_index}: location_time filled for {timed}")
    growth[str(chapter_index)] = {"characters": filled, "locations": [x.split("：", 1)[0] for x in added_locations], "suggestions": suggestions, "needs_human": needs_human}
    atomic_write_json(growth_path, growth)
    model_client.log(f"bible ch{chapter_index}: +{len(filled)} characters {filled or ''} +{len(added_locations)} locations {[x.split('：', 1)[0] for x in added_locations] or ''}; suggestions {list(suggestions) or 'none'}; bible now {len(bible.characters)}/{len(bible.locations)}")
    return {"characters": filled, "locations": added_locations, "suggestions": suggestions}


def fill_location_time(novel_dir: Path, location_rows: list[dict], known_locations: list[str]) -> list[str]:
    """Write the usual time of day and main light of the scanned places into
    visual_grammar.json's location_time, for entries still empty.  The planner
    locks each stage's light to this table and the reviewer checks against it;
    an empty entry leaves both guessing (chapter 1 of 诸天 went night -> day)."""
    grammar_path = novel_dir / "visual_grammar.json"
    if not grammar_path.is_file():
        return []
    try:
        grammar = json.loads(grammar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    table = grammar.setdefault("location_time", {})
    filled: list[str] = []
    for row in location_rows:
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        when = str(row.get("time_of_day") or "").strip()
        light = re.sub(r"\s+", " ", str(row.get("main_light") or "")).strip()
        light = re.sub(r"（[^）]*）|\([^)]*\)", "", light).strip()  # no hedging notes: the planner copies this into every stage
        if len(name) < 2 or not when or when == "不定" and not light:
            continue
        key = next((k for k in known_locations if story_names.name_matches(name, [k])), name)
        if str(table.get(key, "")).strip():
            continue
        table[key] = (f"{when}，{light}为主光" if light and when != "不定" else (light + "为主光" if light else when))
        filled.append(key)
    if filled:
        atomic_write_json(grammar_path, grammar)
    return filled


def summarize_volume(novel_dir: Path, first: int, last: int) -> dict:
    """Condense a volume's chapter recaps into one arc summary.

    The planner only ever sees five chapters of recap; over hundreds of chapters
    that loses the through-line (who owes whom, which promise is outstanding).
    One summary per volume, written from the recaps the planner itself produced,
    keeps the arc in front of it for a few hundred tokens.
    """
    recap_path = novel_dir / "recap.json"
    rows = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
    rows = [row for row in rows if first <= int(row.get("chapter", 0)) <= last]
    if not rows:
        return {}
    digest = "\n".join(f"第{row['chapter']}章 {row.get('title', '')}：{row.get('summary', '')}" for row in rows)
    verdict = model_client.ask_json([{"type": "text", "text": (
        f"下面是一部长篇小说第 {first} 到 {last} 章的逐章梗概。写出这一卷的主线走向，供后续章节的改编保持连续性。\n"
        "summary：150 到 300 字，写清这一卷发生了什么、人物关系和处境有什么变化；\n"
        "open_threads：3 到 6 条仍未了结的线索（欠下的人情、未兑现的承诺、埋下的伏笔、还没露面的对手），每条一句话；\n"
        "standing：主要人物在本卷结束时的位置和状态，每人一句话。\n"
        "只写梗概里有的内容，不要推测后文。\n\n" + digest[:12000])}],
        {"type": "object", "additionalProperties": False,
         "required": ["summary", "open_threads", "standing"],
         "properties": {"summary": {"type": "string"},
                        "open_threads": {"type": "array", "items": {"type": "string"}},
                        "standing": {"type": "array", "items": {"type": "string"}}}},
        name="volume_summary", max_tokens=1400)
    row = {"from": first, "to": last, "chapters": len(rows), **verdict}
    path = novel_dir / "volumes.json"
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        volumes = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        volumes = [item for item in volumes if not (int(item.get("from", 0)) == first and int(item.get("to", 0)) == last)]
        volumes.append(row)
        atomic_write_json(path, sorted(volumes, key=lambda item: int(item.get("from", 0))))
    return row
