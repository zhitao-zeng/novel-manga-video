"""Pure dialogue ownership and request binding rules."""
from __future__ import annotations
import re
import copy
from novel_manga.runtime_backends import normalize_text
from .identity import canonical_entity

POLICY = 'grounded-dialogue-binding-v1'
TERMINAL_PUNCT = "。！？…!?"
SFX_ONLY = re.compile(r"^[\u4e00-\u9fff]{1,5}声$")


def rewritten_dialogue(clip: dict, edits: list[dict]) -> dict:
    """Return changed dialogue fields together, preserving ownership and source addresses."""
    lines = copy.deepcopy(clip.get('lines', []))
    bindings = copy.deepcopy(clip.get('dialogue_bindings', []))
    for edit in edits:
        old, new = edit.get('old'), edit.get('new', '')
        if not old:
            continue
        for row in [*lines, *bindings]:
            if old in str(row.get('text', '')):
                row['text'] = str(row['text']).replace(old, new)
    if lines == clip.get('lines', []) and bindings == clip.get('dialogue_bindings', []):
        return {}
    result = {}
    if 'lines' in clip:
        result.update(lines=lines, spoken_text=''.join(str(line.get('text', '')) for line in lines))
    if 'dialogue_bindings' in clip:
        result['dialogue_bindings'] = bindings
    return result

def nonverbal_sound(turn: dict) -> str:
    """A standalone sneeze/bark is a sound event, not Chinese words to recite."""
    text = str(turn.get("text") or "").strip()
    if turn.get("delivery_mode") not in {"visible_dialogue", "offscreen_dialogue"}:
        return ""
    if turn.get("delivery_mode") == "offscreen_dialogue" and SFX_ONLY.fullmatch(text):
        return text
    bare = re.sub(r"[\W_]+", "", text)
    sound = next((sound for pattern, sound in ((r"(?:阿嚏)+", "打喷嚏"), (r"汪+", "犬吠"),
                 (r"喵[呜喵]*", "猫叫"), (r"咳+", "咳嗽"), (r"吼+", "吼叫"), (r"呵", "短促轻笑"), (r"嗝+", "打嗝"))
                  if re.fullmatch(pattern, bare)), "")
    return (str(turn.get("speaker_name") or "") + sound) if sound else ""


def merged_turns(shot: dict) -> list[dict]:
    """Rejoin pieces of one line that the planner split at a comma.

    A piece whose predecessor ended mid-sentence (comma, no terminal
    punctuation) is a continuation of the same line.  Separate crowd lines end
    with terminal punctuation and stay separate voices.  Offscreen "turns" that
    are really a sound label (e.g. 狼嚎声) become sfx instead of speech.
    """
    merged: list[dict] = []
    extra_sfx: list[str] = []
    for turn in shot["turns"]:
        text = turn["text"].strip()
        if sound := nonverbal_sound(turn):
            extra_sfx.append(sound)
            continue
        if (
            merged
            and turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
            and merged[-1]["delivery_mode"] == turn["delivery_mode"]
            and merged[-1]["speaker_name"] == turn["speaker_name"]
            and merged[-1]["text"].rstrip()[-1:] not in TERMINAL_PUNCT
        ):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + text}
        else:
            merged.append({**turn, "text": text})
    if extra_sfx:
        existing = str(shot.get("sfx") or "")
        shot["sfx"] = "，".join([*(x for x in [existing] if x), *(s for s in dict.fromkeys(extra_sfx) if s not in existing)])
    return merged


def confirmed_bindings(shots, facts, context, segments):
    if not facts:
        return {}
    if not context:
        return {}
    passage = normalize_text('\n'.join(s['text'] for s in segments))
    forms = []
    for mention in context.get('mentions', []):
        if mention.get('kind') != 'proper' or mention['entity_id'] == 'UNKNOWN':
            continue
        name = context['entities'].get(canonical_entity(context, mention['entity_id']))
        if name:
            forms.append((normalize_text(mention['form']), name))
    by_stage = {s.get('index', i): s for i, s in enumerate(shots, 1)}
    result = {}
    for row in facts:
        stage, turn = row.get('stage'), row.get('turn')
        turns = by_stage.get(stage, {}).get('turns', [])
        if not isinstance(turn, int) or not 1 <= turn <= len(turns):
            continue
        quote = normalize_text(row.get('source_quote', ''))
        phrase = normalize_text(row.get('source_speaker_phrase', ''))
        text = normalize_text(turns[turn-1].get('text', ''))
        if (not quote or quote not in passage or not phrase or phrase not in quote or not text
                or row.get('relation') == 'uncertain'
                or (text != normalize_text(row.get('adapted_text', '')) and text not in quote)):
            continue
        owners = [(len(form), name) for form, name in forms if form and form in phrase]
        if not owners:
            continue
        longest = max(length for length, _ in owners)
        names = {name for length, name in owners if length == longest}
        if names != {row.get('speaker')}:
            continue
        result[(stage, turn)] = {**row, 'identity_policy': context['policy']}
    return result

def apply_bindings(shots, bindings):
    for index, shot in enumerate(shots, 1):
        stage = shot.get('index', index)
        for turn_index, turn in enumerate(shot.get('turns', []), 1):
            fact = bindings.get((stage, turn_index))
            if fact and turn.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue'}:
                turn['speaker_name'] = fact['speaker']
                turn['source_binding'] = {'stage':stage, 'turn':turn_index, 'speaker':fact['speaker']}
                if turn['delivery_mode'] == 'visible_dialogue':
                    for field in ['characters', 'in_frame']:
                        if field in shot and fact['speaker'] not in shot[field]:
                            shot[field].append(fact['speaker'])
    return bindings

def clip_bindings(shots: list[dict]) -> list[dict]:
    return [{'stage': stage, 'source_stage': shot.get('index', shot.get('origin_index')),
             'speaker_name': turn['speaker_name'], 'delivery_mode': turn['delivery_mode'], 'text': turn['text']}
            for stage, shot in enumerate(shots, 1) for turn in merged_turns(shot)
            if turn['delivery_mode'] in {'visible_dialogue','offscreen_dialogue'}]

def indexed_pictures(clip: dict):
    return enumerate((r for r in clip.get('references', []) if r.get('role') in {'character', 'location'}), 1)


def subject_map(clip: dict) -> dict:
    return {ref['name']: picture for picture, ref in indexed_pictures(clip)
            if ref['role'] == 'character' and ref['name'] not in clip.get('crowd_roles', {})}
