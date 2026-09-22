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
    "properties": {"parts": {"type": "array", "minItems": 1, "maxItems": cp.MAX_PARTS, "items": {
        "type": "object", "additionalProperties": False, "required": ["first", "last", "reason"],
        "properties": {"first": {"type": "integer", "minimum": 1}, "last": {"type": "integer", "minimum": 1},
                       "reason": {"type": "string"}}}}},
}


def _prompt(rows: list[str], count: int, windows: list[tuple[int, int]], faults: list[str]) -> str:
    listing = "\n".join(f"[{n}]（台词 {cp.dialogue_chars(p)} 字）{p[:80]}{'…' if len(p) > 80 else ''}"
                        for n, p in enumerate(rows, 1))
    per_part = cp.estimate_seconds(rows) / count
    where = "；".join(f"第 {n} 集在第 {lo}–{hi} 段之间结束" for n, (lo, hi) in enumerate(windows, 1))
    head = (f"下面是一章小说的 {len(rows)} 个段落，按顺序编号，每段标了台词字数。这一章要拆成 {count} 集短剧，"
            f"各集台词量相当（每集约 {per_part:.0f} 秒）。为了均衡，切点的范围已经算好：{where}。"
            "请在各自范围内挑故事转折的那一段作为结束。\n"
            "切点要落在故事转折的地方：换了地点、时间跳过、有人离场或进场、一段对话结束、情绪落定。"
            "一场很长的对话可以在话题转折处切开（一个问题谈完、换了话题、一方沉默之后），"
            "不要在一句话说到一半或一问一答之间切。各集台词量相当比切点漂亮更重要。"
            "各集首尾相接、不重叠不遗漏、覆盖全部段落。"
            "每集给出起止段落编号和一句切点理由。只输出 JSON。\n")
    tail = ("\n上一次的切法有这些问题，请改正：" + "；".join(faults) + "\n") if faults else ""
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
    if wanted <= 1 or len(rows) < wanted:
        parts = cp.whole(rows)
        cp.write_parts(episode_dir, parts, decided_by="one-episode", chapter=chapter)
        log(f"ch{chapter}: 台词约 {cp.estimate_seconds(rows):.0f} 秒，一集装得下，不拆")
        return parts
    settings = settings or endpoint_settings(planner_endpoint_name())
    faults: list[str] = []
    parts: list[cp.Part] = []
    candidates: list[list[cp.Part]] = []           # structurally sound cuts, best balance kept
    windows = cp.cut_windows(rows, wanted)
    for attempt in range(2):
        try:
            answer = model_client.ask_json([{"type": "text", "text": _prompt(rows, wanted, windows, faults)}], SCHEMA,
                                           name="chapter_parts", max_tokens=600, settings=settings)
        except Exception as error:                    # noqa: BLE001 - the cut has a fallback; the chapter must not
            log(f"ch{chapter}: 分集调用失败（{type(error).__name__}: {str(error)[:120]}）")
            break
        proposed = [row for row in answer.get("parts", []) if isinstance(row, dict)]
        count_here = len(proposed)
        try:
            parts = [cp.make_part(rows, n, count_here, int(row["first"]), int(row["last"]), str(row.get("reason") or ""))
                     for n, row in enumerate(proposed, 1)] if 1 <= count_here <= cp.MAX_PARTS else []
        except (KeyError, ValueError, IndexError):
            parts = []
        faults = cp.validate(parts, rows, windows) if parts else ["没有给出合法的分集"]
        if not faults:
            break
        if parts and not [f for f in faults if "相差太多" not in f]:
            candidates.append(parts)
        log(f"ch{chapter}: 第 {attempt + 1} 次分集有问题{'，再问一次' if attempt == 0 else ''}：{'；'.join(faults)[:200]}")
        parts = []
    if not parts and candidates:
        # Of the model's sound cuts, the one whose parts are most alike; but a cut whose longest part is
        # twice its shortest gives the viewer one episode of the chapter and two of nothing, and the
        # mechanical cut - even shares, still on paragraph boundaries - serves them better than that.
        best = min(candidates, key=lambda c: cp.imbalance(c))
        if cp.imbalance(best) <= cp.IMBALANCE_LIMIT:
            parts = best
            log(f"ch{chapter}: 两次分集都不均衡，取较均衡的一次（最长/最短 {cp.imbalance(best):.1f}）")
        else:
            log(f"ch{chapter}: 模型的切法最长/最短 {cp.imbalance(best):.1f}，超过 {cp.IMBALANCE_LIMIT}，改用机械等分")
    if not parts:
        parts = cp.mechanical_split(rows, wanted)
        decided_by = "mechanical"
    else:
        decided_by = f"model:{settings.model}"
    cp.write_parts(episode_dir, parts, decided_by=decided_by, chapter=chapter)
    log(f"ch{chapter}: 拆成 {len(parts)} 集（{decided_by}）：" + "；".join(
        f"{p.title or '整章'} 第{p.first}–{p.last}段 台词{p.dialogue_chars}字≈{p.est_seconds:.0f}秒" for p in parts))
    return parts
