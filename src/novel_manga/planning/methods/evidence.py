"""Literal source spans, independent of screenplay and director contracts."""
import re
from novel_manga.planning.text import quote_key, chapter_quotes


def source_refs(quote: str, segments: list[dict], anchor: str = '') -> list[dict]:
    """Map one literal chapter span to its actual segments, including boundaries."""
    key = quote_key(quote)
    if not key:
        return []
    indexed, offset = [], 0
    for segment in segments:
        positions = [m.start() for m in re.finditer(r'[A-Za-z0-9\u3400-\u9fff]', segment['text'])]
        segment_key = ''.join(segment['text'][i] for i in positions)
        indexed.append((segment, offset, positions, segment_key))
        offset += len(segment_key)
    chapter = ''.join(row[3] for row in indexed)
    start, first = chapter.find(key), []
    while start >= 0:
        end, refs = start + len(key), []
        for segment, offset, positions, segment_key in indexed:
            left, right = max(start, offset), min(end, offset + len(segment_key))
            if left >= right:
                continue
            text = segment['text'][positions[left-offset]:positions[right-offset-1]+1]
            refs.append({'segment_id': segment['segment_id'], 'source_quote': text})
        if not first:
            first = refs
        if not anchor or any(r['segment_id'] == anchor for r in refs):
            return refs
        start = chapter.find(key, start + 1)
    return first


def quote_candidates(text: str) -> list[str]:
    """Offer literal evidence from this input, instead of asking the model to retype it.

    These are source spans, not a vocabulary of allowable people or action targets.
    Long prose gets overlapping excerpts; all creative fields remain open text.
    """
    spans = [text.strip(), *(s.strip() for s in re.split(r'(?<=[。！？!?；;])|\n+', text) if s.strip())]
    quotes = []
    for span in spans:
        if len(span) <= 120:
            quotes.append(span)
        else:
            quotes.extend(span[i:i+120] for i in range(0, len(span), 100))
            quotes.append(span[-120:])
    short_lines = {quote_key(q) for q in chapter_quotes(text)}
    usable = [q for q in quotes if q and (len(re.sub(r'\s+', '', q)) >= 8 or quote_key(q) in short_lines)]
    return list(dict.fromkeys(usable or [text.strip()]))
