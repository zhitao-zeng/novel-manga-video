"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
import novel_manga.llm.client as model_client
import novel_manga.story.identity as story_names
from novel_manga.util import atomic_write_json


import novel_manga.application.review.cast_endpoint as service_cast_endpoint


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
        name="volume_summary", max_tokens=1400, settings=service_cast_endpoint.bible_settings())
    row = {"from": first, "to": last, "chapters": len(rows), **verdict}
    path = novel_dir / "volumes.json"
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        volumes = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        volumes = [item for item in volumes if not (int(item.get("from", 0)) == first and int(item.get("to", 0)) == last)]
        volumes.append(row)
        atomic_write_json(path, sorted(volumes, key=lambda item: int(item.get("from", 0))))
    return row
