"""Which stages show a wearer's faceplate shut: asked of the model, one answer per stage, the script untouched.

A worn phase card may carry a closed-faceplate view (closed.jpeg, assets/phase_cards).  Sent to every clip it had H3
shut the faceplate on its own in 6 clips of 10, against 2 of 10 without it - once through 托尼's own lines (美漫 ch12
part one, 2026-09-25).  So it goes only to clips whose stages have the faceplate shut, closing or opening, and which
stages those are is asked here.  Nothing else changes: the script and every other clip's request stay as they were.
No answer file, an old policy, or a stage whose picture has changed since it was answered sends the view nowhere.

    fill_visor_states.py --episode-dir outputs/<book>/<episode>
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_manga.application.identity.phases import chapter_of, load_phases, phase_for
from novel_manga.llm.client import ask_json
from novel_manga.llm.config import endpoint_settings
from novel_manga.util import atomic_write_json

POLICY = "visor-states-v2-stage-index"
FILE = "visor_states.json"
STATES = ("open", "closed", "closing", "opening", "unknown")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["stages"],
          "properties": {"stages": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["index", "state"],
              "properties": {"index": {"type": "integer"}, "state": {"type": "string", "enum": list(STATES)}}}}}}


def picture_key(shot: dict) -> str:
    """The stage's opening picture, which every part of a cut stage keeps: an answer is for this picture only."""
    material = {key: shot.get(key) for key in ('visual_prompt', 'motion_prompt', 'end_state', 'wears')}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:12]


def closed_view_wearers(novel_dir: Path, chapter: int | None) -> dict[str, str]:
    """name -> the wearable, for this chapter's worn phase cards that have a closed view on disk."""
    out: dict[str, str] = {}
    for name, phases in load_phases(novel_dir).items():
        phase = phase_for({name: phases}, name, chapter)
        if phase and phase.get("wears") and (
                novel_dir / "series_assets" / "characters" / str(phase.get("asset_id")) / "closed.jpeg").is_file():
            out[name] = str(phase["wears"])
    return out


def question(name: str, prop: str, shots: list[dict]) -> str:
    rows = []
    for shot in shots:
        said = "；".join(f"{t.get('speaker_name')}{'（心声）' if t.get('inner_monologue') else '（画外）' if t.get('delivery_mode') == 'offscreen_dialogue' else ''}：{t.get('text')}"
                        for t in shot.get("turns") or [] if t.get("text"))
        rows.append(f"{shot['index']}. 开始时：{shot.get('visual_prompt', '')}；主要事件：{shot.get('motion_prompt', '')}；"
                    f"结束时：{shot.get('end_state', '')}" + (f"；台词：{said}" if said else ""))
    return (f"下面是一集动画短剧里{name}出场的分镜阶段。{name}穿着{prop}，头盔的面罩可以开合。逐个阶段判断{name}的面罩：\n"
            "open＝整个阶段打开、能看到脸；closed＝整个阶段合上、看不到脸；closing＝这一阶段里从打开变成合上；"
            "opening＝这一阶段里从合上变成打开。\n"
            "只按阶段里写到的判断：写了面罩、面具、头盔合上或打开，看得到或看不到他的脸，声音从机甲里传出，就照写的；"
            "未说明时依据前镜明确的末态；没有依据填unknown，不能自行决定合面罩。已确认的状态保持，只补指定的空项。只输出JSON。\n\n"
            + "\n".join(rows))


def fill(episode_dir: Path, *, ask=None, script=None, write=True) -> dict:
    """Ask, per wearer with a closed view, the faceplate state of each stage he is in; write FILE and return it."""
    episode_dir = Path(episode_dir)
    script = script if script is not None else json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
    wearers = closed_view_wearers(episode_dir.parent, chapter_of(episode_dir))
    ask = ask or (lambda prompt: ask_json([{"type": "text", "text": prompt}], SCHEMA, name="visor_states",
                                          max_tokens=1500, settings=endpoint_settings("local")))
    try:
        previous = json.loads((episode_dir / FILE).read_text())
    except (OSError, ValueError):
        previous = {}
    previous = previous if previous.get('policy') == POLICY else {}
    result = {"policy": POLICY, "wearers": {}, "warnings": []}
    for name, prop in wearers.items():
        shots = [{**shot, 'index': int(shot.get('index', position))}
                 for position, shot in enumerate(script.get('shots') or [], 1)
                 if name in shot.get('in_frame', shot.get('characters') or [])
                 and (shot.get('wears') or {}).get(name, prop) == prop]
        if not shots:
            continue
        old = (previous.get('wearers') or {}).get(name) or {}
        kept = {str(shot['index']): old[str(shot['index'])] for shot in shots
                if old.get(str(shot['index']), {}).get('picture') == picture_key(shot)
                and old[str(shot['index'])].get('state') in STATES
                and old[str(shot['index'])]['state'] != 'unknown'}
        needed = [shot['index'] for shot in shots if str(shot['index']) not in kept]
        answer = {}
        if needed:
            prompt = question(name, prop, shots) + f"\n只需补这些编号：{needed}；已确认：" + json.dumps(kept, ensure_ascii=False)
            answer = {int(row['index']): row['state'] for row in (ask(prompt).get('stages') or [])
                      if row.get('state') in STATES and int(row['index']) in needed}
        missing = [index for index in needed if index not in answer or answer[index] == 'unknown']
        if missing:
            result['warnings'].append(f'{name}: unresolved stages {missing}; no extra closed view attached')
        result['wearers'][name] = {str(shot['index']): kept.get(str(shot['index'])) or {
            'state': answer.get(shot['index'], 'unknown'), 'picture': picture_key(shot)} for shot in shots}
    if write:
        atomic_write_json(episode_dir / FILE, result)
    return result


def closed_for(episode_dir: Path, clip: dict, *, script=None, states=None, throughout=False) -> set[str]:
    """The wearers whose faceplate is shut, closing or opening in any stage of this clip."""
    try:
        data = states if states is not None else json.loads((Path(episode_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if data.get("policy") != POLICY:
        return set()
    try:
        script = script if script is not None else json.loads((Path(episode_dir) / 'chapter_script.json').read_text())
    except (OSError, ValueError):
        return set()
    source = {int(s.get('index', i)): s for i, s in enumerate(script.get('shots') or [], 1)}
    out = set()
    for name, stages in (data.get('wearers') or {}).items():
        observed = []
        for shot in clip.get('shots') or []:
            index = int(shot.get('index', shot.get('origin_index', 0)))
            original = source.get(index)
            entry = stages.get(str(index)) or {}
            state = entry.get('state')
            if name not in shot.get('in_frame', shot.get('characters') or []):
                continue
            if original is None or entry.get('picture') != picture_key(original):
                observed.append('unknown')
                continue
            # A caller-modified shot is not the shot classified in the source file.
            if not shot.get('split_part') and any(shot.get(k) != original.get(k) for k in ('visual_prompt', 'motion_prompt', 'end_state')):
                observed.append('unknown')
                continue
            observed.append(state)
            part, total = shot.get('split_part') or [1, 1]
            if state == 'closing' and part < total:
                continue  # this cut has not reached the authored closing event
            if not throughout and state in {'closed', 'closing', 'opening'}:
                out.add(name)
        if throughout and observed and all(state == 'closed' for state in observed):
            out.add(name)
    return out


def recorded_clip_states(episode_dir: Path, clip: dict) -> dict[str, dict[str, str]]:
    """Review consumes the same current-stage answers as packing; an old or missing answer is unknown."""
    try:
        data = json.loads((episode_dir / FILE).read_text())
        script = json.loads((episode_dir / 'chapter_script.json').read_text())
    except (OSError, ValueError):
        return {}
    if data.get('policy') != POLICY:
        return {}
    source = {int(s.get('index', i)): s for i, s in enumerate(script.get('shots') or [], 1)}
    parts = clip.get('shot_parts') or [{'index': i, 'part': [1, 1]} for i in clip.get('shot_indexes') or []]
    result = {}
    for name, stages in data.get('wearers', {}).items():
        for part in parts:
            index = int(part['index']); shot = source.get(index); entry = stages.get(str(index)) or {}
            if not shot or name not in shot.get('in_frame', shot.get('characters') or []):
                continue
            if entry.get('picture') != picture_key(shot):
                continue
            state = entry.get('state', 'unknown'); number, total = part.get('part') or [1, 1]
            if state == 'closing' and number < total:
                state = 'open'  # the packer's nonfinal pieces have not performed the closing event
            if state in STATES and state != 'unknown':
                result.setdefault(name, {})[str(index)] = state
    return result
