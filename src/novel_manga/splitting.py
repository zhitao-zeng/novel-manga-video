from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import Episode


_CHAPTER_PATTERNS = (
    re.compile(r"(?m)^\s*(?:#{1,6}\s*)?(第[零〇一二两三四五六七八九十百千万0-9]+[章节回卷部篇](?:\s+[^\n]{0,60})?)\s*$"),
    re.compile(r"(?mi)^\s*(?:#{1,6}\s*)?((?:chapter|part)\s+[0-9ivxlcdm]+(?:\s*[:：.-]?\s*[^\n]{0,60})?)\s*$"),
)
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;…])")
_WHITESPACE = re.compile(r"\s+")


def visible_count(text: str) -> int:
    return len(_WHITESPACE.sub("", text))


@dataclass(frozen=True)
class _Heading:
    start: int
    end: int
    title: str


def _headings(text: str) -> list[_Heading]:
    candidates: list[_Heading] = []
    for pattern in _CHAPTER_PATTERNS:
        for match in pattern.finditer(text):
            candidates.append(_Heading(match.start(), match.end(), match.group(1).strip("# \t")))
    candidates.sort(key=lambda item: item.start)
    deduped: list[_Heading] = []
    for item in candidates:
        if not deduped or item.start > deduped[-1].end:
            deduped.append(item)
    return deduped


def _units(text: str) -> list[tuple[int, int, str]]:
    units: list[tuple[int, int, str]] = []
    cursor = 0
    for paragraph in re.finditer(r"[^\n]+(?:\n+|$)", text):
        raw = paragraph.group(0)
        if not raw.strip():
            continue
        local = 0
        sentence_parts = _SENTENCE_END.split(raw)
        for part in sentence_parts:
            if not part.strip():
                local += len(part)
                continue
            start = paragraph.start() + local
            end = start + len(part)
            units.append((start, end, part))
            local += len(part)
        cursor = paragraph.end()
    if not units and text.strip():
        start = len(text) - len(text.lstrip())
        units.append((start, len(text), text[start:]))
    return units


def _target_episode_count(count: int) -> int:
    if count <= 6000:
        return 1
    if count <= 10000:
        return 2
    return max(2, math.ceil(count / 6000))


def _balanced_boundaries(text: str, episode_count: int) -> list[tuple[int, int]]:
    if episode_count == 1:
        return [(0, len(text))]
    units = _units(text)
    if len(units) <= episode_count:
        return [(start, end) for start, end, _ in units]

    weights = [visible_count(unit[2]) for unit in units]
    cumulative: list[int] = []
    running = 0
    for weight in weights:
        running += weight
        cumulative.append(running)

    total = cumulative[-1]
    cuts: list[int] = []
    previous = -1
    for part in range(1, episode_count):
        ideal = total * part / episode_count
        min_idx = previous + 1
        max_idx = len(units) - (episode_count - part) - 1
        chosen = min(range(min_idx, max_idx + 1), key=lambda idx: abs(cumulative[idx] - ideal))
        cuts.append(chosen)
        previous = chosen

    ranges: list[tuple[int, int]] = []
    unit_start = 0
    for cut in cuts + [len(units) - 1]:
        start = 0 if unit_start == 0 else units[unit_start][0]
        end = len(text) if cut == len(units) - 1 else units[cut][1]
        ranges.append((start, end))
        unit_start = cut + 1
    return ranges


def split_episodes(text: str) -> tuple[list[Episode], bool]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("novel text is empty")

    headings = _headings(text)
    episodes: list[Episode] = []
    if headings:
        for index, heading in enumerate(headings, 1):
            start = 0 if index == 1 else heading.start
            end = headings[index].start if index < len(headings) else len(text)
            body = text[start:end].strip()
            episodes.append(Episode(
                index=index,
                source_title=heading.title,
                source_text=body,
                text_count=visible_count(body),
                source_start=start,
                source_end=end,
            ))
        return episodes, True

    count = visible_count(text)
    episode_count = _target_episode_count(count)
    for index, (start, end) in enumerate(_balanced_boundaries(text, episode_count), 1):
        body = text[start:end].strip()
        episodes.append(Episode(
            index=index,
            source_title=f"第{index:02d}集",
            source_text=body,
            text_count=visible_count(body),
            source_start=start,
            source_end=end,
        ))
    return episodes, False
