"""Existing quote grounding and full-chapter coverage validation."""
from __future__ import annotations
import re
from .issues import PlanningIssue, PlanningCode
from . import constants as pc_constants, text as pc_text

def source_address(shot, position, segment_keys, chapter_key, chapter_text, errors, warnings):
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
        errors.append(PlanningIssue(PlanningCode.QUOTE_TOO_SHORT, f"source_quote too short, need at least {pc_constants.QUOTE_MIN_CHARS} chars: {quote!r}", stage=position, field='source_quote'))
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
                errors.append(PlanningIssue(PlanningCode.QUOTE_NOT_SOURCE, f"source_quote 不是原文（疑似改写）：{quote[:50]!r}。"
                    + (f"最接近的原文句子是：{nearest!r}，请逐字复制这一句或它所在段落里的一段连续原文" if nearest else "请从对应区段逐字复制一段连续原文"), stage=position, field='source_quote'))
    return segment_id, quote


def chapter_coverage(raw, normalized, segments, cited, chapter_text, ctx, errors, warnings):
    skipped_raw = raw.get("skipped_segments") or []
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in skipped_raw if isinstance(item, dict)}
    for segment_id in list(skipped):
        if segment_id in cited:
            warnings.append(f"{segment_id} listed as skipped but also cited; skip ignored")
            skipped.pop(segment_id)
    if len(skipped) > ctx.max_skipped:
        errors.append(PlanningIssue(PlanningCode.SKIPPED_SEGMENTS, f"不允许跳过区段，skipped_segments 必须为空，但收到 {sorted(skipped)}：把这些区段各写进至少一个阶段（可以拉长集数）", field='skipped_segments'))
    chat_speakers = re.findall(r"^([^\n：:]{2,8})[：:]", chapter_text, re.M)
    chat_source_lines = len(chat_speakers)
    # A chat has somebody speaking more than once; a stat block (法宝名称：…
    # 法宝属性：… 法宝等级：…) has the same line shape but every label once.
    looks_like_chat = chat_source_lines >= 5 and max((chat_speakers.count(s) for s in set(chat_speakers)), default=0) >= 2
    if looks_like_chat and not any(turn["delivery_mode"] == "chat_message" for shot in normalized for turn in shot["turns"]):
        errors.append(PlanningIssue(PlanningCode.MISSING_CHAT, f"本章原文有 {chat_source_lines} 行聊天消息（形如「昵称：内容」），但没有任何 chat_message："
            "群聊和私聊必须用 chat_message 呈现（一个阶段最多八条），不得改写成画外音或角色自述", field='turns'))
    uncited = [s["segment_id"] for s in segments if s["segment_id"] not in cited and s["segment_id"] not in skipped]
    for segment_id in uncited:
        segment = next(s for s in segments if s["segment_id"] == segment_id)
        errors.append(PlanningIssue(PlanningCode.UNCITED_SEGMENT, f"{segment_id} is neither cited by any shot nor listed in skipped_segments; "
            f"it begins with: {segment['text'][:40]!r}", segment_id=segment_id))
