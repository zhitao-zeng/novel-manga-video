"""Speech-capacity constraints and scoped source adaptation, independent of models."""
import copy
import re

MAX_CJK_PER_SECOND = 6


def count(text):
    return len(re.findall(r'[\u3400-\u9fff]', str(text)))


def capacity_issue(clip):
    spoken = clip.get('spoken_text') or ''.join(t.get('text', '') for t in clip.get('lines', []))
    seconds = float(clip.get('request_seconds') or 0)
    size = count(spoken)
    if seconds and size > seconds * MAX_CJK_PER_SECOND:
        return f'speech capacity: {size} Chinese characters in {seconds:g}s; maximum {int(seconds*MAX_CJK_PER_SECOND)}. Compress the adapted dialogue or split the provisional clip; preserve speakers and essential meaning.'
    return None


def compress_turns(mode, shot, texts, clips):
    if mode != 'source' or not any(capacity_issue(c) for c in clips):
        raise ValueError('turn_texts is only available for source-authored speech exceeding its clip duration')
    old = shot.get('turns') or []
    if len(texts) != len(old) or any(not isinstance(t, str) or not t.strip() for t in texts):
        raise ValueError('turn_texts must keep each original turn, speaker and order; use nonempty complete sentences')
    if sum(count(t) for t in texts) >= sum(count(t.get('text', '')) for t in old):
        raise ValueError('turn_texts must reduce overfull dialogue, not expand or merely reword it')
    return [{**copy.deepcopy(turn), 'text': text} for turn, text in zip(old, texts)]


def expected_lines(before, old_shots, new_shots, patches):
    """Use the exact source-checked rewritten text, without changing anyone's owner."""
    lines = copy.deepcopy(before.get('lines', []))
    replacements = {}
    for patch in patches:
        index = patch['index']
        if 'turn_texts' not in patch or index not in before.get('shot_indexes', []):
            continue
        for old, new in zip(old_shots[index].get('turns', []), new_shots[index].get('turns', [])):
            key = (old.get('speaker_name'), old.get('delivery_mode'), old.get('text'))
            replacements[key] = new.get('text')
    for line in lines:
        key = (line.get('speaker_name'), line.get('delivery_mode'), line.get('text'))
        if key in replacements:
            line['text'] = replacements[key]
    return lines
