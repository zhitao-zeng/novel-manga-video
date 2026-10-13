"""Pure formatting of source and picture evidence for the existing judges."""
from __future__ import annotations

import re
import json
from .evidence import ClipEvidence
from . import contracts as review_contracts
from novel_manga.models.bible import Character


EVENT_LINE = re.compile(r"主要事件是(.+?)。\n")


def shot_contract(clip: dict) -> str:
    """The actual shot, including its crop and state changes, rather than the cast card's default pose."""
    description = ("\n本镜实际英文请求（人物编号与引用关系也在其中）：\n" + str(clip['prompt_h3'])
                   if clip.get('prompt_h3') else
                   "\n本镜中文拍摄计划：\n" + str(clip.get('prompt') or '（未提供）'))
    return (description
            + "\n编号说明：英文请求里的 <Picture n> 是生成时的素材编号，不是本次审查图片序号。"
              "请按本次图例的姓名/Subject 对应角色卡；角色卡拼图中的左右小图各代表独立人物，视频帧不作角色卡。"
            + "\n判定边界：角色卡提供身份、服装材质和配色，不规定每一镜的姿势、站位、面罩开合或可见部位。"
              "这些状态优先按实际最终英文请求及其动作先后判断，英文未明确时再参考中文计划和台账；开合过程不能要求全程保持卡上的状态。"
              "局部特写、背影、遮挡、画外人物不要求露脸；只看见穿甲者的躯干或腿仍算该人物，"
              "不能由脸不在构图内推断缺头或人物缺席。明确能看到本该连接的断口才可判结构缺失。"
              "无人空甲、雕像和装备与穿戴者分开匹配；空甲的面罩状态不能算到人物身上。"
              "剧情允许的独立空甲不能仅凭同款外形判成人物克隆；要有第二张人脸或其他人在其中的可见证据。"
              "同色系明暗变化（如深灰与黑）和光照差异不算换装；看不清或描述没提到不等于缺失。\n"
              "原文明确的换装按本镜事件时点及穿戴关系核对，身份仍按角色参考辨认；"
              "本镜计划不能为与原文冲突的身份或衣着开脱。\n"
            + ('\n已确认的逐镜面罩状态：' + json.dumps(clip['review_visor_states'], ensure_ascii=False)
               if clip.get('review_visor_states') else ''))


def scripted_event(clip: dict) -> str:
    """Read the legacy event line or the source writer's existing structured bindings."""
    match = EVENT_LINE.search(str(clip.get("prompt") or ""))
    if match:
        return match.group(1)
    bindings = {key: clip[key] for key in ('action_bindings', 'wears_bindings') if clip.get(key)}
    return json.dumps(bindings, ensure_ascii=False) if bindings else ""


def story_block(clip: dict, segments: dict) -> str:
    """The passage this clip adapts and the planner's event line, for the judge to check the picture against."""
    source = "\n".join(str(segments.get(str(s), "")) for s in clip.get("segment_ids") or []).strip()
    event = scripted_event(clip)
    if not source and not event:
        return "\n本段原文（剧情依据）：（无）\n"
    return (f"\n本段原文（剧情依据）：{source or '（无）'}" + (f"\n本段剧本事件：{event}" if event else "")
            + "\n审查范围：原文段落可能覆盖多个镜头。先按本镜事件、动作与穿戴关系及实际请求定位当前阶段，"
              "再对照完整原文判断人物和动作归属；其他镜头的事件不要求在本镜全部出现。"
              "本镜计划与原文明示内容冲突仍须指出，不能用计划覆盖原文。\n")


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
        + shot_contract(clip)
        + evidence.snapshot
        + f"\n预期台词：{lines or '无'}\n语音识别出的台词：{hypothesis or '无'}\n"
        + (f"手机屏幕上应显示的群消息（这些文字允许出现）：{chats}\n" if chats else "")
        + "回答：visible_people 帧里清晰可见的人数（最多的一帧）；identity_ok 每个具名角色的实际身份是否一致、没有两个角色长成同一人、没有明显换脸/错人、近景里没有多出的具名角色（远处模糊背景里的人不算）；非关键服饰差异只在identity_issue记提示，不把identity_ok判false，真正不一致写清是谁、哪一帧；"
        "若给出了原著账本出场快照，以快照为准判断谁该在场、谁该做动作、谁只是声音或只被提及；快照说某人在别人的身体里，画面就该是那具身体。"
        "story_ok：对照完整原文与本镜阶段——当前镜头事件中应入镜、有动作或对白的人物是否出现？画面里的动作是否由原文说的那个人完成"
        "（例如原文是甲将物品递给乙，画面却由丙代替甲，就是动作落在错误的人物身上；出场人物名单漏了原文里的人，"
        "也按原文判）？只看原文写到的事，不苛求细节；story_kind 选最主要的一类（story_ok 为 true 时填无问题）；story_issue 用一句话写清谁缺席、或谁的动作被谁做了；"
        "按镜头阶段与抽帧时间核对，前阶段已出现的人物或动作不能因后阶段换景、近景裁切而判缺失；"
        "身份匹配的人物同时出现不是混淆。动作归属错误必须指出具体帧中实际的发起者和承受者，未覆盖动作的帧不能证明动作颠倒。"
        "ASR是辅助文字识别，专名同音或书写差异不能单独证明人物、动作或台词归属错误；"
        "location_ok 与 time_of_day_ok 是否符合地点和时间设定；text_or_watermark 画面是否出现手机屏幕聊天消息以外的文字、字幕、水印、Logo；"
        "chat_text_ok：若本段有应显示的群消息，帧里手机屏幕上的文字是否是清晰的简体中文且内容与预期一致（允许只显示部分或截断，不允许乱码、错字连篇或无关文字），没有预期消息时填 true，不一致时在 chat_text_issue 写清；visual_defects 是否有明显崩坏（多手、面部扭曲、肢体错位、人物穿模）；"
        "severity：identity 不一致、原文有动作的人物缺席或动作落在错误的人物身上、屏幕消息乱码或不符、文字水印或严重崩坏为 fail；仅地点时间存疑或轻微瑕疵为 minor；否则 pass。feedback 为一句给视频模型的修正指令（fail 时必填，指明谁应该长什么样、避免什么）。只输出JSON。"
    )
    return text + '\n' + review_contracts.WATCHABLE_CRITERIA


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
            + shot_contract(clip)
            + evidence.snapshot
            + evidence.source_contract
            + f"\n预期台词：{lines or '无'}\n" + evidence.world
            + evidence.identity
            + '\n验收尺度：当前原文、当前实际选用的阶段角色图优先于旧角色登记和历史判词。'
              '若旧文字性别/年龄与原文及当前图冲突，指出输入信息冲突，不要要求视频改成旧错误。'
              '不影响辨认人物和理解情节的色差、衣纹、发丝、轻微体型比例和背景装饰差异只记subtle；'
              'obvious必须有当前帧中可定位且影响观看的证据。静态抽帧不能证明漏说台词。'
              '关键人物缺席、动作由错的人做、关键事件相反或严重结构错误仍需修复。'
            + review_contracts.VERIFY_QUESTIONS)
    return text
