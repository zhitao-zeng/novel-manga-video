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

def beat_lines_survive(blueprint: dict, shots: list[dict], errors: list) -> None:
    """Every beat's spoken line must still be spoken after packing - or the beat's hand-off died.

    The blueprint asks each beat for its source_quote, and the review's case is the exit question
    of a scene: 席勒 asks whether he must ride the armour, 托尼's answer is the cut point - and a
    plan that cites the segment while dropping the line plays the question without the answer.
    A beat's quote is not verbatim law (adaptation may compress), so a turn counts as carrying it
    when it shares at least the quote's first content run, or enough of its characters; what the
    stage merely DESCRIBES (its picture text) does not count - an audience hears lines, not
    event fields.
    """
    spoken = [pc_text.quote_key(turn.get("text") or "") for shot in shots
              for turn in shot.get("turns") or []
              if turn.get("delivery_mode") in {"visible_dialogue", "offscreen_dialogue", "singing"}]
    if not spoken:
        return
    for beat in blueprint.get("beats") or []:
        quote = str(beat.get("source_quote") or "")
        key = pc_text.quote_key(quote)
        if len(key) < 8:                     # too short to identify, or the beat's beat is visual
            continue
        if any(_line_carries(spoken_line, key) for spoken_line in spoken):
            continue
        errors.append(PlanningIssue(PlanningCode.BEAT_LINE_LOST,
            f"{beat.get('beat_id')}: 拍点的台词「{quote[:40]}」在打包后没有任何台词承载（可能被改成事件描述或被删）；"
            "这一拍的对白或其等义表达必须由某个阶段说出，不能只写在画面描述里", field='turns'))


def _line_carries(spoken_key: str, quote_key_text: str, *, share=0.6) -> bool:
    """Whether a spoken turn carries this beat's quote: contains its opening run, or enough of it.

    Turns may be compressed or re-cut between stages; exact containment failed those.  The opening
    run is what a paraphrase keeps (its subject and verb), and the character share catches the
    compressed rest.  Both sides arrive as quote keys (punctuation-stripped); the quote's is taken
    again here so callers may pass the raw quote.
    """
    quote_key_text = pc_text.quote_key(quote_key_text)
    if not quote_key_text:
        return True
    head = quote_key_text[: max(4, len(quote_key_text) // 3)]
    if head and head in spoken_key:
        return True
    if len(spoken_key) < len(quote_key_text) * share:
        return False
    common = sum(1 for character in quote_key_text if character in spoken_key)
    return common >= len(quote_key_text) * 0.8


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
                    "characters": list(stage.get("in_frame", []) if stage.get('scene_id') else (stage.get("in_frame") or clip.get("characters") or [])),
                    "in_frame_given": ('in_frame' in stage) if stage.get('scene_id') else bool(stage.get("in_frame")),
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
                       'authored_id', 'authored_seconds', 'authored_angle', 'props') if k in stage},
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
    if not ctx.clip_range[0] <= clip_count <= ctx.clip_range[1]:
        warnings.append(f"clip_count {clip_count} outside {ctx.clip_range[0]}-{ctx.clip_range[1]} (report only)")
    cited: dict[str, list[int]] = {}
    normalized: list[dict] = []
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

        characters, extras, actions, motion_text, location = cast_and_actions(shot, names, everyone, location_map, position, ctx, errors, warnings)
        turns_out, visible = normalize_turns(shot, characters, names, position, ctx, errors, warnings)
        end_state = end_state_and_visual_checks(shot, turns_out, position, ctx, errors, warnings)
        base = {
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
               'authored_id', 'authored_seconds', 'authored_angle', 'props') if k in shot},
            **({'in_frame': characters} if shot.get('scene_id') else {}),
        }
        if "props" in base:
            # 道具只认圣经名单：幻觉名字在这里丢（装配期还会再挡一次），空数组不落盘
            known_props = {p.name for p in getattr(bible, "props", None) or []}
            dropped = [p for p in base["props"] if p not in known_props]
            if dropped:
                warnings.append(f"{position}: props 不在圣经道具名单，已丢弃: {dropped}")
            base["props"] = [p for p in base["props"] if p in known_props]
            if not base["props"]:
                base.pop("props")
        framed, notes = visible_speaker_shots(base, turns_out, visible, position,
                                              split=not ctx.authored_storyboard)
        normalized.extend(framed)
        warnings.extend(notes)

    validate_duration(normalized, ctx, errors, warnings)
    if ctx.story_blueprint and ctx.story_blueprint.get('version') != 'scene-screenplay-v2':
        owners = {b['beat_id']: b['segment_id'] for b in ctx.story_blueprint['beats']}
        for shot in normalized:
            bid = shot.get('beat_id')
            if bid in owners and owners[bid] != shot['segment_id']:
                errors.append(PlanningIssue(PlanningCode.METHOD_CONTRACT,
                    f"{bid} source ownership changed during quote normalization; correct its source_quote"))
        beat_lines_survive(ctx.story_blueprint, normalized, errors)
    chapter_coverage(raw, normalized, segments, cited, chapter_text, ctx, errors, warnings,
                     known_speakers=[*names, *ctx.aliases])
    return ValidationResult(errors, warnings, normalized)


def strict_plan_issues(shots: list[dict], chapter_text: str, segments: list[dict], raw: dict, *, ctx: PlannerContext) -> list[PlanningIssue]:
    """Opt-in gates (NOVEL_PLAN_STRICT=1), kept to what changes an episode's
    length a lot: a plan far below or far above the spoken budget is redone
    once or twice.  Wording fidelity, the closing line and the share of quoted
    lines stay report-only (the user chose throughput over verbatim lines)."""
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
    for quote in report_metrics.get("missing_quoted_lines", []):
        notes.append(f"原文引号台词未出现或被删改（report only）：{quote[:40]}")
    low, high = pc_constants.SHOT_RANGE
    if not low <= report_metrics["shot_count"] <= high:
        notes.append(f"shot_count {report_metrics['shot_count']} outside {low}-{high} (report only)")
    if not ctx.spoken_range[0] <= report_metrics["spoken_chars"] <= ctx.spoken_range[1]:
        notes.append(f"spoken_chars {report_metrics['spoken_chars']} outside {ctx.spoken_range[0]}-{ctx.spoken_range[1]} (report only)")
    return notes
