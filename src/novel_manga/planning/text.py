"""planning.text responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
import difflib
import hashlib
import re
import novel_manga.planning.constants as pc_constants

def spoken_turn_seconds(text: str) -> float:
    return spoken_chars(text) / 4.0 + 1.0


def stage_seconds(turns: list[dict], *, ctx: PlannerContext) -> float:
    seconds = 1.0
    for turn in turns:
        mode = turn.get("delivery_mode")
        if mode in {"visible_dialogue", "offscreen_dialogue"}:
            seconds += spoken_turn_seconds(str(turn.get("text", "")))
        elif mode == "silent_action":
            seconds += 3.0
        elif mode == "chat_message":
            # card mode (the default): the chat card carries the message; the
            # stage itself is one reaction beat
            seconds += 1.0 if ctx.chat_card_mode else spoken_chars(str(turn.get("text", ""))) / 5.0 + 1.5
        elif mode == "singing":
            seconds += 6.0
    return max(3.0, round(seconds, 2))


def closest_source_line(quote: str, chapter_text: str) -> str:
    """The chapter sentence most similar to a rejected quote, for the repair message."""
    key = quote_key(quote)
    best, best_score = "", 0.0
    for line in re.split(r"(?<=[。！？!?；;])|\n+", chapter_text):
        line = line.strip()
        if len(quote_key(line)) < 4:
            continue
        score = difflib.SequenceMatcher(None, key, quote_key(line)).ratio()
        if score > best_score:
            best, best_score = line, score
    return best[:120]


def trim_quote(quote: str, limit: int = pc_constants.QUOTE_MAX_CHARS) -> str:
    """The longest verbatim prefix whose key fits the limit, cut back to a sentence end when one lies past the halfway point."""
    end = len(quote)
    while end > 0 and len(quote_key(quote[:end])) > limit:
        end -= 1
    head = quote[:end]
    boundary = max(head.rfind(mark) for mark in "。！？；…”」")
    if boundary >= len(head) // 2:
        head = head[:boundary + 1]
    return head.strip()


def chapter_quotes(text: str) -> list[str]:
    """Quoted lines that read like speech.  Novels also quote proper nouns and
    terms (“秘术”“心胜于物”); a quote counts as a line only when it is 8+
    characters or carries sentence punctuation."""
    quotes = re.findall(r"[“\"]([^”\"]{2,120})[”\"]", text)
    return list(dict.fromkeys(quote.strip() for quote in quotes
                              if spoken_chars(quote) >= 8 or (spoken_chars(quote) >= 2 and re.search(r"[，。！？…；、]", quote))))


def compact(value: str) -> str:
    return re.sub(r"[\s　]+", "", value or "")


def handoff_pairs(text: str) -> list[tuple[str, str]]:
    """Quoted question-and-answer pairs that carry a scene's exit into the next scene's opening.

    The ch12 defect, both routes: 席勒 asks 你该不会想让我坐这个过去吧？, 托尼 answers 不然呢？你打算
    怎么过去？ - and the next scene IS the answer (the two of them at the bus stop).  Both writers
    dropped the pair and cut to the bus with the question unspoken; the audience never learns the
    trip was a joke between the two of them.

    The tell is the scene-change marker right after the answer: 几分钟后/出现在/来到了 - prose that
    jumps place or time, which is what a cut renders.  A question answered and then followed by
    ordinary continuous prose is dialogue inside a scene (the 实验室 interrogation chain is ten
    pairs of those); it stays where it is, and only the pair at the door goes with the hand-off.
    """
    pairs: list[tuple[str, str]] = []
    quotes = [(m.start(), m.end(), m.group(1).strip()) for m in re.finditer(r"[“\"]([^”\"]{2,120})[”\"]", text)]
    for (pos, end, line), (next_pos, next_end, next_line) in zip(quotes, quotes[1:]):
        if not line.endswith("？"):
            continue
        if next_pos - end > 60:            # a question and its answer sit together in prose
            continue
        if spoken_chars(line) < 4 or spoken_chars(next_line) < 2:
            continue
        after = text[next_end: next_end + 40]
        if re.search(r"(几分钟|出现在|来到了|来到|赶到|来到|抵达|时已是|已是|当晚|次日|翌日|转眼)", after):
            pairs.append((line, next_line))
    return pairs


def quote_key(value: str) -> str:
    """Provenance key: letters, digits and CJK only.

    The gate exists to prove the words come from the chapter.  Quotation
    marks, a colon turned into a full stop, or a dropped ellipsis are not
    evidence of invention, so all punctuation and whitespace are ignored.
    """
    return "".join(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", value or ""))


def spoken_chars(value: str) -> int:
    return len(re.sub(pc_constants.STRIP_PUNCT, "", value or ""))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_segments(text: str, title: str, count: int) -> list[dict]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if paragraphs and compact(paragraphs[0]) == compact(title):
        paragraphs = paragraphs[1:]
    total = sum(len(compact(p)) for p in paragraphs)
    segments: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        current.append(paragraph)
        current_len += len(compact(paragraph))
        remaining = count - len(segments)
        remaining_chars = total - sum(len(compact(p)) for group in segments for p in group) - current_len
        if remaining > 1 and current_len >= (current_len + remaining_chars) / remaining:
            segments.append(current)
            current, current_len = [], 0
    if current:
        segments.append(current)
    return [
        {"segment_id": f"seg_{index}", "text": "\n".join(group), "chars": sum(len(compact(p)) for p in group)}
        for index, group in enumerate(segments, start=1)
    ]


def split_turn_text(text: str) -> list[str]:
    text = text.strip()
    if spoken_chars(text) <= pc_constants.TURN_MAX_CHARS:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if spoken_chars(remaining) <= pc_constants.TURN_MAX_CHARS:
            chunks.append(remaining)
            break
        cut = None
        counted = 0
        for position, character in enumerate(remaining):
            if not re.match(pc_constants.STRIP_PUNCT, character):
                counted += 1
            if counted > pc_constants.TURN_MAX_CHARS:
                break
            if character in pc_constants.SPLIT_PUNCT and counted >= 4:
                cut = position + 1
        if cut is None:
            counted = 0
            for position, character in enumerate(remaining):
                if not re.match(pc_constants.STRIP_PUNCT, character):
                    counted += 1
                if counted >= pc_constants.TURN_MAX_CHARS:
                    cut = position + 1
                    break
            cut = cut or len(remaining)
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    merged: list[str] = []
    for chunk in chunks:
        if merged and spoken_chars(chunk) == 0:
            merged[-1] += chunk
        elif chunk:
            merged.append(chunk)
    return [chunk for chunk in merged if spoken_chars(chunk) > 0]
