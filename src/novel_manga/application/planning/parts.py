"""Decide where a chapter's episodes begin and end, and write it down beside the chapter.

The arithmetic - how many episodes, how long each runs, whether a cut is sound - is
novel_manga.planning.parts.  This asks a model to place the cuts where the story turns, checks its
answer with that arithmetic, asks once more with the faults named, and falls back to an equal share
of the dialogue when nothing better came.  Whoever plans the parts afterwards, the local planner or
the sandbox agent, reads the same parts.json; the decision is made once and does not depend on who
asks.
"""
from __future__ import annotations

import json
from pathlib import Path

from novel_manga.planning import parts as cp
from novel_manga.llm import client as model_client
from novel_manga.llm.config import endpoint_settings, planner_endpoint_name

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["parts"],
    "properties": {"parts": {"type": "array", "minItems": 1, "items": {
        "type": "object", "additionalProperties": False, "required": ["first", "last", "reason"],
        "properties": {"first": {"type": "integer", "minimum": 1}, "last": {"type": "integer", "minimum": 1},
                       "reason": {"type": "string"}}}}},
}


def _prompt(rows: list[str], count: int, windows: list[tuple[int, int]], faults: list[str], previous=None) -> str:
    listing = "\n".join(f"[{n}]（台词 {cp.dialogue_chars(p)} 字）{p}"
                        for n, p in enumerate(rows, 1))
    per_part = cp.estimate_seconds(rows) / count
    where = "；".join(f"第 {n} 集在第 {lo}–{hi} 段之间结束" for n, (lo, hi) in enumerate(windows, 1))
    head = (f"下面是一章小说的 {len(rows)} 个段落，按顺序编号，每段标了台词字数。这一章估计可拆成 {count} 集短剧，可按剧情与时长调整集数，"
            f"每集原对白预估约 {per_part:.0f} 秒。均分参考区间：{where}。"
            "区间仅供参考，可以越过，优先在故事转折处结束。\n"
            "切点要落在故事转折的地方：换了地点、时间跳过、有人离场或进场、一段对话结束、情绪落定。"
            "一场很长的对话可以在话题转折处切开（一个问题谈完、换了话题、一方沉默之后），"
            "不要在一句话说到一半或一问一答之间切。每集应有能看懂的进入原因、人物回应和阶段结果；不要切断必要的问答与因果。"
            "目标每集60–120秒。按本次保留对白估算时长=本集台词字数之和/3.096，每集不得超过120秒，超过就继续拆；结尾收束可以短于60秒。不必强迫每集一样长。后续编剧可以压缩重复解释，但本次切点不能假定台词已删，也不能删关键事实、人物意图与因果。各集首尾相接、不重叠不遗漏、覆盖全部段落。"
            "每集给出起止段落编号，reason说明本集核心问题、阶段结果及下一集如何承接。只输出 JSON。\n")
    tail = ("\n上一次的切法有这些问题，请改正：" + "；".join(faults) + "\n") if faults else ""
    if faults and previous:
        tail += "上次实际方案：" + json.dumps(previous, ensure_ascii=False) + "\n只修指出的超时或交接问题，保留其余有效切点；增加集数后不要再把别处已分开的集拼成长集。\n"
    return head + tail + "\n" + listing


def decide_parts(novel_dir: Path, chapter: int, text: str, title: str = "", *, count: int | None = None,
                 settings=None, log=print) -> list[cp.Part]:
    """The chapter's parts, decided once and written to <chapter dir>/parts.json.

    An existing decision is kept: the sandbox may already have written storyboards against it, and a
    different cut would leave them describing episodes that no longer exist.
    """
    episode_dir = Path(novel_dir) / cp.part_dir_name(Path(novel_dir).name, chapter, None)
    existing = cp.read_parts(episode_dir)
    if existing:
        return existing
    rows = cp.paragraphs(text, title)
    wanted = count or cp.suggest_count(rows)
    wanted = min(wanted, len(rows)) if rows else 1
    if wanted <= 1:
        parts = cp.whole(rows)
        remaining = cp.validate(parts, rows, max_seconds=120)
        if remaining:
            raise ValueError("单段原文仍超时，需要先明确细分或改编，未保存分集：" + "；".join(remaining))
        cp.write_parts(episode_dir, parts, decided_by="one-episode", chapter=chapter)
        log(f"ch{chapter}: 台词约 {cp.estimate_seconds(rows):.0f} 秒，一集装得下，不拆")
        return parts
    settings = settings or endpoint_settings(planner_endpoint_name())
    faults: list[str] = []
    parts: list[cp.Part] = []
    previous = None
    windows = cp.cut_windows(rows, wanted)
    for attempt in range(2):
        try:
            answer = model_client.ask_json([{"type": "text", "text": _prompt(rows, wanted, windows, faults, previous)}], SCHEMA,
                                           name="chapter_parts", max_tokens=max(600, wanted * 220), settings=settings)
        except Exception as error:                    # noqa: BLE001 - the cut has a fallback; the chapter must not
            log(f"ch{chapter}: 分集调用失败（{type(error).__name__}: {str(error)[:120]}）")
            break
        proposed = [row for row in answer.get("parts", []) if isinstance(row, dict)]
        previous = proposed
        count_here = len(proposed)
        try:
            parts = [cp.make_part(rows, n, count_here, int(row["first"]), int(row["last"]), str(row.get("reason") or ""))
                     for n, row in enumerate(proposed, 1)] if 1 <= count_here <= len(rows) else []
        except (KeyError, ValueError, IndexError):
            parts = []
        faults = cp.validate(parts, rows, max_seconds=120) if parts else ["没有给出合法的分集"]
        if not faults:
            break
        log(f"ch{chapter}: 第 {attempt + 1} 次分集有问题{'，再问一次' if attempt == 0 else ''}：{'；'.join(faults)[:200]}")
        parts = []
    if not parts:
        parts = cp.mechanical_split(rows, wanted)
        decided_by = "mechanical"
    else:
        decided_by = f"model:{settings.model}"
    remaining = cp.validate(parts, rows, max_seconds=120)
    if remaining:
        raise ValueError("分集未通过，未保存：" + "；".join(remaining))
    cp.write_parts(episode_dir, parts, decided_by=decided_by, chapter=chapter)
    log(f"ch{chapter}: 拆成 {len(parts)} 集（{decided_by}）：" + "；".join(
        f"{p.title or '整章'} 第{p.first}–{p.last}段 台词{p.dialogue_chars}字≈{p.est_seconds:.0f}秒" for p in parts))
    return parts
