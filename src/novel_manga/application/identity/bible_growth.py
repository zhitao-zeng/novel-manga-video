"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
from novel_manga.models.bible import Prop
import novel_manga.story.identity as story_names
import novel_manga.story.name_rules as name_rules
from novel_manga.util import atomic_write_json


import novel_manga.application.review.cast_endpoint as service_cast_endpoint
import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.review.entity_extraction as service_entity_extraction
import novel_manga.application.review.entity_extraction as service_entity_extraction
import novel_manga.application.review.entity_extraction as service_entity_extraction
import novel_manga.application.assets.character_design as service_character_design
import novel_manga.application.identity.volume_context as service_volume_context

GENERIC_PLACES = re.compile(r"^(路上|远处|门外|门口|外面|里面|附近|某处|空中|天上|地上|前方|后方|这里|那里|途中|路边)$")


JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["people"],
                "properties": {"people": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["name", "verdict", "why"],
                    "properties": {"name": {"type": "string"},
                                   "verdict": {"type": "string", "enum": ["角色", "路人", "不是人物"]},
                                   "why": {"type": "string", "maxLength": 60}}}}}}

def judge_unknown(novel_dir: Path, names: list[str], chapter_text: str, chapter_index: int) -> dict:
    """Rule, once, on people the whole-book reading never registered, and write the answer down.

    These are the names in neither list: not characters the reading kept, not forms it saw and
    declined.  Waving them through demotes anyone the reading missed to a walk-on; blocking them all
    loses a chapter to a door knocker.  So they get a verdict on this chapter's own evidence, and the
    verdict joins reading_cast.json - asked once per name for the whole book, a record rather than a
    guess repeated every time the name appears.
    """
    path = Path(novel_dir) / "reading_cast.json"
    if not names or not path.is_file():
        return {}
    prompt = ("下面是一章小说原文，以及原文里出现、但全书通读时没有收进名单的几个指称。"
              "对每一个判断它属于哪一类：\n"
              "角色——这本书的一个人物，会再出现，值得单独定妆；\n"
              "路人——只在这一章露面的无名人物，靠画面描述出镜即可；\n"
              "不是人物——动物、机械、道具或环境里会动的东西，本来就不该当人看待。\n"
              "只看原文证据。不要为了凑名单把路人说成角色，也不要因为名字短就当路人。"
              "拿不准是路人还是不是人物时，按不是人物填——两者在画面上的处理相同。只输出 JSON。\n"
              f"指称：{'、'.join(names)}\n\n原文：\n{chapter_text[:12000]}")
    try:
        rows = model_client.ask_json([{"type": "text", "text": prompt}], JUDGE_SCHEMA,
                                     name="unknown_actors", max_tokens=800, settings=service_cast_endpoint.bible_settings()).get("people", [])
    except Exception as error:  # noqa: BLE001 - no verdict is not a verdict; the binding check still stops
        # with the reason: swallowing it cost an afternoon on 巨大机器人, where the call was not failing
        # at all - the model was returning an empty list because both answers it was offered were wrong
        model_client.log(f"bible ch{chapter_index}: 判不了这几个指称（{type(error).__name__}: "
                         f"{str(error)[:160]}）：{names}")
        return {}
    verdicts = {str(r.get("name", "")).strip(): str(r.get("verdict", "")) for r in rows}
    verdicts = {name: verdicts[name] for name in names
                if verdicts.get(name) in {"角色", "路人", "不是人物"}}
    if not verdicts:
        model_client.log(f"bible ch{chapter_index}: 这几个指称问了但没得到判决：{names}")
    if not verdicts:
        return {}
    # 路人 and 不是人物 are both filmed as what the shot describes rather than as a card; the difference
    # is honesty about what they are, and a model that is offered only the wrong two answers gives none
    with open(novel_dir / "reading_cast.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["declined"] = sorted({*(data.get("declined") or []),
                                   *(n for n, v in verdicts.items() if v in {"路人", "不是人物"})})
        data["characters"] = sorted({*(data.get("characters") or []),
                                     *(n for n, v in verdicts.items() if v == "角色")})
        atomic_write_json(path, data)
    model_client.log(f"bible ch{chapter_index}: 读书没见过的指称，补判并记下："
                     + "、".join(f"{n}={v}" for n, v in verdicts.items()))
    return verdicts


def grow_unbound_roster(novel_dir: Path, context: dict, chapter_text: str, chapter_index: int) -> list[str]:
    """Build the people this chapter has on stage that the reading vouches for and the bible lacks.

    Growth reads a name scan of the chapter; binding reads the chapter's own source reading.  They are
    two readers of one chapter and they disagree: the scan listed no 猫女 in chapter 39 of
    在美漫当心灵导师的日子 while the reading had her on stage.  When they do, the chapter cannot be
    planned at all - the reading calls her a character, so she may not be demoted to an extra, and the
    bible holds nobody for her to bind to.

    Rather than try to make two readers agree, the side that knows exactly who is missing asks for them
    by name.  Only names the reading vouched for; for anyone else the binding check's job is still to
    stop, because nobody has decided anything about them.
    """
    reading = service_reading_roster.reading_decisions(novel_dir)
    if not reading["roster"]:
        return []
    aliases = service_reading_roster.load_aliases(novel_dir)
    from novel_manga.application.identity.flow import reading_aliases
    aliases.update(reading_aliases(novel_dir, chapter_index))
    bible_path = novel_dir / "story_bible.json"
    with open(novel_dir / "story_bible.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bible = review_models_StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
        known = [c.name for c in bible.characters]
        wanted: dict[str, dict] = {}
        for actor in context.get("unmatched_actors", []):
            if context.get('semantic_review', {}).get('approved') and actor['name'] in (context.get('semantic_extras') or []):
                continue
            if actor.get("presence") not in {"on_stage", "voice"} or actor.get("kind") != "individual":
                continue
            # The chapter says 猫女 and the alias map knows that is 赛琳娜: build the person, not the
            # nickname, or the book ends up with a card for each of her names.
            name = str(actor.get("name") or "").strip()
            name = aliases.get(name, name)
            if not name or name in wanted or story_names.name_matches(name, known):
                continue
            if story_names.name_matches(name, reading["roster"]):
                wanted[name] = {"mentions": 1, "chapters": [chapter_index], "kind": "具名角色", "speaks": True}
        if not wanted:
            return []
        model_client.log(f"bible ch{chapter_index}: 本章在场、读书认的人，圣经里没有，补建：{list(wanted)}")
        # Everyone here is somebody the reading vouched for - that is the condition for being asked
        # about at all - so the fill has to be told, as growth tells it, or "疑为某某的别称" refuses
        # the very people this function exists to build.
        bible, filled, needs_human, _ = service_character_design.fill_characters(bible, bible_path, wanted, chapter_text, reading["cast"])
        if filled:
            atomic_write_json(bible_path, bible.model_dump(mode="json"))
        # The reasons used to be dropped here.  A chapter then died on "source actors need catalogue
        # bindings" with 补建 logged twice above it and not a word on why neither attempt took, while a
        # sibling chapter built the same person seconds later from a fuller page.
        refused = {name: why for name, why in needs_human.items() if name in wanted and name not in filled}
        if refused:
            model_client.log(f"bible ch{chapter_index}: 补建没建成：" + "；".join(f"{name}（{why}）" for name, why in refused.items()))
        return filled


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
    aliases = service_reading_roster.load_aliases(novel_dir)
    growth_path = novel_dir / "bible_growth.json"
    growth = json.loads(growth_path.read_text(encoding="utf-8")) if growth_path.is_file() else {}
    held = {name for entry in growth.values() for name in [*entry.get("suggestions", {}), *entry.get("needs_human", {})]}
    counts: dict[str, dict] = {}
    name_rows = scan["names"] if scan else service_entity_extraction.extract_names(chapter_text)
    for row in name_rows:
        name = re.sub(r"\s+", "", str(row.get("name", "")))
        if name in aliases and not story_names.name_matches(aliases[name], known):
            # The chapter says 阿福 and the alias map knows that is 阿尔弗雷德 - who is not in the bible
            # yet, and nobody was ever going to build him.  Skipping an alias outright is right, or a
            # nickname becomes a second card of someone who already has one; but when the person behind
            # it has no card at all, and this chapter never uses his own name, the skip loses the person.
            # So the mention counts towards him, and the roster gate below rules on him as it would on
            # any other name.  ch17 of 在美漫当心灵导师的日子 was unplannable for exactly this: 阿福 on
            # stage, bound to nothing, because 阿尔弗雷德 was never built.
            name = aliases[name]
        if row.get("kind") == "群体" or not name or name in name_rules.GENERIC_NAMES or story_names.name_matches(name, known) or name in aliases or name in held:
            continue
        entry = counts.setdefault(name, {"mentions": 0, "chapters": [chapter_index], "kind": row.get("kind"), "speaks": False})
        entry["mentions"] += int(row.get("mentions", 0))
        entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
    missing = {name: info for name, info in counts.items() if info["mentions"] >= 2 and (info["kind"] == "具名角色" or info["speaks"])}
    # "or it speaks" lets anything with two mentions and a close-up in - a door knocker, a
    # hound - because this runs on one chapter and cannot tell whether it ever comes back.
    # A reading of the whole book can, so when there is one it decides who the book has.
    reading = service_reading_roster.reading_decisions(novel_dir)
    roster = reading["roster"]
    if roster:
        # The roster's "no" is a ruling only where the reading looked.  reading_decisions says so in
        # as many words - "outside it, absence means nothing at all" - and the planner's half of this
        # has always honoured it; this gate did not, and nobody could tell while every chapter planned
        # was one the reading had read.  The first time a book was taken past its reading (chapter 101
        # of 在美漫当心灵导师的日子, read to 100) everyone who first appears later was refused here, and
        # each of them then had to be argued back in downstream, one model call and one race at a time.
        # Past the coverage the chapter's own evidence decides, as it does for a book with no reading
        # at all.  A form the reading saw and declined stays declined: that was a ruling on the form.
        # A reading that recorded no coverage is taken at its word everywhere, as it was before this:
        # "how far it read" cannot be assumed to be "not this far".
        covered = not reading["chapters"] or chapter_index in set(reading["chapters"])
        declined = set(reading["declined"])
        turned_down = [name for name in missing if not story_names.name_matches(name, roster)
                       and (covered or name in declined)]
        unread = [name for name in missing if name not in turned_down
                  and not story_names.name_matches(name, roster)]
        missing = {name: info for name, info in missing.items() if name not in turned_down}
        if turned_down:
            why = "读书名单里没有" if covered else "读书看过并否决的指称"
            model_client.log(f"bible ch{chapter_index}: {why}，不建档：{turned_down}")
        if unread:
            model_client.log(f"bible ch{chapter_index}: 读书只读到第 {max(reading['chapters'], default=0)} 章，"
                             f"这一章按本章证据建档：{unread}")
        # 名单里的人，本章点到名就建档。mentions >= 2 和"具名角色或开口说话"都是只看得见一章时
        # 的噪声过滤；读书已经拿全书证据过滤过一遍，再滤一遍就是拿更差的信息否决更好的判断。
        # 而绑定检查要求本章在场的人必须绑得上——上游多拦一个，下游就整章排不出来。
        # 这个条件收紧一次就换一个名字再卡一次：ch6 暴风女被点名一次卡住，补了"在场"；ch17 阿福
        # 在场但不说话，又卡住。所以不再猜"够不够重要"——读书说他是人，本章提到他，就建档。
        vouched = {name: info for name, info in counts.items()
                   if name not in missing and story_names.name_matches(name, roster)}
        if vouched:
            model_client.log(f"bible ch{chapter_index}: 读书名单里有、本章在场，补建档：{list(vouched)}")
            missing = {**missing, **vouched}
    filled: list[str] = []
    needs_human: dict = {}
    suggestions: dict = {}
    if missing:
        cast = service_reading_roster.reading_decisions(novel_dir)["cast"]
        facts = service_reading_roster.reading_character_facts(novel_dir)
        fact_options = {"character_facts": facts, "aliases": aliases, "chapter": chapter_index} if facts else {}
        bible, filled, needs_human, suggestions = service_character_design.fill_characters(bible, bible_path, missing, chapter_text, cast, **fact_options)
        for name, entry in suggestions.items():
            target = str(entry.get("same_as") or "")
            if cast and story_names.name_matches(name, cast):
                # Never write down that one of the book's own characters is somebody else's nickname.
                model_client.log(f"bible ch{chapter_index}: 读书把 {name} 列为独立角色，"
                                 f"不采信「疑为 {target} 的别称」")
                continue
            if target and story_names.name_matches(target, known):
                aliases[name] = next(k for k in known if story_names.name_matches(target, [k]))
        if aliases:
            atomic_write_json(novel_dir / "bible_aliases.json", aliases)
    known_locations = [full.split("：", 1)[0].strip() for full in bible.locations]
    added_locations: list[str] = []
    location_rows = scan["locations"] if scan else service_entity_extraction.extract_locations(chapter_text, known_locations)
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
    timed = service_volume_context.fill_location_time(novel_dir, location_rows, known_locations)
    if timed:
        model_client.log(f"bible ch{chapter_index}: location_time filled for {timed}")
    added_props: list[str] = []
    props_on = _props_enabled(novel_dir)
    if props_on:
        known_prop_names = [p.name for p in bible.props]
        prop_rows = (scan or {}).get("props") or service_entity_extraction.extract_props(chapter_text, known_prop_names,
                                                                {c.name for c in bible.characters})
        new_props = []
        for row in prop_rows:
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if not name or name in known_prop_names or story_names.name_matches(name, known):
                continue
            new_props.append(Prop(name=name, category=str(row.get("category") or "其他"),
                                  appearance=re.sub(r"\s+", " ", str(row.get("appearance", ""))).strip(),
                                  material=re.sub(r"\s+", " ", str(row.get("material", ""))).strip(),
                                  owner=str(row.get("owner") or ""), first_chapter=chapter_index,
                                  quote=str(row.get("quote") or ""), closeup=bool(row.get("closeup")),
                                  wearable=bool(row.get("wearable"))))
            known_prop_names.append(name)
            added_props.append(name)
        if new_props:
            bible = bible.model_copy(update={"props": [*bible.props, *new_props]})
            atomic_write_json(bible_path, bible.model_dump(mode="json"))
    growth[str(chapter_index)] = {"characters": filled, "locations": [x.split("：", 1)[0] for x in added_locations],
                                  **({"props": added_props} if props_on else {}),
                                  "suggestions": suggestions, "needs_human": needs_human}
    atomic_write_json(growth_path, growth)
    model_client.log(f"bible ch{chapter_index}: +{len(filled)} characters {filled or ''} +{len(added_locations)} locations {[x.split('：', 1)[0] for x in added_locations] or ''}; suggestions {list(suggestions) or 'none'}; bible now {len(bible.characters)}/{len(bible.locations)}")
    return {"characters": filled, "locations": added_locations, "suggestions": suggestions,
            **({"props": added_props} if props_on else {})}


def _props_enabled(novel_dir: Path) -> bool:
    """The book opted into prop assets at build time: profile.json carries {"props": true}.

    Read the file directly: load_profile's dict is written into chapter artifacts, so a new
    default key there would change every book's chapter_script.json.  Only book-building writes
    the flag; books in production never get it, and nothing here scans them.
    """
    try:
        return bool(json.loads((novel_dir / "profile.json").read_text(encoding="utf-8")).get("props"))
    except (OSError, ValueError):
        return False
