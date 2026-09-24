"""Pure scene resolution, applied once before model-specific compilation."""
from __future__ import annotations
from dataclasses import dataclass, field
import copy
import re
from .actions import normalize_actions
from .dialogue import confirmed_bindings, apply_bindings
from .identity import scan_mentions


@dataclass(frozen=True)
class SceneContext:
    segments: list[dict] = field(default_factory=list)
    source_available: bool = False
    bible_names: set[str] = field(default_factory=set)
    entity_forms: dict = field(default_factory=dict)
    mention_forms: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    identity: dict = field(default_factory=dict)
    types: dict = field(default_factory=dict)
    speaker_facts: list[dict] = field(default_factory=list)
    compilation: dict = field(default_factory=dict)


@dataclass
class ResolvedScene:
    script: dict
    changes: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def shots(self):
        return self.script['shots']


def resolve_identities(script, context: SceneContext):
    script = copy.deepcopy(script)
    changes = _resolve_identities(script, context) if context.source_available else {}
    return ResolvedScene(script, changes)


def _resolve_identities(script, context):
    if not context.entity_forms and not context.identity:
        return {}
    by_segment = {str(s.get('segment_id')): s.get('text', '') for s in context.segments}
    chapter = '\n'.join(by_segment.values())
    changed = {}
    for index, shot in enumerate(script.get('shots', []), 1):
        source = str(shot.get('source_quote') or '') + '\n' + by_segment.get(str(shot.get('segment_id')), '')
        listed = set(shot.get('characters', [])) | set(shot.get('in_frame') or [])
        listed.update(t.get('speaker_name', '') for t in shot.get('turns', []))
        mapping = {}
        for old in listed:
            if not old:
                continue
            if context.identity:
                target = context.aliases.get(old)
                if target and target in context.bible_names:
                    mapping[old] = target
                continue
            anchors = {old}
            alternatives = {name for name in context.entity_forms if name in context.bible_names and name != old and anchors.intersection(context.entity_forms[name])}
            if not alternatives:
                continue
            passage = source if any(a in source for a in anchors) else chapter
            mentioned = {name for _, name, _ in scan_mentions(passage, context.mention_forms)}
            candidates = {name for name in alternatives & mentioned
                          if any(anchor in form and len(form) > len(anchor) and form in passage
                                 for form in context.entity_forms[name] for anchor in anchors)}
            # A separate mention of the shorter canonical name remains ambiguous.
            if old not in mentioned and len(candidates) == 1:
                mapping[old] = next(iter(candidates))
        if not mapping:
            continue
        pattern = re.compile('|'.join(re.escape(s) for s in sorted(mapping, key=len, reverse=True)))
        for key in ('characters', 'in_frame', 'listeners'):
            if key in shot:
                shot[key] = list(dict.fromkeys(mapping.get(n, n) for n in shot.get(key) or []))
        for turn in shot.get('turns', []):
            turn['speaker_name'] = mapping.get(turn.get('speaker_name'), turn.get('speaker_name', ''))
        for action in shot.get('actions', []):
            for key in ('actor', 'target'):
                if action.get(key) in mapping:
                    action[key] = mapping[action[key]]
        for key in ('visual_prompt', 'motion_prompt', 'end_state', 'sfx'):
            if shot.get(key):
                shot[key] = pattern.sub(lambda m: mapping[m.group()], str(shot[key]))
        changed[int(shot.get('index', index))] = mapping
    return changed


def resolve_scene(script: dict | ResolvedScene, context: SceneContext) -> ResolvedScene:
    if isinstance(script, ResolvedScene):
        return copy.deepcopy(script)
    result = resolve_identities(script, context)
    shots = result.shots
    aliases, types = context.aliases, context.types
    objects = {name for name, row in types.items() if row['kind'] == 'object'}
    for shot in shots:
        for field in ['characters', 'in_frame', 'listeners']:
            if field in shot:
                shot[field] = [name for name in shot[field] if name not in objects]
    if aliases:  # a nickname in the script must resolve to the canonical card
        for shot in shots:
            shot["characters"] = list(dict.fromkeys(aliases.get(n, n) for n in shot.get("characters", [])))
            if "in_frame" in shot:
                shot["in_frame"] = list(dict.fromkeys(aliases.get(n, n) for n in (shot.get("in_frame") or [])))
            for turn in shot.get("turns", []):
                turn["speaker_name"] = aliases.get(turn.get("speaker_name", ""), turn.get("speaker_name", ""))
                if turn.get('chat_target'):
                    turn['chat_target'] = aliases.get(turn['chat_target'], turn['chat_target'])
            if 'actions' in shot:
                shot['actions'] = normalize_actions(shot['actions'], aliases=aliases, extras=shot.get('extras', []))
    bindings = confirmed_bindings(shots, context.speaker_facts, context.identity, context.segments)
    apply_bindings(shots, bindings)
    # Wearing is a state, not a one-shot action. An omitted field keeps the
    # established outfit; an explicit null removes it. Only visible bodies
    # receive the carried prop, so a spoken-about person earns no reference.
    wearing = {}
    for shot in shots:
        wearing.update(shot.get('wears') or {})
        visible = set(shot.get('characters') or []) | set(shot.get('listeners') or []) | set(shot.get('in_frame') or [])
        carried = {name: item for name, item in wearing.items() if name in visible}
        if carried:
            shot['wears'] = {**carried, **(shot.get('wears') or {})}
            props = [item for item in carried.values() if item]
            if props:
                shot['props'] = list(dict.fromkeys([*(shot.get('props') or []), *props]))
    # "同上" is only meaningful inside one prompt.  Resolve it (and blanks)
    # from the last concrete value in reading order so that the first stage of
    # every clip states its light and camera explicitly.
    last: dict[str, str] = {}
    last_scene = None
    # An object's reflection belongs to the object being on camera: a stage that shoots 席勒
    # alone inherits the CLINIC's lamp and moonlight from 同上, not the 机甲反光 of the stage
    # before it - the armour is off frame here, and its reflection dragged a solo shot into
    # describing light off something nobody can see (four-layer audit #4).
    reflection = re.compile(r"(机甲|装甲|战甲|盔甲|反光|金属光泽)")
    for shot in shots:
        if shot.get('scene_id') and shot['scene_id'] != last_scene:
            last.clear()
        last_scene = shot.get('scene_id')
        for field in ("camera", "light"):
            value = re.sub(r"\s+", "", shot.get(field, "") or "").strip("；。")
            if value and value != "同上":
                last[field] = value
            elif last.get(field):
                inherited = last[field]
                if field == "light" and reflection.search(inherited):
                    visible = set(shot.get("characters") or []) | set(shot.get("listeners") or []) | set(shot.get("in_frame") or [])
                    worn_here = {item for item in (shot.get("wears") or {}).values() if item}
                    # keep the reflection only when the reflective thing is on camera this stage:
                    # somebody wears it, or its object/prop rides along, or an action names it
                    on_camera = bool(worn_here) or bool(shot.get("props") or shot.get("scene_objects")) \
                        or any(reflection.search(str(a.get("target") or "") + str(a.get("action") or ""))
                               for a in shot.get("actions") or [])
                    if not on_camera:
                        kept = [part for part in re.split(r"[，,；;]", inherited)
                                if not reflection.search(part)]
                        inherited = "；".join(part for part in kept if part.strip()) or None
                if inherited:
                    shot[field] = inherited
    for index, shot in enumerate(shots, start=1):
        shot.setdefault("index", index)
    return result
