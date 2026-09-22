"""Existing model-field normalization, independent of framing and scheduling."""
from __future__ import annotations
import re
from .issues import PlanningIssue, PlanningCode
from . import constants as pc_constants, text as pc_text, cast as pc_cast
from novel_manga.story.actions import normalize_extras, normalize_actions, action_text

def cast_and_actions(shot, names, everyone, location_map, position, ctx, errors, warnings):
    characters = list(dict.fromkeys(pc_cast.canonical(name, ctx=ctx) for name in shot.get("characters", []) if pc_cast.canonical(name, ctx=ctx) in names))
    unknown = [str(name) for name in shot.get("characters", []) if pc_cast.canonical(name, ctx=ctx) not in names]
    if unknown:
        errors.append(PlanningIssue(PlanningCode.UNKNOWN_CHARACTERS, f"characters not in StoryBible: {unknown}", stage=position, field='characters'))
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
    if actions and not shot.get('scene_id'):
        # the event line names who does what to whom before anything else: that sentence is what the
        # renderer and the reviewer read as 主要事件
        line = action_text(actions)
        motion_text = f"{line}。{motion_text}" if motion_text and line not in motion_text else (motion_text or line)
    location = str(shot.get("location", ""))
    if location not in location_map:
        errors.append(PlanningIssue(PlanningCode.UNKNOWN_LOCATION, f"unknown location {location!r}; allowed: {list(location_map)}", stage=position, field='location'))

    return characters, extras, actions, motion_text, location


def normalize_turns(shot, characters, names, position, ctx, errors, warnings):
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
                errors.append(PlanningIssue(PlanningCode.VISIBLE_SPEAKER, f"visible speaker {speaker!r} is not a StoryBible character", stage=position, field='turns'))
        elif mode == "offscreen_dialogue":
            if not speaker:
                errors.append(PlanningIssue(PlanningCode.OFFSCREEN_SPEAKER_MISSING, f"offscreen_dialogue needs speaker_name", stage=position, field='turns'))
            elif speaker not in names and not speaker.startswith("无名") and speaker not in ctx.anonymous_speakers:
                errors.append(PlanningIssue(PlanningCode.OFFSCREEN_SPEAKER_UNKNOWN, f"offscreen speaker {speaker!r} unknown; use a StoryBible name or 无名 role", stage=position, field='turns'))
        elif mode in {"silent_action", "title_card"}:
            speaker = ""
        elif mode == "singing":
            if speaker in names and speaker not in characters:
                characters.append(speaker)
            if speaker not in names:
                errors.append(PlanningIssue(PlanningCode.SINGING_SPEAKER, f"singing 的 speaker_name 必须是 StoryBible 角色", stage=position, field='turns'))
            if len(pc_text.compact(text)) > 12 and not re.search(r"哼|唱|旋律|曲调|歌声|声音|嗓|吟", text):  # a manner description names the singing; lyrics do not
                errors.append(PlanningIssue(PlanningCode.SINGING_TEXT, f"singing 的 text 疑似歌词：{text[:20]!r}，只写演唱方式（如“轻声哼唱一段温柔的无词旋律”），不得写歌词", stage=position, field='turns'))
        elif mode == "chat_message":
            if not speaker:
                errors.append(PlanningIssue(PlanningCode.CHAT_SPEAKER, f"chat_message needs speaker_name（发消息的人）", stage=position, field='turns'))
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
            errors.append(PlanningIssue(PlanningCode.DELIVERY_MODE, f"unknown delivery_mode {mode!r}", stage=position, field='turns'))
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

    return turns_out, visible


def end_state_and_visual_checks(shot, turns_out, position, ctx, errors, warnings):
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
            if not match:
                continue
            detail = f"{field} 含{label}描述（{match.group(0)}），{pc_constants.FORBIDDEN_VISUAL_FIX[label]}"
            message = f"{position}: {detail}"
            # An error here sends the stage to the patch round, which asks the model for a rewrite.
            # For a sheet a person or an agent authored that is the one thing binding must not do:
            # 在美漫当心灵导师的日子 ch1 shot 12 went in as a knife fight over a dog and came back as a
            # corridor in a mind palace, with its shot number gone.  The author's cut is reported
            # and kept; whatever the service then makes of it is the render stage's to handle.
            # And blood is only worth reporting to a service that refuses it: the local H3 drew
            # the same shot as written, so for it the note would be noise.
            if label == "血液或伤口" and not ctx.renderer_moderates:
                continue
            if ctx.authored_storyboard or (label == "可读文字" and (ctx.fast_tier or not ctx.text_on_props_gate)):
                warnings.append("report only: " + message)  # a note, not a gate
            else:
                errors.append(PlanningIssue(PlanningCode.VISUAL_CONTENT, detail, stage=position, field=field))
    return end_state
