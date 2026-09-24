from __future__ import annotations
import re
from ..runtime_backends import edit_distance, normalize_text

from novel_manga.media.pagination import timed_subtitle_pages

MIN_LINE_SIMILARITY = 0.35

CAPTION_LINE_CHARS = 18

CAPTION_TRAILING_PUNCT = "，、。；：,.;:"

CLAUSE_PUNCT = "，。！？；：、…,.!?;:"

DIGIT_NAMES = "零一二三四五六七八九"

MIN_ASR_SECONDS = 0.8

MIN_ASR_CHARS = 6

SECONDS_PER_CHAR_CAP = 0.45

SECONDS_PER_CHAR_FLOOR = 0.2

def subsequence_overlap(reference: str, hypothesis: str) -> int:
    """Length of the longest common subsequence: script characters heard in order.

    Insertions in the hypothesis cost nothing, so an ad-lib or a read-out stage
    direction does not count against the clip; only script text that never
    appears does.
    """
    if not reference or not hypothesis:
        return 0
    previous = [0] * (len(hypothesis) + 1)
    for r in reference:
        current = [0]
        for j, h in enumerate(hypothesis, start=1):
            current.append(previous[j - 1] + 1 if r == h else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]

def spoken_integer(digits: str) -> str:
    """Read an integer the way it is said in Chinese: 1000 → 一千, 19800 → 一万九千八百, 10500 → 一万零五百."""
    n = int(digits)
    if n == 0:
        return "零"
    units, bigs = ("", "十", "百", "千"), ("", "万", "亿", "万亿")
    groups = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            continue
        s, pending_zero = "", False
        for i in (3, 2, 1, 0):
            d = (g // 10 ** i) % 10
            if d:
                if pending_zero:
                    s += "零"
                s += DIGIT_NAMES[d] + units[i]
                pending_zero = False
            elif s:
                pending_zero = True
        if gi < len(groups) - 1 and g < 1000:
            s = "零" + s
        parts.append(s + bigs[gi])
    text = "".join(parts)
    return text[1:] if text.startswith("一十") else text

def speakable(text: str) -> str:
    """Replace Arabic numbers by their spoken form so script and ASR text compare on equal terms."""
    def read(match: re.Match) -> str:
        token = match.group(0)
        following = text[match.end():match.end() + 1]
        if "." in token:
            whole, _, fraction = token.partition(".")
            return spoken_integer(whole) + "点" + "".join(DIGIT_NAMES[int(d)] for d in fraction)
        if following == "年" or len(token) >= 7 or (token.startswith("0") and len(token) > 1):
            return "".join(DIGIT_NAMES[int(d)] for d in token)
        return spoken_integer(token)
    return re.sub(r"\d+(?:\.\d+)?", read, text)

def match_key(text: str) -> str:
    """Normalised comparison text: spoken numbers, 两 read as 二, no punctuation."""
    return normalize_text(speakable(text)).replace("两", "二")

def balanced_split(text: str, width: int = CAPTION_LINE_CHARS) -> list[str]:
    """Split one caption into two lines near the middle, at a clause boundary when one is close enough."""
    if len(text) <= width:
        return [text]
    middle = len(text) / 2
    candidates = [i for i in range(2, len(text) - 1) if text[i - 1] in CLAUSE_PUNCT and i <= width and len(text) - i <= width]
    if candidates:
        cut = min(candidates, key=lambda i: abs(i - middle))
    else:
        cut = max(1, min(len(text) - 1, round(middle)))
    return [text[:cut], text[cut:]]

def tidy_page(page: str) -> str:
    """Rebalance orphaned second lines and drop trailing commas/periods at line ends."""
    lines = [line.strip() for line in page.split(r"\N") if line.strip()]
    if len(lines) == 2 and (len(normalize_text(lines[0])) <= 2 or len(normalize_text(lines[1])) <= 2):
        lines = balanced_split(lines[0] + lines[1])
    lines = [line.rstrip(CAPTION_TRAILING_PUNCT) for line in lines]
    lines = [line for line in lines if normalize_text(line)]
    return r"\N".join(lines) if lines else page.rstrip(CAPTION_TRAILING_PUNCT)

def classify_unmatched(heard: str, seconds: float) -> str:
    """Decide what to do with speech that matches no script line.

    Only objective properties are used: how long the block is and how much text
    the recogniser produced.  Short murmurs stay silent; anything longer is shown
    as heard, ad-libs and the occasional stage direction the model read aloud
    alike.
    """
    key = normalize_text(heard)
    if not key:
        return "silent"
    if seconds < MIN_ASR_SECONDS or len(key) < MIN_ASR_CHARS:
        return "dropped_murmur"
    return "asr_text"

def script_lines(ctx, clip_id: str) -> list[str]:
    clip = next((c for c in ctx.clip_plan["clips"] if c["clip_id"] == clip_id), None)
    return [line["text"] for line in (clip or {}).get("lines", []) if normalize_text(line["text"])]

def align_chunks(lines: list[str], chunks: list[dict], threshold: float = MIN_LINE_SIMILARITY) -> list[tuple[dict, str | None, float, list[tuple[int, str]]]]:
    """Map ASR chunks onto script text, in order, over the whole remaining script.

    The script is flattened to one normalised character sequence (numbers in
    their spoken form) with a pointer back to the original text.  For every
    chunk the best span is searched from the cursor to the end of the script
    (skipping ahead costs a small penalty per skipped line), so one noisy
    chunk cannot derail the lines that follow, and the order constraint lets
    the acceptance threshold sit well below a free-text match.  A span end
    that lands within three characters of a clause boundary snaps to it, so
    captions do not start mid-word.  Returns (chunk, matched original text
    or None, score, pieces) where pieces splits the matched text per script
    line - one line is one speaker's turn, so captions never mix speakers.
    """
    flat: list[tuple[str, int, int, int]] = []  # (key char, line index, first original index, last original index)
    for line_index, line in enumerate(lines):
        for match in re.finditer(r"\d+(?:\.\d+)?|.", line, re.S):
            token = match.group(0)
            if token[0].isdigit():
                for key_char in match_key(line[match.start():match.end() + 1] if line[match.end():match.end() + 1] == "年" else token).replace("年", ""):
                    flat.append((key_char, line_index, match.start(), match.end() - 1))
            else:
                key = normalize_text(token).replace("两", "二")
                if key:
                    flat.append((key, line_index, match.start(), match.start()))
    keys = "".join(item[0] for item in flat)
    boundary_after: list[bool] = []  # True when a clause ends right after this flat position
    for position, (_, line_index, _, last) in enumerate(flat):
        following = flat[position + 1] if position + 1 < len(flat) else None
        if following is None or following[1] != line_index:
            boundary_after.append(True)
            continue
        between = lines[line_index][last + 1:following[2]]
        boundary_after.append(any(ch in CLAUSE_PUNCT for ch in between))
    line_starts: dict[int, int] = {}
    for position, (_, line_index, _, _) in enumerate(flat):
        line_starts.setdefault(line_index, position)
    cursor = 0
    results: list[tuple[dict, str | None, float, list[tuple[int, str]]]] = []

    def original_pieces(start: int, end: int) -> list[tuple[int, str]]:
        pieces: list[list[int]] = []
        for position in range(start, end):
            _, line_index, first, last = flat[position]
            if not pieces or pieces[-1][0] != line_index:
                pieces.append([line_index, first, last])
            pieces[-1][1] = min(pieces[-1][1], first)
            pieces[-1][2] = max(pieces[-1][2], last)
        out = []
        for line_index, first, last in pieces:
            line = lines[line_index]
            stop = last + 1
            while stop < len(line) and not normalize_text(line[stop]):
                stop += 1
            out.append((line_index, line[first:stop]))
        return out

    for chunk in chunks:
        hypothesis = match_key(str(chunk.get("hypothesis", "")))
        if not hypothesis or cursor >= len(keys):
            results.append((chunk, None, 0.0, []))
            continue
        current_line = flat[cursor][1]
        candidates = [(cursor, 0)] + [(pos, li - current_line) for li, pos in line_starts.items() if pos > cursor]
        best = (0.0, None, None)
        for start, skipped in candidates:
            low = max(2, int(len(hypothesis) * 0.6))
            high = min(len(keys) - start, int(len(hypothesis) * 1.5) + 2)
            for width in range(low, high + 1):
                span = keys[start:start + width]
                score = 1.0 - edit_distance(span, hypothesis) / max(len(span), len(hypothesis)) - 0.04 * skipped
                if score > best[0]:
                    best = (score, start, width)
        score, start, width = best
        if start is None or score < threshold:
            results.append((chunk, None, round(max(score, 0.0), 3), []))
            continue
        end = start + width
        for delta in (0, -1, 1, -2, 2, -3, 3):
            candidate = end + delta
            if start < candidate <= len(keys) and boundary_after[candidate - 1]:
                end = candidate
                break
        pieces = original_pieces(start, end)
        results.append((chunk, "".join(text for _, text in pieces), round(score, 3), pieces))
        cursor = end
    return results

def subtitle_events(ctx, clip_id: str, analysis: dict) -> list[dict]:
    """Time subtitles by ASR chunks but print the script wording.

    One caption per script line (so two speakers never share a caption),
    timed by character count inside the chunk, then capped and floored by
    reading speed so a caption neither lingers over the next speaker nor
    flashes by. Unmatched recognition stays in the review record; it must not
    become invented subtitles in the film. The speech gate handles that defect.
    """
    rows: list[list] = []  # [start, end, text, source]
    for row, text, score, pieces in align_chunks(script_lines(ctx, clip_id), analysis["chunks"]):
        row["match_score"] = score
        row["matched_lines"] = text or ""
        start, end = float(row["start"]), float(row["end"])
        if not text:
            heard = str(row.get("hypothesis", "")).strip()
            verdict = classify_unmatched(heard, end - start)
            row["subtitle"] = verdict
            if verdict == "asr_text":
                row['subtitle'] = 'rejected_unplanned_speech'
            continue
        row["subtitle"] = "script_span"
        weights = [max(1, len(normalize_text(piece))) for _, piece in pieces]
        total = sum(weights)
        cursor = start
        for index, ((_, piece), weight) in enumerate(zip(pieces, weights)):
            piece_end = end if index == len(pieces) - 1 else cursor + (end - start) * weight / total
            clip = next((c for c in ctx.clip_plan['clips'] if c['clip_id'] == clip_id), {})
            planned = [line for line in clip.get('lines', []) if normalize_text(line.get('text', ''))]
            line_index = pieces[index][0]
            inner = line_index < len(planned) and planned[line_index].get('inner_monologue')
            rows.append([cursor, piece_end, ('（心声）' if inner else '') + piece, "native_audio_asr"])
            cursor = piece_end
    clip_seconds = float(analysis.get("duration") or 0.0) or (rows[-1][1] if rows else 0.0)
    for index, item in enumerate(rows):
        chars = max(1, len(normalize_text(item[2])))
        next_start = rows[index + 1][0] if index + 1 < len(rows) else clip_seconds
        item[1] = min(item[1], item[0] + 0.8 + SECONDS_PER_CHAR_CAP * chars)
        wanted = item[0] + SECONDS_PER_CHAR_FLOOR * chars
        item[1] = max(item[1], min(wanted, max(item[0] + 0.3, next_start - 0.05)))
    events = []
    for start, end, text, source in rows:
        for page in timed_subtitle_pages(text, start, end):
            events.append({"unit_id": clip_id, "role": "dialogue", "start": float(page["start"]), "end": float(page["end"]), "text": tidy_page(str(page["text"])), "subtitle_source": source})
    return events
