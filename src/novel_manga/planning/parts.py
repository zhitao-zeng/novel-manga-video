"""A chapter cut into the episodes it needs, at the paragraphs where the story turns.

A chapter of 在美漫当心灵导师的日子 carries a median 1,138 characters of dialogue.  H3 speaks 4.3 of
them a second and an episode spends about 72% of its length speaking, so a chapter's dialogue alone
is some 260 seconds of screen - and an episode is 105.  Both planners kept about 28% of the lines,
not because either was careless but because that is what fits; the viewer was left guessing what
the scene was about.

Cutting an episode is not the same as cutting the coverage segments.  split_segments makes eight
equal-sized pieces so that "every eighth of the chapter is cited" is a fair check; scenes straddle
those boundaries as often as not.  An episode cut has to land where the story turns - a place
changes, time jumps, someone leaves - and how many episodes a chapter needs follows from how much it
says.  So the cut is chosen on paragraphs, by a model or by hand, and the segments are then made
afresh inside each part, so that everything downstream of a part is exactly what it is for a chapter.

This module is the arithmetic and the record: what a part is, how many a chapter wants, whether a
proposed cut is sound, and the mechanical cut to fall back on.  Asking a model is
application.planning.parts's job.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from novel_manga.planning.text import compact

PARTS_FILE = "parts.json"
QUOTED = re.compile(r"[“「]([^”」]{2,})[”」]")
# Measured on ch12 (2026-09-22): 4.3 characters a second while speaking, 72% of the episode speaking.
SPEECH_CHARS_PER_SECOND = 4.3 * 0.72
TARGET_SECONDS = 105.0
BALANCE_LOW, BALANCE_HIGH = 0.6, 1.5      # a part within this of the mean share is balanced enough
IMBALANCE_LIMIT = 2.0                     # longest part / shortest part beyond which the cut is not worth keeping
WINDOW = 0.12                             # a cut may sit this far, in dialogue share, from the even point
MAX_PARTS = 3
TITLES = {1: ("",), 2: ("上", "下"), 3: ("上", "中", "下")}


@dataclass(frozen=True)
class Part:
    part: int                 # 1-based
    title: str                # 上 / 中 / 下, or "" for a chapter that is one episode
    first: int                # first paragraph, 1-based inclusive
    last: int                 # last paragraph, 1-based inclusive
    dialogue_chars: int
    est_seconds: float
    reason: str = ""

    @property
    def label(self) -> str:
        return f"（{self.title}）" if self.title else ""


def paragraphs(text: str, title: str = "") -> list[str]:
    """The chapter's paragraphs, the same way split_segments sees them: blank lines dropped, a leading
    title line dropped, so a paragraph number here is a paragraph number there."""
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    if rows and title and compact(rows[0]) == compact(title):
        rows = rows[1:]
    return rows


def dialogue_chars(paragraph: str) -> int:
    return sum(len(q) for q in QUOTED.findall(paragraph))


def estimate_seconds(rows: list[str]) -> float:
    """How long these paragraphs take to play, from their dialogue alone.

    Narration is not counted: a paragraph of narration becomes a picture whose length depends on
    what the planner makes of it, and the measured density already includes the pictures that sat
    between lines.  A chapter with little dialogue and much narration therefore estimates short.
    """
    return round(sum(dialogue_chars(p) for p in rows) / SPEECH_CHARS_PER_SECOND, 1)


def suggest_count(rows: list[str]) -> int:
    """How many episodes this chapter wants: enough that each is about TARGET_SECONDS, at most three."""
    return max(1, min(MAX_PARTS, math.ceil(estimate_seconds(rows) / TARGET_SECONDS)))


def make_part(rows: list[str], number: int, count: int, first: int, last: int, reason: str = "") -> Part:
    body = rows[first - 1:last]
    return Part(number, TITLES[count][number - 1], first, last,
                sum(dialogue_chars(p) for p in body), estimate_seconds(body), reason)


def whole(rows: list[str]) -> list[Part]:
    return [make_part(rows, 1, 1, 1, len(rows), "整章一集")]


def mechanical_split(rows: list[str], count: int) -> list[Part]:
    """Equal shares of dialogue, cut at paragraph boundaries: what to do when nobody chose better."""
    if count <= 1 or len(rows) < count:
        return whole(rows)
    # A paragraph with no dialogue still weighs one, so a stretch of narration is not zero screen.
    weights = [max(1, dialogue_chars(p)) for p in rows]
    total = sum(weights)
    parts, first, acc = [], 1, 0
    for index, weight in enumerate(weights, 1):
        acc += weight
        number, left = len(parts) + 1, count - len(parts)
        # close this part once it holds its share, as long as every part still to come gets a paragraph
        if left > 1 and acc >= total * number / count and len(rows) - index >= left - 1:
            parts.append(make_part(rows, number, count, first, index, "按台词字数等分"))
            first = index + 1
    parts.append(make_part(rows, len(parts) + 1, count, first, len(rows), "按台词字数等分"))
    return parts


def validate(parts: list[Part], rows: list[str], windows: list[tuple[int, int]] | None = None) -> list[str]:
    """What is wrong with a proposed cut, in words a person or a model can act on."""
    problems = []
    if not parts:
        return ["没有分出任何一集"]
    if len(parts) > MAX_PARTS:
        problems.append(f"分了 {len(parts)} 集，最多 {MAX_PARTS} 集")
    if [p.part for p in parts] != list(range(1, len(parts) + 1)):
        problems.append("集的编号必须是 1、2、3 依次")
    expected = 1
    for p in parts:
        if p.first != expected:
            problems.append(f"第 {p.part} 集从第 {p.first} 段开始，但上一集到第 {expected - 1} 段：各集必须首尾相接、不重叠不遗漏")
        if p.last < p.first:
            problems.append(f"第 {p.part} 集的结束段 {p.last} 在开始段 {p.first} 之前")
        expected = p.last + 1
    if parts and parts[-1].last != len(rows):
        problems.append(f"最后一集到第 {parts[-1].last} 段，但这一章有 {len(rows)} 段")
    for p, (lo, hi) in zip(parts, windows or []):
        if not lo <= p.last <= hi:
            problems.append(f"第 {p.part} 集必须在第 {lo}–{hi} 段之间结束，现在结束在第 {p.last} 段")
    # Balance, not absolute length: a chapter with twice the median dialogue is three long episodes
    # however it is cut, and the cut is still right when the three are alike.  Chapter 12 came back
    # 129 / 326 / 202 seconds - the middle a whole laboratory conversation - which is a cut to redo.
    if len(parts) > 1 and problems == []:
        mean = sum(p.est_seconds for p in parts) / len(parts)
        for p in parts:
            if not BALANCE_LOW * mean <= p.est_seconds <= BALANCE_HIGH * mean:
                problems.append(f"第 {p.part} 集按台词估约 {p.est_seconds:.0f} 秒，各集平均 {mean:.0f} 秒，相差太多；"
                                + ("把这一集的切点往前挪，分一些给相邻的集" if p.est_seconds > mean else "把切点往后挪，从相邻的集匀一些过来"))
    return problems


def cut_windows(rows: list[str], count: int, width: float = WINDOW) -> list[tuple[int, int]]:
    """For each cut but the last, the paragraphs a part may end on: those where the dialogue so far
    is within `width` of the even share.  The story's turn is chosen inside the window; the window
    keeps the parts alike.  Chapter 12's mechanical cut fell between the sixth and seventh of seven
    questions, and its model cut kept a whole five-minute conversation in one part: the window is
    what lets the model find "斯塔克沉默了" instead of either.
    """
    weights = [max(1, dialogue_chars(p)) for p in rows]
    total = sum(weights) or 1
    cumulative, acc = [], 0
    for w in weights:
        acc += w
        cumulative.append(acc / total)
    windows = []
    for number in range(1, count):
        ideal = number / count
        inside = [i + 1 for i, share in enumerate(cumulative) if abs(share - ideal) <= width]
        if not inside:                                   # one huge paragraph: the nearest boundary
            nearest = min(range(len(cumulative)), key=lambda i: abs(cumulative[i] - ideal)) + 1
            inside = [nearest]
        # a part must keep at least one paragraph on each side
        lo, hi = max(inside[0], number), min(inside[-1], len(rows) - (count - number))
        windows.append((lo, max(lo, hi)))
    return windows


def imbalance(parts: list[Part]) -> float:
    """Longest part over shortest, by estimated seconds; 1.0 is perfectly even."""
    seconds = [max(1.0, p.est_seconds) for p in parts]
    return max(seconds) / min(seconds) if seconds else 1.0


def locate(rows: list[str], opening: str) -> int | None:
    """The 1-based paragraph that begins with (or contains) the quoted opening words, or None."""
    key = compact(opening)
    if not key:
        return None
    for index, row in enumerate(rows, 1):
        if compact(row).startswith(key):
            return index
    for index, row in enumerate(rows, 1):
        if key in compact(row):
            return index
    return None


def part_text(rows: list[str], part: Part) -> str:
    return "\n".join(rows[part.first - 1:part.last])


def read_parts(episode_dir: Path) -> list[Part]:
    path = Path(episode_dir) / PARTS_FILE
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Part(**{k: row[k] for k in Part.__dataclass_fields__ if k in row}) for row in data.get("parts", [])]


def write_parts(episode_dir: Path, parts: list[Part], *, decided_by: str, chapter: int) -> Path:
    path = Path(episode_dir) / PARTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chapter": chapter, "decided_by": decided_by, "count": len(parts),
                                "parts": [asdict(p) for p in parts]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def part_dir_name(novel_id: str, chapter: int, part: int | None) -> str:
    """meiman-daoshi_12 for a chapter that is one episode; meiman-daoshi_12-2 for its second part."""
    return f"{novel_id}_{chapter}" + (f"-{part}" if part else "")


def previous_episode_dir(novel_dir: Path, novel_id: str, chapter: int, part: int | None) -> Path:
    """Where the episode before this one lives, so an opening can pick up the last shot's scene.

    The middle of a chapter follows its own first part, not the previous chapter; the first part
    follows the previous chapter's last part when that chapter was cut, its only episode when not.
    """
    if part and part > 1:
        return Path(novel_dir) / part_dir_name(novel_id, chapter, part - 1)
    before = read_parts(Path(novel_dir) / part_dir_name(novel_id, chapter - 1, None))
    last = before[-1].part if len(before) > 1 else None
    return Path(novel_dir) / part_dir_name(novel_id, chapter - 1, last)
