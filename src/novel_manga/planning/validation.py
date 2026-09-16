"""planning.validation responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.models import StoryBible
from novel_manga.story.actions import action_participants
from novel_manga.story.actions import action_text
from novel_manga.story.actions import normalize_actions
from novel_manga.story.actions import normalize_extras
import re
import novel_manga.planning.cast as pc_cast
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.metrics as pc_metrics
import novel_manga.planning.text as pc_text

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
                    "location": clip.get("location", ""),
                    "characters": list(stage.get("in_frame") or clip.get("characters") or []),
                    "in_frame_given": bool(stage.get("in_frame")),
                    "actions": [a for a in (stage.get("actions") or []) if isinstance(a, dict)],
                    "extras": [str(e).strip() for e in (stage.get("extras") or []) if str(e).strip()],
                    "segment_id": stage.get("segment_id", ""),
                    "source_quote": stage.get("source_quote", ""),
                    "visual_prompt": stage.get("start_state", ""),
                    "motion_prompt": stage.get("event", ""),
                    "end_state": stage.get("end_state", ""),
                    "camera": stage.get("camera", ""),
                    "light": stage.get("light", ""),
                    "avoid": clip.get("avoid", ""),
                    "sfx": stage.get("sfx", ""),
                    "shot_scale": stage.get("shot_scale", "中近景"),
                    "turns": list(stage.get("turns") or []),
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


def patchable_errors(errors: list[str]) -> tuple[list[str], dict[str, list[str]]] | None:
    """Split gate errors into forgotten segments and faulty stages.

    Returns None when any error needs the whole plan rewritten: nothing
    returned, the length floor, a clip-level location or cast problem.
    """
    missing_ids: list[str] = []
    faulty: dict[str, list[str]] = {}
    for error in errors:
        uncited = pc_constants.UNCITED_ERROR.match(error)
        local = pc_constants.STAGE_ERROR.match(error)
        if uncited:
            missing_ids.append(uncited.group(1))
        elif local and not pc_constants.CLIP_LEVEL_ERROR.search(error):
            faulty.setdefault(local.group(1), []).append(error[local.end():])
        else:
            return None
    return (list(dict.fromkeys(missing_ids)), faulty) if missing_ids or faulty else None


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
                           everyone: list[str] | None = None, *, ctx: PlannerContext) -> tuple[list[str], list[str], list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    names = [character.name for character in bible.characters]
    everyone = list(everyone) if everyone else names  # the whole bible: a description may name someone the slice left out
    segment_keys = {segment["segment_id"]: pc_text.quote_key(segment["text"]) for segment in segments}
    chapter_key = pc_text.quote_key(chapter_text)
    shots = flatten_clips(raw)
    if not shots:
        return ["no clips/stages returned"], warnings, []
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
        segment_id = str(shot.get("segment_id", ""))
        quote = str(shot.get("source_quote", "")).strip()
        key = pc_text.quote_key(quote)
        if len(key) > pc_constants.QUOTE_MAX_CHARS:
            # Seventeen of the trial's redo errors were quotes over the cap.  The
            # words are verbatim; only the length is wrong, so keep the head up
            # to a sentence end rather than sending the chapter back for a redo.
            quote = pc_text.trim_quote(quote)
            warnings.append(f"{position}: source_quote {len(key)} chars, cut at a sentence end to {len(pc_text.quote_key(quote))}")
            key = pc_text.quote_key(quote)
        full_line = key in {pc_text.quote_key(q) for q in pc_text.chapter_quotes(chapter_text)}
        # Length is judged on what the model wrote (punctuation included), the
        # same count it was given in the schema; the key is only for matching.
        if len(re.sub(r"[\s\u3000]+", "", quote)) < pc_constants.QUOTE_MIN_CHARS and not full_line:
            errors.append(f"{position}: source_quote too short, need at least {pc_constants.QUOTE_MIN_CHARS} chars: {quote!r}")
        else:
            found = [sid for sid, segment_key in segment_keys.items() if key in segment_key]
            if segment_id in found:
                pass
            elif found:
                warnings.append(f"{position}: source_quote belongs to {found[0]}, not {segment_id}; reassigned")
                segment_id = found[0]
            elif key in chapter_key:
                warnings.append(f"{position}: source_quote spans a segment boundary; kept {segment_id}")
            else:
                # A quote that stitches two lines of the same exchange together
                # (the narration between them dropped) is still provenance: every
                # word is verbatim, so accept it and note which segment it lands in.
                pieces = [pc_text.quote_key(piece) for piece in re.split(r"[\n\r]+", quote) if len(pc_text.quote_key(piece)) >= 4]
                if pieces and all(piece in chapter_key for piece in pieces):
                    owners = [sid for sid, segment_key in segment_keys.items() if pieces[0] in segment_key]
                    if owners and segment_id not in owners:
                        warnings.append(f"{position}: source_quote 由 {len(pieces)} 行原文拼成，归到 {owners[0]}")
                        segment_id = owners[0]
                    else:
                        warnings.append(f"{position}: source_quote 由 {len(pieces)} 行原文拼成（中间的叙述被略去）")
                else:
                    nearest = pc_text.closest_source_line(quote, chapter_text)
                    errors.append(
                        f"{position}: source_quote 不是原文（疑似改写）：{quote[:50]!r}。"
                        + (f"最接近的原文句子是：{nearest!r}，请逐字复制这一句或它所在段落里的一段连续原文" if nearest else "请从对应区段逐字复制一段连续原文")
                    )
        cited.setdefault(segment_id, []).append(position)

        characters = list(dict.fromkeys(pc_cast.canonical(name, ctx=ctx) for name in shot.get("characters", []) if pc_cast.canonical(name, ctx=ctx) in names))
        unknown = [str(name) for name in shot.get("characters", []) if pc_cast.canonical(name, ctx=ctx) not in names]
        if unknown:
            errors.append(f"{position}: characters not in StoryBible: {unknown}")
        if shot.get("in_frame_given"):
            added = []  # the stage said who is in the picture; a name in the event line (塞西娅在楼上) is not a presence
        else:
            characters, added = pc_cast.complete_characters(characters, shot, everyone, ctx=ctx)
        if added:
            warnings.append(f"{position}: characters 补上镜头描述里出现的 {added}")
        # Extra descriptions are scene-local references, not entries in the
        # portrait catalogue. Resolve them before global name aliases.
        extras = [e for e in normalize_extras(shot.get('extras')) if pc_cast.canonical(e, ctx=ctx) not in names]
        actions = normalize_actions(shot.get('actions'), aliases=ctx.aliases, extras=extras)
        for action in actions:
            for who in (action['actor'], action['target']):
                if not shot.get('in_frame_given') and who in names and who not in characters:
                    characters.append(who)
                    warnings.append(f"{position}: actions 里的 {who} 补进 characters")
        motion_text = str(shot.get("motion_prompt") or "").strip()
        if actions:
            # the event line names who does what to whom before anything else: that sentence is what the
            # renderer and the reviewer read as 主要事件
            line = action_text(actions)
            motion_text = f"{line}。{motion_text}" if motion_text and line not in motion_text else (motion_text or line)
        location = str(shot.get("location", ""))
        if location not in location_map:
            errors.append(f"{position}: unknown location {location!r}; allowed: {list(location_map)}")

        turns_out: list[dict] = []
        visible: list[str] = []
        for turn in shot.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            mode = str(turn.get("delivery_mode", ""))
            speaker = pc_cast.canonical(turn.get("speaker_name", ""), ctx=ctx)
            text = str(turn.get("text", "")).strip()
            emotion = str(turn.get("emotion", "")).strip() or "克制自然"
            if not text:
                continue
            if mode == "visible_dialogue":
                if speaker in names:
                    if speaker not in characters:
                        characters.append(speaker)
                        warnings.append(f"{position}: visible speaker {speaker} added to characters")
                    visible.append(speaker)
                elif speaker.startswith("无名") or speaker in ctx.anonymous_speakers:
                    warnings.append(f"{position}: anonymous {speaker} cannot be visible; converted to offscreen")
                    mode = "offscreen_dialogue"
                else:
                    errors.append(f"{position}: visible speaker {speaker!r} is not a StoryBible character")
            elif mode == "offscreen_dialogue":
                if not speaker:
                    errors.append(f"{position}: offscreen_dialogue needs speaker_name")
                elif speaker not in names and not speaker.startswith("无名") and speaker not in ctx.anonymous_speakers:
                    errors.append(f"{position}: offscreen speaker {speaker!r} unknown; use a StoryBible name or 无名 role")
            elif mode in {"silent_action", "title_card"}:
                speaker = ""
            elif mode == "singing":
                if speaker in names and speaker not in characters:
                    characters.append(speaker)
                if speaker not in names:
                    errors.append(f"{position}: singing 的 speaker_name 必须是 StoryBible 角色")
                if len(pc_text.compact(text)) > 12 and not re.search(r"哼|唱|旋律|曲调|歌声|声音|嗓|吟", text):  # a manner description names the singing; lyrics do not
                    errors.append(f"{position}: singing 的 text 疑似歌词：{text[:20]!r}，只写演唱方式（如“轻声哼唱一段温柔的无词旋律”），不得写歌词")
            elif mode == "chat_message":
                if not speaker:
                    errors.append(f"{position}: chat_message needs speaker_name（发消息的人）")
                target = str(turn.get("chat_target") or "").strip()
                if target and target not in names:
                    warnings.append(f"{position}: chat_target {target!r} 不在 StoryBible，按群聊处理")
                    turn["chat_target"] = ""
                elif target and ctx.chat_self and speaker == ctx.chat_self and target != ctx.chat_self:
                    pass  # the protagonist writing into a private chat: normal
                elif target and ctx.chat_self and target == ctx.chat_self:
                    # A private chat is titled with the other party, never with the protagonist.
                    warnings.append(f"{position}: chat_target 写成了主角 {target!r}，按群聊处理")
                    turn["chat_target"] = ""

                if len(pc_text.compact(text)) > pc_constants.CHAT_MAX_CHARS:
                    # A long message is cut, not rejected: the bubble just shows its first clause.
                    cut = text[:pc_constants.CHAT_MAX_CHARS]
                    boundary = max(cut.rfind(mark) for mark in "，。！？；：、,.!?;:")
                    if boundary >= pc_constants.CHAT_MAX_CHARS // 2:
                        cut = cut[:boundary + 1]
                    cut = cut.rstrip("，,、；;：:") + "…"
                    warnings.append(f"{position}: chat_message {len(pc_text.compact(text))} 字，截为 {cut!r}")
                    text = cut
            else:
                errors.append(f"{position}: unknown delivery_mode {mode!r}")
                continue
            pieces = pc_text.split_turn_text(text) if mode in {"visible_dialogue", "offscreen_dialogue"} else [text]
            if len(pieces) > 1:
                warnings.append(f"{position}: turn of {pc_text.spoken_chars(text)} chars split into {len(pieces)}")
            for piece in pieces:
                turns_out.append({"speaker_name": speaker, "delivery_mode": mode, "chat_target": str(turn.get("chat_target") or "").strip(), "text": piece, "emotion": emotion})
        if not turns_out:
            fallback = str(shot.get("motion_prompt") or shot.get("end_state") or "无声反应").strip()
            turns_out = [{"speaker_name": "", "delivery_mode": "silent_action", "text": fallback[:60], "emotion": "克制自然"}]
            warnings.append(f"{position}: no usable turns; added silent_action")

        end_state = str(shot.get("end_state") or "").strip()
        if not end_state:
            end_state = str(shot.get("motion_prompt") or "").strip()[:80]
            warnings.append(f"{position}: end_state missing; derived from motion_prompt")
        has_chat = any(t.get("delivery_mode") == "chat_message" for t in turns_out)
        for field in ("visual_prompt", "motion_prompt", "end_state", "camera", "light"):
            value = str(shot.get(field) or "")
            for pattern, label in pc_constants.FORBIDDEN_VISUAL:
                if label == "可读文字" and has_chat:
                    continue  # the phone screen is supposed to show the messages
                match = pattern.search(value)
                if match:
                    message = (f"{position}: {field} 含{label}描述（{match.group(0)}），图片和视频都不允许；"
                               "去掉血迹和伤口，碑上的结果改写为无字的发光纹路")
                    if label == "可读文字" and (ctx.fast_tier or not ctx.text_on_props_gate):
                        warnings.append("report only: " + message)  # fast tier or genre policy: a note, not a gate
                    else:
                        errors.append(message)
        base = {
            "clip_hint": shot.get("clip_hint"),
            "segment_id": segment_id,
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
        }
        def framed(shot_base: dict, speaker: str) -> dict:
            """One visible speaker: only the speaker and the people the stage's actions involve stay in frame; the
            rest are listeners (back to camera or off frame).  Two faces in one frame is where the renderer animates
            the wrong mouth."""
            acting = action_participants(shot_base["actions"])
            keep = [c for c in shot_base["characters"] if c == speaker or c in acting]
            listeners = [c for c in shot_base["characters"] if c not in keep]
            if listeners:
                warnings.append(f"{position}: {speaker} 说话，{listeners} 转为听者（背影或画外）")
            return {**shot_base, "characters": keep or shot_base["characters"], "listeners": listeners if keep else []}

        distinct_visible = list(dict.fromkeys(visible))
        if len(distinct_visible) <= 1:
            normalized.append({**(framed(base, distinct_visible[0]) if distinct_visible else base), "turns": turns_out})
            continue
        warnings.append(f"{position}: {len(distinct_visible)} visible speakers; split into consecutive shots")
        groups: list[list[dict]] = []
        current_speaker = None
        for turn in turns_out:
            if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"] != current_speaker:
                if groups and current_speaker is None:
                    groups[-1].append(turn)
                    current_speaker = turn["speaker_name"]
                    continue
                groups.append([turn])
                current_speaker = turn["speaker_name"]
                continue
            if not groups:
                groups.append([])
            groups[-1].append(turn)
        for group in groups:
            if group:
                speaker = next((t["speaker_name"] for t in group if t["delivery_mode"] == "visible_dialogue" and t["speaker_name"]), "")
                normalized.append({**(framed(base, speaker) if speaker else base), "turns": group})

    clip_seconds: dict[str, float] = {}
    for shot in normalized:
        clip_seconds[shot["clip_hint"]] = round(clip_seconds.get(shot["clip_hint"], 0.0) + pc_text.stage_seconds(shot["turns"], ctx=ctx), 2)
    for clip_id, seconds in clip_seconds.items():
        if seconds > ctx.max_clip_seconds + pc_constants.CLIP_SECONDS_TOLERANCE:
            # The packer cuts overlong clips to this lane's duration limit.
            warnings.append(
                f"{clip_id}: 估算 {seconds} 秒超过单段上限 {int(ctx.max_clip_seconds)} 秒，打包时会自动拆成两段（report only）"
            )
    total_seconds = round(sum(clip_seconds.values()), 2)
    if ctx.episode_seconds_min and 0 < ctx.episode_seconds_min - total_seconds <= pc_constants.EPISODE_FLOOR_TOLERANCE:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，比下限 {ctx.episode_seconds_min:g} 秒少 "
                        f"{ctx.episode_seconds_min - total_seconds:g} 秒，在 {pc_constants.EPISODE_FLOOR_TOLERANCE:g} 秒估时容差内，不重写")
    elif ctx.episode_seconds_min and total_seconds < ctx.episode_seconds_min:
        errors.append(
            f"全集估算只有 {total_seconds} 秒，低于本次要求的下限 {ctx.episode_seconds_min:g} 秒（目标约{ctx.episode_seconds_target:g}秒）；"
            "把当前章还没拍到的事件补成阶段，把叙述里的来历、规则和动机多外化成角色对白或画外议论，"
            "或给已有阶段增加有原文依据的问答，不得注水重复同一句意思"
        )
    if total_seconds > ctx.episode_seconds_max and ctx.fast_tier:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，快速档不返修，打包时按 {ctx.max_clip_seconds:g} 秒拆段")
    elif total_seconds > ctx.episode_seconds_max:
        stage_total = len(normalized)
        spoken_total = sum(pc_text.spoken_chars(t["text"]) for s in normalized for t in s["turns"] if t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"})
        scale = ctx.episode_seconds_target / total_seconds
        stage_target = max(10, round(stage_total * scale))
        errors.append(
            f"全集估算 {total_seconds} 秒，超过上限 {ctx.episode_seconds_max:g} 秒（目标约{ctx.episode_seconds_target:g}秒）。"
            f"上一稿是 {stage_total} 个阶段、发声 {spoken_total} 字；本次压到 {stage_target} 个阶段左右、"
            f"发声 {max(150, round(spoken_total * scale))} 字左右。做法是缩短台词：合并同一人的连续短句，删掉不带新信息的群众议论和感叹，"
            "去掉只有反应没有事件的无声阶段；每个阶段最多两句短台词。不得为了缩短而删掉整个区段：每个区段仍须至少被一个阶段引用，一个都不能少。不得原样重发上一稿。"
        )
    skipped_raw = raw.get("skipped_segments") or []
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in skipped_raw if isinstance(item, dict)}
    for segment_id in list(skipped):
        if segment_id in cited:
            warnings.append(f"{segment_id} listed as skipped but also cited; skip ignored")
            skipped.pop(segment_id)
    if len(skipped) > ctx.max_skipped:
        errors.append(f"不允许跳过区段，skipped_segments 必须为空，但收到 {sorted(skipped)}：把这些区段各写进至少一个阶段（可以拉长集数）")
    chat_speakers = re.findall(r"^([^\n：:]{2,8})[：:]", chapter_text, re.M)
    chat_source_lines = len(chat_speakers)
    # A chat has somebody speaking more than once; a stat block (法宝名称：…
    # 法宝属性：… 法宝等级：…) has the same line shape but every label once.
    looks_like_chat = chat_source_lines >= 5 and max((chat_speakers.count(s) for s in set(chat_speakers)), default=0) >= 2
    if looks_like_chat and not any(turn["delivery_mode"] == "chat_message" for shot in normalized for turn in shot["turns"]):
        errors.append(
            f"本章原文有 {chat_source_lines} 行聊天消息（形如「昵称：内容」），但没有任何 chat_message："
            "群聊和私聊必须用 chat_message 呈现（一个阶段最多八条），不得改写成画外音或角色自述"
        )
    uncited = [s["segment_id"] for s in segments if s["segment_id"] not in cited and s["segment_id"] not in skipped]
    for segment_id in uncited:
        segment = next(s for s in segments if s["segment_id"] == segment_id)
        errors.append(
            f"{segment_id} is neither cited by any shot nor listed in skipped_segments; "
            f"it begins with: {segment['text'][:40]!r}"
        )
    return errors, warnings, normalized


def strict_plan_errors(shots: list[dict], chapter_text: str, segments: list[dict], raw: dict, *, ctx: PlannerContext) -> list[str]:
    """Opt-in gates (NOVEL_PLAN_STRICT=1), kept to what changes an episode's
    length a lot: a plan far below or far above the spoken budget is redone
    once or twice.  Wording fidelity, the closing line and the share of quoted
    lines stay report-only (the user chose throughput over verbatim lines)."""
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    found = pc_metrics.metrics(shots, chapter_text, segments, skipped)
    missing = list(found.get("missing_quoted_lines", []))
    low, high = ctx.spoken_range
    errors: list[str] = []
    spoken_turns = [turn["text"] for shot in shots for turn in shot["turns"] if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}]
    if found["spoken_chars"] < low * 0.6:
        errors.append(f"发声字数 {found['spoken_chars']} 远低于下限 {low}：把下列原文台词加回对应阶段，作为可见或画外台词（可适当精简）：" + " / ".join(q[:40] for q in missing[:8]))
    elif found["spoken_chars"] > high * 1.5:
        longest = "；".join(f"「{text[:24]}…」({pc_text.spoken_chars(text)}字)" for text in sorted(spoken_turns, key=pc_text.spoken_chars, reverse=True)[:5])
        errors.append(f"发声字数 {found['spoken_chars']} 远高于上限 {high}，至少删掉 {found['spoken_chars'] - high} 字：删除或精简寒暄、铺垫和重复的台词（例如 {longest}），"
                      "或把整句改为一句动作描述；不要新增台词")
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
