"""Shared subtitle pagination and timing, unchanged from the original renderer."""
import re

PUNCTUATION = "，。！？；：、…,.!?;:"

def _hard_wrap(text: str, limit: int) -> list[str]:
    chunks = [text[index:index + limit] for index in range(0, len(text), limit)] or [""]
    if len(chunks) >= 2 and chunks[-1] and all(char in PUNCTUATION for char in chunks[-1]):
        chunks[-2] += chunks[-1]
        chunks.pop()
    return chunks


def subtitle_pages(text: str, chars_per_line: int = 18) -> list[str]:
    """Wrap subtitles into at most two lines without punctuation-only pages."""
    clean = "".join(text.split())
    clauses = re.findall(rf".+?[{re.escape(PUNCTUATION)}]+|.+$", clean) or [clean]
    lines: list[str] = []
    current = ""
    for clause in clauses:
        if current and len(current) + len(clause) <= chars_per_line:
            current += clause
            continue
        if current:
            lines.append(current)
            current = ""
        if len(clause) <= chars_per_line:
            current = clause
        else:
            wrapped = _hard_wrap(clause, chars_per_line)
            lines.extend(wrapped[:-1])
            current = wrapped[-1]
    if current or not lines:
        lines.append(current)

    pages = [r"\N".join(lines[index:index + 2]) for index in range(0, len(lines), 2)]
    if len(pages) >= 2 and all(char in PUNCTUATION + r"\N" for char in pages[-1]):
        pages[-2] += pages.pop().replace(r"\N", "")
    return pages


def timed_subtitle_pages(text: str, start: float, end: float) -> list[dict[str, float | str]]:
    pages = subtitle_pages(text)
    duration = max(0.01, end - start)
    weights = [max(1, sum(char not in PUNCTUATION + r"\N" for char in page)) for page in pages]
    total_weight = sum(weights)
    cursor = start
    events: list[dict[str, float | str]] = []
    for index, (page, weight) in enumerate(zip(pages, weights, strict=True)):
        page_end = end if index == len(pages) - 1 else cursor + duration * weight / total_weight
        events.append({"start": cursor, "end": page_end, "text": page})
        cursor = page_end
    return events
