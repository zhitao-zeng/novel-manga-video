"""planning.validation responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from .issues import PlanningIssue, PlanningCode, ValidationResult
from novel_manga.planning.context import PlannerContext
from novel_manga.models.bible import StoryBible
import re
from .source_checks import source_address, chapter_coverage
from .normalization import cast_and_actions, normalize_turns, end_state_and_visual_checks
from .budget import validate_duration
from novel_manga.story.framing import visible_speaker_shots
from novel_manga.planning.methods.blueprint import validate_application
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.metrics as pc_metrics
import novel_manga.planning.text as pc_text

def _copied_from_previous_scene(new_opening_key: str, previous_shot: dict, *, min_chars: int = 16) -> tuple[str, str] | None:
    """(field, copied text) when a new scene's opening repeats a previous-scene shot's text.

    The tell of the ch12 replan defect: the model opened the bus-stop shot with the clinic's
    whole event sentence, '开场瞬间：…' and all.  A shared long run between the new start_state
    and any previous-scene shot's motion/end/picture text is a copy, not a coincidence - writers
    do not independently produce 16 identical characters.
    """
    if not new_opening_key:
        return None
    for field in ("motion_prompt", "end_state", "visual_prompt"):
        old_text = pc_text.quote_key(str(previous_shot.get(field) or ""))
        if len(old_text) < min_chars:
            continue
        head = old_text[: max(min_chars, len(old_text) // 3)]
        if head and head in new_opening_key:
            return field, str(previous_shot.get(field))
    return None


def flatten_clips(raw: dict) -> list[dict]:
    """Turn model clips/stages into the flat shot list the gates and packer use."""
    shots: list[dict] = []
    for clip_number, clip in enumerate(raw.get("clips") or [], start=1):
        if not isinstance(clip, dict):
            continue
        raw_id = str(clip.get("clip_id") or "").strip()
        # The id is a free string in the schema; a model once dumped its whole
        # planning draft into it.  Only accept a short token, else renumber.
        clip_id = raw_id if re.fullmatch(r"[A-Za-z0-9_\-]{1,24}", raw_id) else f"clip_{clip_number:02d}"
        for stage_number, stage in enumerate(clip.get("stages") or [], start=1):
            if not isinstance(stage, dict):
                continue
            shots.append(
                {
                    "clip_hint": clip_id,
                    "label": f"{clip_id} stage {stage_number}",
                    # A stage may name its own place (诊室 → 公交站 within one clip); the header stays
                    # the default, so every existing stage keeps the location it always had.
                    "location": str(stage.get("location") or clip.get("location", "")),
                    "characters": list(stage.get("in_frame") or []) if 'in_frame' in stage else list(clip.get("characters") or []),
                    "in_frame_given": 'in_frame' in stage,
                    "actions": [a for a in (stage.get("actions") or []) if isinstance(a, dict)],
                    "extras": [str(e).strip() for e in (stage.get("extras") or []) if str(e).strip()],
                    "segment_id": stage.get("segment_id", ""),
                    "source_quote": stage.get("source_quote", ""),
                    **({"beat_id": stage["beat_id"]} if "beat_id" in stage else {}),
                    "visual_prompt": stage.get("start_state", ""),
                    "motion_prompt": stage.get("event", ""),
                    "end_state": stage.get("end_state", ""),
                    "camera": stage.get("camera", ""),
                    "light": stage.get("light", ""),
                    "avoid": clip.get("avoid", ""),
                    "sfx": stage.get("sfx", ""),
                    "shot_scale": stage.get("shot_scale", "中近景"),
                    "turns": list(stage.get("turns") or []),
                    # An authored sheet's own shot number, planned length and camera angle travel with
                    # the shot.  They used to stop here: the whitelist carried the local scene method's
                    # fields and not the sheet's, so a 12-second authored action shot arrived with no
                    # length at all and was estimated as a generic silent stage.
                    **{k: stage[k] for k in ('scene_id', 'scene_time', 'scene_transition', 'shot_id',
                       'unit_ids', 'turn_ids', 'source_refs', 'duration_seconds', 'timing_adjustment', 'purpose', 'cut',
                       'authored_id', 'authored_seconds', 'authored_angle', 'props', 'scene_objects', 'wears') if k in stage},
                }
            )
    return shots


def separation_warnings(shots: list[dict], *, ctx: PlannerContext) -> list[str]:
    """Stages that still put a forbidden pair on screen together."""
    if not ctx.separate_pairs:
        return []
    out = []
    for index, shot in enumerate(shots, start=1):
        visible = {turn.get("speaker_name") for turn in shot.get("turns") or []
                   if turn.get("delivery_mode") == "visible_dialogue"}
        text = " ".join(str(shot.get(key) or "") for key in ("start_state", "event", "end_state"))
        for a, b in ctx.separate_pairs:
            on_screen = {name for name in (a, b) if name in visible or name in text}
            if len(on_screen) == 2:
                out.append(f"阶段{index}：{a} 与 {b} 同时入画（这两个角色容易被画成同一个人）")
    return out




def stage_slots(raw: dict) -> dict[str, tuple[int, int]]:
    """label -> (clip index, stage index), numbered exactly like flatten_clips."""
    slots: dict[str, tuple[int, int]] = {}
    for clip_number, clip in enumerate(raw.get("clips") or [], start=1):
        if not isinstance(clip, dict):
            continue
        raw_id = str(clip.get("clip_id") or "").strip()
        clip_id = raw_id if re.fullmatch(r"[A-Za-z0-9_\-]{1,24}", raw_id) else f"clip_{clip_number:02d}"
        for stage_number, stage in enumerate(clip.get("stages") or [], start=1):
            if isinstance(stage, dict):
                slots[f"{clip_id} stage {stage_number}"] = (clip_number - 1, stage_number - 1)
    return slots


def validate_and_normalize(raw: dict, segments: list[dict], bible: StoryBible, location_map: dict[str, str], chapter_text: str,
                           everyone: list[str] | None = None, *, ctx: PlannerContext) -> ValidationResult:
    errors: list[PlanningIssue] = []
    if ctx.story_blueprint:
        errors.extend(PlanningIssue(PlanningCode.METHOD_CONTRACT, message)
                      for message in validate_application(raw, ctx.story_blueprint))
    warnings: list[str] = []
    names = [character.name for character in bible.characters]
    everyone = list(everyone) if everyone else names  # the whole bible: a description may name someone the slice left out
    segment_keys = {segment["segment_id"]: pc_text.quote_key(segment["text"]) for segment in segments}
    chapter_key = pc_text.quote_key(chapter_text)
    shots = flatten_clips(raw)
    if not shots:
        return ValidationResult([PlanningIssue(PlanningCode.NO_STAGES, "no clips/stages returned")], warnings, [])
    clip_count = len(raw.get("clips") or [])
    for clip in raw.get("clips") or []:
        if isinstance(clip, dict) and len(clip.get("stages") or []) > ctx.stage_range[1]:
            warnings.append(f"{clip.get('clip_id')}: {len(clip['stages'])} stages; packer will split")
    if ctx.episode_shape != "flex" and not ctx.clip_range[0] <= clip_count <= ctx.clip_range[1]:
        warnings.append(f"clip_count {clip_count} outside {ctx.clip_range[0]}-{ctx.clip_range[1]} (report only)")
    cited: dict[str, list[int]] = {}
    normalized: list[dict] = []
    normalized_labels: list[str] = []
    for position, shot in enumerate(shots, start=1):
        position = shot.get("label") or f"shot {position}"
        segment_id, quote = source_address(shot, position, segment_keys, chapter_key, chapter_text, errors, warnings)
        cited.setdefault(segment_id, []).append(position)
        if ctx.story_blueprint.get('version') == 'scene-screenplay-v2':
            for ref in shot.get('source_refs', []):
                sid, evidence = ref.get('segment_id'), pc_text.quote_key(ref.get('source_quote', ''))
                if not evidence or evidence not in segment_keys.get(sid, ''):
                    errors.append(PlanningIssue(PlanningCode.METHOD_CONTRACT, f'{position}: 场景来源引用无效'))
                elif position not in cited.setdefault(sid, []):
                    cited[sid].append(position)

        known_props = {p.name for p in getattr(bible, 'props', None) or []}
        characters, extras, actions, motion_text, location = cast_and_actions(
            shot, names, everyone, location_map, position, ctx, errors, warnings, prop_names=known_props)
        turns_out, visible = normalize_turns(shot, characters, names, position, ctx, errors, warnings)
        speech = [t for t in turns_out if t.get('text') and t.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue'}]
        if ctx.postmix and any(t.get('inner_monologue') for t in speech) and (
                not all(t.get('inner_monologue') for t in speech) or len({t['speaker_name'] for t in speech}) > 1):
            errors.append(PlanningIssue(PlanningCode.MIXED_THOUGHT,
                '本镜混有心声和其他发声；请明确拆成连续镜头，每镜只含一位角色的心声或现场对白，'
                '保留全部台词、归属、事件顺序和画面交接，不得删除inner_monologue标记绕过。', stage=position, field='turns'))
        end_state = end_state_and_visual_checks(shot, turns_out, position, ctx, errors, warnings)
        base = {
            "label": position,
            "clip_hint": shot.get("clip_hint"),
            "segment_id": segment_id,
            **({"beat_id": shot["beat_id"]} if "beat_id" in shot else {}),
            "source_quote": quote,
            "location": location,
            "characters": characters,
            "visual_prompt": str(shot.get("visual_prompt") or "").strip(),
            "motion_prompt": motion_text,
            "actions": actions,
            "extras": extras,
            "listeners": [],
            "end_state": end_state,
            "camera": str(shot.get("camera") or "").strip(),
            "light": str(shot.get("light") or "").strip(),
            "avoid": str(shot.get("avoid") or "").strip(),
            "sfx": str(shot.get("sfx") or "").strip(),
            "shot_scale": str(shot.get("shot_scale") or "中近景"),
            "origin_index": len(normalized) + 1,
            **{k: shot[k] for k in ('scene_id', 'scene_time', 'scene_transition', 'shot_id',
               'unit_ids', 'turn_ids', 'source_refs', 'duration_seconds', 'timing_adjustment', 'purpose', 'cut',
               'authored_id', 'authored_seconds', 'authored_angle', 'props', 'scene_objects', 'wears') if k in shot},
            **({'in_frame': list(characters)} if shot.get('in_frame_given') else {}),
        }
        if "props" in base:
            # Books with a prop catalogue bind cards by name. Books without one
            # still need to describe ordinary objects without casting them as extras.
            if known_props:
                dropped = [p for p in base["props"] if p not in known_props]
                if dropped:
                    warnings.append(f"{position}: props 不在圣经道具名单，作为临时物件保留: {dropped}")
                    base['scene_objects'] = list(dict.fromkeys([*(base.get('scene_objects') or []), *dropped]))
                base["props"] = [p for p in base["props"] if p in known_props]
            else:
                base['scene_objects'] = list(dict.fromkeys([*(base.get('scene_objects') or []), *base['props']]))
                base['props'] = []
            if not base["props"]:
                base.pop("props")
        for wearer, item in (base.get('wears') or {}).items():
            if wearer not in names or (known_props and item is not None and item not in known_props):
                errors.append(PlanningIssue(PlanningCode.INVALID_WEARABLE,
                    f"穿戴状态 {wearer}:{item} 不是已登记的人物和圣经穿戴物；普通衣物留在角色外观中",
                    stage=position, field='wears'))
        framed, notes = visible_speaker_shots(base, turns_out, visible, position,
                                              split=not ctx.authored_storyboard)
        normalized.extend(framed)
        normalized_labels.extend([position] * len(framed))
        warnings.extend(notes)

    for previous, current, prev_label, label in zip(normalized, normalized[1:], normalized_labels, normalized_labels[1:]):
        if (prev_label != label and previous.get('location') == current.get('location')
                and previous.get('source_quote') == current.get('source_quote')
                and previous.get('motion_prompt') == current.get('motion_prompt')
                and previous.get('end_state') == current.get('end_state')
                and previous.get('actions') and previous.get('actions') == current.get('actions')):
            errors.append(PlanningIssue(
                PlanningCode.DUPLICATED_STAGE_ACTION,
                '相邻镜头重复执行同一动作；前镜完成后，后镜应拍结果、反应或继续对话，不得再次执行动作',
                stage=label, field='motion_prompt'))

    # A new scene's first frame copied from the previous scene's text: the second pass carried the
    # last shot's event line into the new location's start_state whole (ch12's replan: the clinic
    # tableau opened the bus-stop shot).  The copy may come from ANY shot of the previous scene -
    # round three copied the scene's OPENING, not its closing - so the check holds every previous
    # scene shot's text, not just the immediately preceding one.
    by_scene: list[tuple[str, list[dict]]] = []
    for shot in normalized:
        # The default planning schema has no scene_id. A change of location is
        # still a scene boundary and must not inherit the previous tableau.
        scene = str(shot.get('scene_id') or shot.get('location') or '')
        if by_scene and by_scene[-1][0] == scene:
            by_scene[-1][1].append(shot)
        else:
            by_scene.append((scene, [shot]))
    for index, (scene_id, scene_shots) in enumerate(by_scene):
        if index == 0 or not scene_id:
            continue
        new_opening = pc_text.quote_key(str(scene_shots[0].get("visual_prompt") or ""))
        for previous_shot in by_scene[index - 1][1]:
            copied = _copied_from_previous_scene(new_opening, previous_shot)
            if copied:
                errors.append(PlanningIssue(PlanningCode.SCENE_OPENING_COPIED,
                    f"{scene_shots[0].get('label') or scene_shots[0].get('origin_index')}: 新场景第一镜的{copied[0]}复制了上一场的画面/事件文本"
                    f"（{copied[1][:40]}…）；按提纲 scene_handoffs 的开场写新场景自己的画面", field='start_state'))
                break

    validate_duration(normalized, ctx, errors, warnings)
    if ctx.story_blueprint and ctx.story_blueprint.get('version') != 'scene-screenplay-v2':
        owners = {b['beat_id']: b['segment_id'] for b in ctx.story_blueprint['beats']}
        for shot in normalized:
            bid = shot.get('beat_id')
            if bid in owners and owners[bid] != shot['segment_id']:
                errors.append(PlanningIssue(PlanningCode.METHOD_CONTRACT,
                    f"{bid} source ownership changed during quote normalization; correct its source_quote"))
    # The outline's promises are reviewed after structural validation. This
    # pure validator cannot decide whether a paraphrase reversed the meaning.
    chapter_coverage(raw, normalized, segments, cited, chapter_text, ctx, errors, warnings,
                     known_speakers=[*names, *ctx.aliases])
    return ValidationResult(errors, warnings, normalized)


def strict_plan_issues(shots: list[dict], chapter_text: str, segments: list[dict], raw: dict, *, ctx: PlannerContext) -> list[PlanningIssue]:
    """Opt-in gates (NOVEL_PLAN_STRICT=1), kept to what changes an episode's
    length a lot: a plan far below or far above the spoken budget is redone
    once or twice.  Wording fidelity, the closing line and the share of quoted
    lines stay report-only (the user chose throughput over verbatim lines)."""
    if ctx.episode_shape == "flex":
        return []
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    found = pc_metrics.metrics(shots, chapter_text, segments, skipped)
    missing = list(found.get("missing_quoted_lines", []))
    low, high = ctx.spoken_range
    errors: list[PlanningIssue] = []
    spoken_turns = [turn["text"] for shot in shots for turn in shot["turns"] if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}]
    if found["spoken_chars"] < low * 0.6:
        errors.append(PlanningIssue(PlanningCode.STRICT_SPEECH_BELOW_MINIMUM, f"发声字数 {found['spoken_chars']} 远低于下限 {low}：把下列原文台词加回对应阶段，作为可见或画外台词（可适当精简）：" + " / ".join(q[:40] for q in missing[:8])))
    elif found["spoken_chars"] > high * 1.5:
        longest = "；".join(f"「{text[:24]}…」({pc_text.spoken_chars(text)}字)" for text in sorted(spoken_turns, key=pc_text.spoken_chars, reverse=True)[:5])
        errors.append(PlanningIssue(PlanningCode.STRICT_SPEECH_ABOVE_MAXIMUM, f"发声字数 {found['spoken_chars']} 远高于上限 {high}，至少删掉 {found['spoken_chars'] - high} 字：删除或精简寒暄、铺垫和重复的台词（例如 {longest}），"
                      "或把整句改为一句动作描述；不要新增台词"))
    return errors




def soft_warnings(report_metrics: dict, *, ctx: PlannerContext) -> list[str]:
    notes = []
    low, high = pc_constants.SHOT_RANGE
    if ctx.episode_shape != "flex" and not low <= report_metrics["shot_count"] <= high:
        notes.append(f"shot_count {report_metrics['shot_count']} outside {low}-{high} (report only)")
    if ctx.episode_shape != "flex" and not ctx.spoken_range[0] <= report_metrics["spoken_chars"] <= ctx.spoken_range[1]:
        notes.append(f"spoken_chars {report_metrics['spoken_chars']} outside {ctx.spoken_range[0]}-{ctx.spoken_range[1]} (report only)")
    return notes
