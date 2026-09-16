"""Pure formatting of source and picture evidence for the existing judges."""
from __future__ import annotations

import re
from .evidence import ClipEvidence
from . import contracts as review_contracts
from novel_manga.models import Character


EVENT_LINE = re.compile(r"主要事件是(.+?)。\n")


def scripted_event(clip: dict) -> str:
    """The planner's one-line summary of what the clip shows, read back out of its request prompt."""
    match = EVENT_LINE.search(str(clip.get("prompt") or ""))
    return match.group(1) if match else ""


def story_block(clip: dict, segments: dict) -> str:
    """The passage this clip adapts and the planner's event line, for the judge to check the picture against."""
    source = "\n".join(str(segments.get(str(s), "")) for s in clip.get("segment_ids") or []).strip()[:1500]
    event = scripted_event(clip)
    return (f"\n本段原文（剧情依据）：{source or '（无）'}" + (f"\n本段剧本事件：{event}" if event else "") + "\n")


def describe(character: Character) -> str:
    return f"{character.name}：{character.gender}，{character.age}；外貌：{character.appearance}；发型：{character.hair}；服装：{character.wardrobe}；识别物：{character.signature_prop}"


def classic_prompt(clip: dict, location_time: dict, hypothesis: str, evidence: ClipEvidence) -> str:
    by_name = evidence.by_name
    cast = evidence.cast
    extras = evidence.extras
    listeners = evidence.listeners
    offscreen = evidence.offscreen
    background = evidence.background
    legend = evidence.legend
    location = clip.get("location", "")
    expected_time = location_time.get(location, "")
    lines = "；".join(f"{row.get('speaker_name') or '旁白'}：{row['text']}" for row in clip.get("lines", []))
    chats = "；".join(f"{row.get('speaker_name')}：「{row['text']}」" for row in clip.get("chat_lines", []))
    screen = evidence.chat_screen
    if str(screen.get("render", "card")) == "card":
        # The messages are a card cut in beside this clip, not something the clip
        # has to show; the clip itself must contain no readable text at all.
        chats = ""
    elif chats and screen:
        chats += f"（群名「{screen.get('group_name', '')}」、发送者昵称和界面文字也允许出现）" if screen.get("group_name") else "（发送者昵称和界面文字也允许出现）"
    text = (
        "这是一段动画短剧视频的抽帧，前面几张是本段人物的角色卡（身份依据）。\n" + "，".join(legend) + "。\n"
        f"本段设定：地点 {location}" + (f"（{expected_time}）" if expected_time else "") + f"；出场人物 {'、'.join(cast) or '无具名角色'}"
        + (f"；无参考图的配角（按描述画，他们在画面里不算多出的人）：{'、'.join(extras)}" if extras else "")
        + (f"；按分镜设计只露背影或不入镜的听者：{'、'.join(listeners)}（不在画面里不算缺席）" if listeners else "")
        + (f"；画外说话的人：{'、'.join(offscreen)}（本来就不该出现在画面里，不算缺席）" if offscreen else "")
        + "".join(f"\n- {describe(by_name[n])}" for n in cast)
        + (f"\n允许出现在远处背景、不入近景不说话的角色：{'、'.join(background)}（他们出现在背景里是正常的，不算多出）" if background else "")
        + story_block(clip, evidence.segments)
        + evidence.snapshot
        + f"\n预期台词：{lines or '无'}\n语音识别出的台词：{hypothesis or '无'}\n"
        + (f"手机屏幕上应显示的群消息（这些文字允许出现）：{chats}\n" if chats else "")
        + "回答：visible_people 帧里清晰可见的人数（最多的一帧）；identity_ok 每个具名角色是否与其角色卡一致、没有两个角色长成同一人、没有角色被画成另一个角色的服装发型、近景里没有多出的具名角色（远处模糊背景里的人不算），不一致时在 identity_issue 写清是谁、哪一帧；"
        "若给出了原著账本出场快照，以快照为准判断谁该在场、谁该做动作、谁只是声音或只被提及；快照说某人在别人的身体里，画面就该是那具身体。"
        "story_ok：对照本段原文——原文里在这一段有动作或对白的人物是否都出现在画面里？画面里的动作是否由原文说的那个人完成"
        "（例如原文是甲将物品递给乙，画面却由丙代替甲，就是动作落在错误的人物身上；出场人物名单漏了原文里的人，"
        "也按原文判）？只看原文写到的事，不苛求细节；story_kind 选最主要的一类（story_ok 为 true 时填无问题）；story_issue 用一句话写清谁缺席、或谁的动作被谁做了；"
        "location_ok 与 time_of_day_ok 是否符合地点和时间设定；text_or_watermark 画面是否出现手机屏幕聊天消息以外的文字、字幕、水印、Logo；"
        "chat_text_ok：若本段有应显示的群消息，帧里手机屏幕上的文字是否是清晰的简体中文且内容与预期一致（允许只显示部分或截断，不允许乱码、错字连篇或无关文字），没有预期消息时填 true，不一致时在 chat_text_issue 写清；visual_defects 是否有明显崩坏（多手、面部扭曲、肢体错位、人物穿模）；"
        "severity：identity 不一致、原文有动作的人物缺席或动作落在错误的人物身上、屏幕消息乱码或不符、文字水印或严重崩坏为 fail；仅地点时间存疑或轻微瑕疵为 minor；否则 pass。feedback 为一句给视频模型的修正指令（fail 时必填，指明谁应该长什么样、避免什么）。只输出JSON。"
    )
    return text


def verify_prompt(clip: dict, location_time: dict, evidence: ClipEvidence) -> str:
    by_name = evidence.by_name
    cast = evidence.cast
    extras = evidence.extras
    listeners = evidence.listeners
    offscreen = evidence.offscreen
    background = evidence.background
    legend = evidence.legend
    location = clip.get("location", ""); expected_time = location_time.get(location, "")
    lines = "；".join(f"{row.get('speaker_name') or '旁白'}：{row['text']}" for row in clip.get("lines", []))
    text = ("这是一段动画短剧视频的抽帧。你是终审：判断一个没看过角色设定卡、顺着看剧的观众，看这一段会不会觉得画面不对劲。\n" + "，".join(legend) + "。\n"
            f"本段设定：地点 {location}" + (f"（{expected_time}）" if expected_time else "") + f"；出场人物 {'、'.join(cast) or '无具名角色'}"
            + (f"；无参考图的配角（按描述画，不算多出的人）：{'、'.join(extras)}" if extras else "")
            + (f"；按分镜只露背影或不入镜的听者：{'、'.join(listeners)}（不在画面里不算缺席）" if listeners else "")
            + (f"；画外说话的人：{'、'.join(offscreen)}（本来就不在画面里，不算缺席）" if offscreen else "")
            + "".join(f"\n- {describe(by_name[n])}" for n in cast)
            + (f"\n允许在远处背景出现的角色：{'、'.join(background)}" if background else "")
            + story_block(clip, evidence.segments)
            + evidence.snapshot
            + evidence.source_contract
            + f"\n预期台词：{lines or '无'}\n" + evidence.world
            + evidence.identity + review_contracts.VERIFY_QUESTIONS)
    return text
