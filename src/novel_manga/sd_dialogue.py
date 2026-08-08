from __future__ import annotations

import re


PUNCTUATION = "，。！？；：、…,.!?;:"


def build_sd_prompt(
    role: str,
    text: str,
    motion_prompt: str,
    *,
    use_reference_audio: bool = False,
    actor_description: str | None = None,
    composition_prompt: str | None = None,
    emotion: str | None = None,
) -> str:
    if use_reference_audio and role == "narrator":
        audio_instruction = (
            "严格以参考音频1作为唯一画外旁白、音色、语速、停顿和情绪依据，完整复现参考音频，"
            "不得改词、漏词、重读或添加其他声音；画中人物不得开口或随旁白做口型。"
        )
    elif use_reference_audio:
        audio_instruction = (
            "严格以参考音频1作为唯一对白、音色、语速、停顿和情绪依据，完整复现参考音频，"
            "不得改词、漏词、重读或添加其他对白；口型必须与参考音频逐字同步。"
        )
    else:
        audio_instruction = "不生成声音。"
    continuity = (
        "严格保持参考图中的国漫画风、人物身份、脸型、发型、服装、场景结构、色彩与光影，"
        "不得变脸，不得新增人物，不出现文字、字幕、气泡、水印或标识，"
        f"{audio_instruction}"
    )
    if role == "narrator":
        return (
            f"{continuity}这是旁白画面，画面内所有人物都不说话，嘴巴自然闭合。"
            f"当前叙事内容是：{text}。画面动作：{motion_prompt}"
            "镜头运动自然克制，动作连续，禁止重复动作或倒放。"
        )
    actor = actor_description or role
    composition = composition_prompt or "固定单人正脸或四分之三近景"
    performance = f"表演情绪为{emotion}。" if emotion else ""
    if use_reference_audio:
        timing = (
            "参考音频开头静音期间嘴巴保持自然闭合，只在参考音频实际人声开始时开口；"
            "嘴唇随台词逐字自然连续开合，下颌动作克制，停顿与标点自然；"
            "参考音频人声结束后立即闭嘴，并在剩余静音和镜头尾部持续闭合至少半秒。"
        )
    else:
        timing = (
            "从镜头开始后立即说话，嘴唇随台词逐字自然连续开合，下颌动作克制，"
            "停顿与标点自然，说完后自然闭嘴。"
        )
    return (
        f"{continuity}镜头明确聚焦{actor}，构图要求：{composition}。"
        "只有这个角色开口，其他人物保持闭嘴，严格保证只有该角色开口。"
        f"{performance}"
        f"该角色正在用标准普通话、符合当前情绪地自然说出完整台词：“{text}”"
        f"{timing}禁止嘴部静止、夸张大张嘴、嘴形抖动、五官扭曲或其他人物同时说话。"
        "说话人的脸和嘴在整个镜头中始终清晰可见，直到最后半秒都不能转身、离开画面或被遮挡。"
        "全程使用同一个连续近景，不切镜、不转场、不改变景别、不摇移相机；只允许自然眨眼、轻微点头、"
        "发丝衣角微动以及符合说话节奏的微小表情变化。"
    )


def _hard_wrap(text: str, limit: int) -> list[str]:
    chunks = [text[index:index + limit] for index in range(0, len(text), limit)] or [""]
    if len(chunks) >= 2 and chunks[-1] and all(char in PUNCTUATION for char in chunks[-1]):
        chunks[-2] += chunks[-1]
        chunks.pop()
    return chunks


def subtitle_pages(text: str, chars_per_line: int = 18) -> list[str]:
    """Wrap subtitles into at most two lines without punctuation-only pages."""
    clean = "".join(text.split())
    clauses = re.findall(rf".+?[{re.escape(PUNCTUATION)}]+|.+$", clean) or [clean]
    lines: list[str] = []
    current = ""
    for clause in clauses:
        if current and len(current) + len(clause) <= chars_per_line:
            current += clause
            continue
        if current:
            lines.append(current)
            current = ""
        if len(clause) <= chars_per_line:
            current = clause
        else:
            wrapped = _hard_wrap(clause, chars_per_line)
            lines.extend(wrapped[:-1])
            current = wrapped[-1]
    if current or not lines:
        lines.append(current)

    pages = [r"\N".join(lines[index:index + 2]) for index in range(0, len(lines), 2)]
    if len(pages) >= 2 and all(char in PUNCTUATION + r"\N" for char in pages[-1]):
        pages[-2] += pages.pop().replace(r"\N", "")
    return pages


def timed_subtitle_pages(text: str, start: float, end: float) -> list[dict[str, float | str]]:
    pages = subtitle_pages(text)
    duration = max(0.01, end - start)
    weights = [max(1, sum(char not in PUNCTUATION + r"\N" for char in page)) for page in pages]
    total_weight = sum(weights)
    cursor = start
    events: list[dict[str, float | str]] = []
    for index, (page, weight) in enumerate(zip(pages, weights, strict=True)):
        page_end = end if index == len(pages) - 1 else cursor + duration * weight / total_weight
        events.append({"start": cursor, "end": page_end, "text": page})
        cursor = page_end
    return events
