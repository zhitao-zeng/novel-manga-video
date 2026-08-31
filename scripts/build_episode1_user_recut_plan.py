#!/usr/bin/env python3
"""Build the user-authored 16-beat episode-1 recut as a valid EpisodePlan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from novel_manga.ingest import read_novel
from novel_manga.models import ChapterDiagnosis, EpisodePlan, StoryBible


SILENT = "【无对白动作镜】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--story-bible", type=Path, required=True)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def turn(
    speaker: str,
    text: str,
    source_quote: str,
    *,
    delivery: str = "visible_dialogue",
    derivation: str = "derived",
    emotion: str = "克制自然",
) -> dict:
    role = "narrator" if speaker == "旁白" else speaker
    return {
        "role": role,
        "speaker_name": speaker,
        "text": text,
        "speaking": delivery == "visible_dialogue",
        "delivery_mode": delivery,
        "emotion": emotion,
        "source_quote": source_quote,
        "derivation": derivation,
    }


def silent_turn(source_quote: str) -> dict:
    return turn(
        "旁白",
        SILENT,
        source_quote,
        delivery="narration",
        derivation="derived",
        emotion="无对白",
    )


def performance(objective: str, start: str, actions: list[str], end: str) -> dict:
    phases = ["opening", "development", "resolution"]
    beats = []
    for index, action in enumerate(actions):
        beats.append(
            {
                "phase": phases[min(index, 2)],
                "trigger": "上一动作完成" if index else "镜头开始",
                "action": action,
                "reaction": "局面变化清楚可见" if index == len(actions) - 1 else "",
                "expression_transition": "",
            }
        )
    return {
        "objective": objective,
        "start_state": start,
        "motion_beats": beats,
        "end_state": end,
    }


def camera(
    start: str,
    end: str,
    *,
    mode: str = "locked",
    trajectory: str = "固定机位",
    motivation: str = "人物表演承担画面动态",
    direction: str = "保持人物屏幕方向连续",
) -> dict:
    return {
        "mode": mode,
        "motivation": motivation,
        "action_axis": "楚家广场中央灵碑与队伍后排形成固定行动轴",
        "screen_direction": direction,
        "start_position": start,
        "camera_beats": [
            {
                "phase": "development",
                "trajectory": trajectory,
                "framing": start,
                "parallax": "建筑、灵碑和人群层次保持一致",
            }
        ],
        "end_position": end,
    }


def audio(ambience: str, cue: str, *, energy: float = 0.5) -> dict:
    return {
        "speech_strategy": "native",
        "voice_reference_id": "",
        "delivery_intent": "短句、直接、服务冲突",
        "pace": "自然偏紧",
        "energy": energy,
        "pauses": [],
        "music_cue": cue,
        "ambience": ambience,
        "sfx_events": [],
        "audio_beats": [
            {
                "position_ratio": 0.0,
                "cue_type": "ambience",
                "cue": ambience,
                "trigger": "镜头开始",
                "retention_beat_id": "",
            }
        ],
        "ducking": True,
    }


def hybrid_keyframe_reasons(
    characters: list[str],
    visual: str,
    motion: str,
) -> list[str]:
    """Route only spatially demanding beats through a generated start frame."""

    reasons: list[str] = []
    if len(characters) >= 2:
        reasons.append("multi_character_blocking")
    text = f"{visual} {motion}"
    if any(
        token in text
        for token in (
            "按碑",
            "触摸",
            "按住",
            "行礼",
            "挡住",
            "绕到",
            "追上",
            "并肩",
        )
    ):
        reasons.append("critical_prop_or_blocking_interaction")
    return reasons


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    novel = read_novel(
        args.source.resolve(),
        novel_id="ftj-anime-api10-3d-script-ab",
        title="焚天纪",
    )
    if len(novel.episodes) != 1:
        raise ValueError("user recut expects exactly one source chapter")
    bible = StoryBible.model_validate_json(
        args.story_bible.resolve().read_text(encoding="utf-8")
    )
    diagnosis = ChapterDiagnosis.model_validate_json(
        args.diagnosis.resolve().read_text(encoding="utf-8")
    )
    event = {row.event_id: row for row in diagnosis.events}

    def quote(event_id: str) -> str:
        return event[event_id].source_quote[:120]

    changes = [
        "观众知道楚焱这个名字令全场异常关注",
        "观众先知道楚焱曾是十岁九段的天才",
        "建立楚烟儿在意楚焱结果",
        "楚焱主动走向灵碑并接受全场注视",
        "三段结果击碎天才预期，楚焱没有立刻缩手",
        "匿名嘲讽变成楚媚与楚焱的具名对立",
        "楚焱被全场以行动孤立",
        "楚媚七段成为对楚焱的直接压制",
        "楚烟儿九段登顶且第一反应是寻找楚焱",
        "楚烟儿公开站到楚焱一边，楚媚受到关系打击",
        "楚焱第一次开口，主动拒绝被尊重",
        "楚烟儿用楚焱自己的话反驳他",
        "楚焱主动离场，楚烟儿用走位阻拦",
        "楚烟儿给出主题承诺，楚焱用绕行动作拒绝",
        "楚烟儿追上并肩，关系得到兑现，楚媚成为后续对手",
        "楚焱主动把三年战气消失变成可听见的悬念问题",
    ]
    shots: list[dict] = []

    def add(
        *,
        title: str,
        event_ids: list[str],
        characters: list[str],
        visual: str,
        motion: str,
        turns: list[dict],
        performance_plan: dict,
        camera_plan: dict,
        function: str,
        power: str,
        emotion: str,
        focus: str,
        ambience: str,
        music: str,
        source_quote: str,
        scale: str = "中景",
        keyframe_reasons: list[str] | None = None,
    ) -> None:
        index = len(shots) + 1
        routed_keyframe_reasons = keyframe_reasons or hybrid_keyframe_reasons(
            characters,
            visual,
            motion,
        )
        shots.append(
            {
                "index": index,
                "narration": title,
                "subtitle": turns[0]["text"] if turns else SILENT,
                "visual_prompt": visual,
                "motion_prompt": motion,
                "characters": characters,
                "location": "楚家广场",
                "source_quote": source_quote[:120],
                "scene_job": f"{title}｜变化：{changes[index - 1]}",
                "change": changes[index - 1],
                "event_ids": event_ids,
                "shot_scale": scale,
                "turns": turns,
                "performance_plan": performance_plan,
                "camera_plan": camera_plan,
                "visual_strategy": (
                    "story-keyframe"
                    if routed_keyframe_reasons
                    else "direct-assets"
                ),
                "keyframe_reasons": routed_keyframe_reasons,
                "shot_intent": {
                    "dramatic_function": function,
                    "power_relation": power,
                    "emotion_target": emotion,
                    "information_fact_ids": [],
                    "viewer_focus": focus,
                    "retention_beat_id": f"beat_{min(7, max(1, (index + 1) // 2)):03d}",
                },
                "audio_plan": audio(ambience, music),
            }
        )

    add(
        title="点名",
        event_ids=["event_001"],
        characters=["中年测验员"],
        visual="3D国漫楚家广场大全景，族人围成半圆，中央黑色灵碑；测验员在名册旁抬头，众人同时回望队伍后方。",
        motion="摄影机从高处缓慢推向灵碑；测验员抬头点名，人群的头部和视线依次转向后方。",
        turns=[
            turn("旁白", "楚家，年度测验。", quote("event_001"), delivery="narration"),
            turn("中年测验员", "下一个，楚焱。", quote("event_001"), emotion="漠然"),
        ],
        performance_plan=performance("让名字引发全场反应", "广场嘈杂", ["测验员抬头点名", "族人同时回望后排"], "全场注意力集中到后排"),
        camera_plan=camera("高处俯拍大全景", "测验员中景", mode="motivated_subtle", trajectory="缓慢推向灵碑和测验员", motivation="点名把全场注意力导向主角"),
        function="establish", power="家族审视尚未出场的楚焱", emotion="异常期待", focus="全场因一个名字改变反应", ambience="广场人群嘈杂", music="低频悬念音进入", source_quote=quote("event_001"), scale="大全景",
    )
    add(
        title="天才？", event_ids=["event_006"], characters=["楚媚"],
        visual="楚媚位于前排，侧身对同伴说话并抬下巴示意队伍后方；画面只让楚媚清楚可见。",
        motion="楚媚抬下巴指向后排，语气带笑，第二句落下时转眼等待结果。",
        turns=[turn("楚媚", "十岁九段的天才。", quote("event_006"), emotion="讥诮"), turn("楚媚", "看看还剩几段。", quote("event_007"), emotion="期待看笑话")],
        performance_plan=performance("先建立楚焱昔日高度", "楚媚面向同伴", ["楚媚抬下巴示意后排", "楚媚转眼等待结果"], "天才预期被立起"),
        camera_plan=camera("楚媚过肩中近景", "楚媚侧前方近景"), function="withhold", power="楚媚以旧天才身份制造审判", emotion="讥讽与期待", focus="十岁九段的昔日高度", ambience="私语声", music="悬念持续", source_quote=quote("event_006"), scale="中近景",
    )
    add(
        title="有人在等", event_ids=["event_010"], characters=["楚烟儿"],
        visual="楚烟儿紫衣站在前排另一侧，双手交叠，转头看向队伍后方；其他人物虚化。",
        motion="楚烟儿转头看向后排后停住两秒，全程闭嘴。", turns=[],
        performance_plan=performance("建立楚烟儿在意结果", "楚烟儿面向灵碑", ["楚烟儿转头望向后排", "保持等待"], "她的视线停在楚焱方向"),
        camera_plan=camera("楚烟儿胸像", "楚烟儿正面近景", mode="motivated_subtle", trajectory="轻微推近", motivation="无对白视线为后续公开站队铺垫"), function="withhold", power="楚烟儿尚未公开表态", emotion="克制关切", focus="唯一在等待楚焱的人", ambience="私语压低", music="留白", source_quote=quote("event_010"), scale="近景",
    )
    add(
        title="走向灵碑", event_ids=["event_001"], characters=["楚焱"],
        visual="人群从中分开，楚焱从最后一排走向中央黑色灵碑，脊背挺直；到碑前伸手按上碑面。",
        motion="楚焱沿人群通道直走到碑前，抬手按住碑面，全程闭嘴。", turns=[],
        performance_plan=performance("让主角主动进入审判中心", "楚焱站在后排", ["楚焱穿过分开的人群", "楚焱伸手按上灵碑"], "楚焱主动承受全场视线"),
        camera_plan=camera("楚焱肩后低角度中景", "手按碑面的近景", mode="motivated_subtle", trajectory="低角度跟拍到碑前后停住", motivation="人物明确位移建立主动性", direction="楚焱始终由后排走向画面深处中央"), function="advance", power="楚焱主动进入家族规则中心", emotion="压迫中的挺直", focus="楚焱自己走上去", ambience="脚步与私语", music="低频节奏", source_quote=quote("event_001"), scale="中全景",
    )
    add(
        title="三段", event_ids=["event_001"], characters=["楚焱"],
        visual="楚焱单手按住灵碑，碑面只出现不可读微光；报数后他的手仍多停一拍，再主动收回。",
        motion="碑面微光闪两次；画外报数；全场停顿；楚焱不缩手，多按一拍再收回。",
        turns=[turn("中年测验员", "三段，低级。", quote("event_001"), delivery="offscreen_dialogue", emotion="漠然")],
        performance_plan=performance("打碎天才预期并保留尊严", "楚焱手按灵碑", ["灵碑微光闪两次", "楚焱不缩手并多停一拍", "楚焱主动收手"], "哄笑即将爆发"),
        camera_plan=camera("楚焱与灵碑中景", "楚焱收手的近景", mode="motivated_emphasis", trajectory="从灵碑收紧到手部后停住", motivation="结果打碎前置预期"), function="pressure", power="灵碑结果把楚焱压到最低", emotion="死寂后羞辱", focus="他没有立刻缩手", ambience="报数后一秒死寂再爆笑", music="结果处骤停", source_quote=quote("event_001"), scale="中近景",
    )
    add(
        title="谁在笑", event_ids=["event_002"], characters=["楚焱", "楚媚"],
        visual="楚媚在前排笑得最大声；楚焱从灵碑前转身，越过人群直视她；两人位于固定对话轴两端。",
        motion="楚媚掩嘴发笑；楚焱转身直视；楚媚迎上目光不躲。",
        turns=[turn("楚媚", "三段？也配叫天才？", quote("event_002"), emotion="尖刻")],
        performance_plan=performance("建立具名对手", "楚媚正在笑", ["楚媚放下掩嘴的手", "楚焱转身直视她", "楚媚不躲"], "两人对立成立"),
        camera_plan=camera("楚媚侧前方近景", "楚媚反打特写", mode="motivated_subtle", trajectory="沿行动轴轻微收紧", motivation="匿名群嘲被集中到具名对手"), function="pressure", power="楚媚公开踩低楚焱", emotion="尖锐对立", focus="楚焱直视具体嘲讽者", ambience="人群哄笑", music="低沉冲突音", source_quote=quote("event_002"), scale="近景",
    )
    add(
        title="走回去", event_ids=["event_003"], characters=["楚焱"],
        visual="楚焱穿过人群走回最后一排；族人向两侧避开且不看他，中央通道越来越空。",
        motion="楚焱正面走向后排；两侧族人依次避开；他到后排站定并闭嘴。", turns=[],
        performance_plan=performance("以行动表现孤立", "楚焱站在灵碑前", ["楚焱穿过人群", "族人依次避开", "楚焱在后排站定"], "他与人群之间形成空隙"),
        camera_plan=camera("楚焱正面中景", "后排孤立中全景", mode="motivated_subtle", trajectory="正面后退跟拍", motivation="人物位移和人群避让建立社会孤立", direction="楚焱持续朝队伍后方移动"), function="transition", power="人群用距离排斥楚焱", emotion="公开孤立", focus="不是让路而是避开", ambience="只保留脚步，笑声淡出", music="低音留白", source_quote=quote("event_003"), scale="中全景",
    )
    add(
        title="七段", event_ids=["event_004", "event_005"], characters=["楚媚", "楚焱"],
        visual="楚媚上前按碑，灵碑光芒明显强于楚焱；听到结果后她回头看向后排再笑。",
        motion="楚媚快步上前按碑；光芒亮起；画外报数；楚媚回头朝后排笑。",
        turns=[turn("中年测验员", "七段，高级。", quote("event_004"), delivery="offscreen_dialogue"), turn("楚媚", "耶！", '"耶！"', derivation="verbatim", emotion="得意")],
        performance_plan=performance("把七段变成针对楚焱的比较", "楚媚在队伍前排", ["楚媚按上灵碑", "楚媚听到结果后回头", "楚媚朝后排笑"], "她的成绩直接压向楚焱"),
        camera_plan=camera("楚媚与灵碑中景", "楚媚回头近景", mode="motivated_subtle", trajectory="从灵碑轻移到回头视线", motivation="成绩的意义由楚媚回望楚焱完成"), function="pressure", power="楚媚七段高于楚焱三段", emotion="得意压制", focus="楚媚回头把成绩变成打击", ambience="羡慕声", music="短促上扬", source_quote=quote("event_004"), scale="中景",
    )
    add(
        title="九段", event_ids=["event_008", "event_009"], characters=["楚烟儿"],
        visual="楚烟儿按住灵碑，强光照亮广场；测验员站直；楚烟儿听完只点头，转身直接望向后排。",
        motion="楚烟儿按碑；强光爆开；画外报出九段和百年第二人；楚烟儿点头并转身看向后排。",
        turns=[turn("中年测验员", "九段，高级。", quote("event_008"), delivery="offscreen_dialogue"), turn("中年测验员", "半年凝旋，百年第二人。", quote("event_009"), delivery="offscreen_dialogue")],
        performance_plan=performance("完成第二级成绩递进并把榜首视线指向榜尾", "楚烟儿站在碑前", ["楚烟儿按碑触发强光", "楚烟儿只点头", "楚烟儿转身寻找楚焱"], "全场知道她首先在意楚焱"),
        camera_plan=camera("灵碑与楚烟儿大全景", "楚烟儿转身胸像", mode="motivated_emphasis", trajectory="由俯拍广场落到转身视线", motivation="九段高潮与关系指向同时揭示"), function="payoff", power="楚烟儿登顶家族年轻一辈", emotion="震撼后克制", focus="榜首第一反应是寻找榜尾", ambience="强光瞬间静音", music="清雅主题进入", source_quote=quote("event_008"), scale="大全景",
    )
    add(
        title="楚焱哥哥", event_ids=["event_010"], characters=["楚烟儿", "楚媚", "楚焱"],
        visual="楚烟儿穿过安静的人群走向后排；楚媚留在前景目送；楚烟儿在楚焱面前弯腰行礼。",
        motion="楚烟儿沿固定通道走近楚焱并行礼；楚媚的得意状态停止；楚烟儿抬头开口。",
        # The visible action (walk in, bow, establish the public cost) must
        # precede the spoken address.  The old order rendered the line first
        # and only showed the approach afterwards.
        turns=[silent_turn(quote("event_010")), turn("楚烟儿", "楚焱哥哥。", quote("event_010"), derivation="verbatim", emotion="恭敬")],
        performance_plan=performance("公开站队并表现代价", "楚烟儿位于灵碑前", ["楚烟儿穿过人群", "楚烟儿向楚焱弯腰", "楚媚停止笑"], "楚烟儿公开站在楚焱面前"),
        camera_plan=camera("楚焱肩后过肩中景", "楚烟儿行礼中近景", mode="motivated_subtle", trajectory="跟随楚烟儿走近后停住", motivation="公开跨越人群完成关系选择"), function="reveal", power="榜首公开尊重榜尾", emotion="温暖反差", focus="楚烟儿的站队及楚媚反应", ambience="人群安静", music="温暖旋律进入", source_quote=quote("event_010"), scale="中近景",
    )
    add(
        title="也配？", event_ids=["event_011"], characters=["楚焱", "楚烟儿"],
        visual="楚焱面向楚烟儿，抬头直视她后开口；背景人群完全虚化。",
        motion="楚焱低头短笑，随后抬头直视楚烟儿并说完短句。",
        turns=[turn("楚焱", "三段，也配你这么叫？", quote("event_011"), emotion="自嘲")],
        performance_plan=performance("让主角主动拒绝被尊重", "楚焱低头站在后排", ["楚焱抬头直视", "楚焱开口反问"], "他的攻击指向自己"),
        camera_plan=camera("楚焱正面胸像", "楚焱正面近景", mode="motivated_subtle", trajectory="极慢推近后停住", motivation="主角第一次开口需要视觉集中"), function="payoff", power="楚焱以自我否定抵抗楚烟儿", emotion="苦涩防御", focus="主角第一次主动开口", ambience="周围压低", music="温暖旋律转低", source_quote=quote("event_011"), scale="近景",
    )
    add(
        title="你教我的", event_ids=["event_012"], characters=["楚烟儿", "楚焱"],
        visual="楚烟儿面对画外楚焱，不后退并向前半步，嘴部清楚无遮挡。",
        motion="楚烟儿向前半步，稳住身体后说出短句。",
        turns=[turn("楚烟儿", "放得下才拿得起，你教我的。", quote("event_012"), emotion="坚定温柔")],
        performance_plan=performance("用楚焱自己的话反驳他", "楚烟儿与楚焱保持一步距离", ["楚烟儿向前半步", "楚烟儿说完保持原位"], "她拒绝接受他的自我否定"),
        camera_plan=camera("楚烟儿正面胸像", "楚烟儿肩部近景", mode="motivated_subtle", trajectory="轻微推近", motivation="她把楚焱过去的话返还给他"), function="reveal", power="楚烟儿掌握楚焱曾经的价值观", emotion="温柔反击", focus="她用他自己的话堵住他", ambience="人群远声", music="温暖主题", source_quote=quote("event_012"), scale="近景",
    )
    add(
        title="那是以前", event_ids=["event_012", "event_014"], characters=["楚焱", "楚烟儿"],
        visual="楚焱转身离开；楚烟儿快步绕到前方挡住去路，两人侧面走位完整可见。",
        motion="楚焱转身就走并说短句；楚烟儿从侧面绕到前方站住。",
        turns=[turn("楚焱", "那是以前。", quote("event_012"), emotion="拒绝")],
        performance_plan=performance("把对话升级成空间对抗", "两人面对面", ["楚焱转身离开", "楚烟儿绕到前方挡住"], "楚烟儿改变楚焱离场路线"),
        camera_plan=camera("两人侧面中景", "楚烟儿挡在前方的正面中景", mode="motivated_subtle", trajectory="侧向短移跟随两人走位", motivation="身体走位承担对抗升级", direction="楚焱由左向右离开，楚烟儿绕行后停在右侧前方"), function="advance", power="楚焱试图结束对话，楚烟儿阻止", emotion="关系拉扯", focus="主动离场与挡路", ambience="脚步声", music="节奏收紧", source_quote=quote("event_012"), scale="中景",
    )
    add(
        title="你会站回去", event_ids=["event_013"], characters=["楚烟儿", "楚焱"],
        visual="楚烟儿正对画外楚焱说出承诺；楚焱停两秒后从她身侧绕过继续走。",
        motion="楚烟儿快速说完两句；楚焱停住两秒；楚焱绕过她继续前行。",
        turns=[turn("楚烟儿", "不管发生过什么。", quote("event_013"), emotion="认真"), turn("楚烟儿", "你会站回去的。", quote("event_013"), emotion="坚定")],
        performance_plan=performance("说出本集主题并让主角以动作回应", "楚烟儿挡在楚焱前方", ["楚烟儿说出承诺", "楚焱停两秒", "楚焱从她身侧绕过"], "楚焱没有接受也没有反驳"),
        camera_plan=camera("楚烟儿正面近景", "楚焱绕过的侧面中景", mode="motivated_subtle", trajectory="从楚烟儿近景克制拉到两人中景后停住", motivation="主题句与无言拒绝共同构成反转"), function="payoff", power="楚烟儿坚定承诺，楚焱仍掌握去留", emotion="坚定与克制拒绝", focus="主题句及楚焱动作回答", ambience="环境声极低", music="温暖主题抬升", source_quote=quote("event_013"), scale="近景",
    )
    add(
        title="等等我", event_ids=["event_013", "event_015"], characters=["楚烟儿", "楚焱", "楚媚"],
        visual="楚焱已经走出十几步；楚烟儿追上并与他并肩；远处楚媚留在广场，目送两人离开。",
        motion="楚烟儿停一秒后起跑，追上楚焱并肩行走；她看向前方说话；楚媚停在远处。",
        # Chase and establish the two-shot before dialogue.  This prevents
        # “等等我” from playing after the characters are already walking
        # together.
        turns=[silent_turn(quote("event_015")), turn("楚烟儿", "楚焱哥哥，等等我。", quote("event_015"), emotion="坚定"), turn("楚烟儿", "以前的你，很耀眼。", quote("event_013"), emotion="坦率")],
        performance_plan=performance("兑现关系并保留具名对手", "楚焱独自离开", ["楚烟儿起跑追上", "两人并肩前行", "楚媚留在后方目送"], "楚焱不再独自离开"),
        camera_plan=camera("楚烟儿背后中景", "两人并肩背影远景", mode="motivated_subtle", trajectory="跟随楚烟儿追上后放慢", motivation="追赶和并肩完成关系变化", direction="楚烟儿与楚焱始终朝广场出口移动"), function="cliffhanger", power="楚烟儿选择与楚焱并肩，楚媚被留在后方", emotion="温暖中带嫉妒", focus="关系兑现与楚媚后续对立", ambience="脚步与低声私语", music="温暖主题渐强", source_quote=quote("event_015"), scale="中远景",
    )
    add(
        title="战气去了哪里", event_ids=["event_007", "event_015"], characters=["楚焱", "楚烟儿"],
        visual="两人走出广场门后，楚焱低头看右手，再慢慢握紧；背景只保留远去广场和楚烟儿模糊同行位置。",
        motion="楚焱继续前行，低头看右手，慢慢握紧后抬眼；画面切黑。",
        turns=[turn("楚焱", "三年了，我的战气去了哪里？", quote("event_007"), delivery="inner_voice", emotion="追问")],
        performance_plan=performance("把设定注释变成主角主动问题", "楚焱与楚烟儿并肩离开", ["楚焱低头看右手", "楚焱慢慢握紧手掌", "楚焱抬眼继续前行"], "战气消失成为可听见的开放问题"),
        camera_plan=camera("广场门外远景", "楚焱手部与侧脸近景", mode="motivated_emphasis", trajectory="由远景收紧到手部和侧脸后切黑", motivation="章末必须在画面内抛出下集动力"), function="cliffhanger", power="楚焱开始主动面对三年谜团", emotion="压抑转为追问", focus="战气究竟去了哪里", ambience="广场声远去", music="悬念低音收尾", source_quote=quote("event_007"), scale="特写",
    )

    event_to_shots: dict[str, list[int]] = {row.event_id: [] for row in diagnosis.events}
    for shot in shots:
        for event_id in shot["event_ids"]:
            event_to_shots[event_id].append(shot["index"])
    ledger = []
    for row in diagnosis.events:
        indexes = event_to_shots[row.event_id]
        if row.event_id in {"event_002", "event_005", "event_006", "event_007"}:
            disposition = "externalized"
            rationale = "原文叙述或匿名冲突改为具名角色短对白、可见动作或章末主动问题"
        elif len(indexes) > 1:
            disposition = "merged"
            rationale = "同一事件在预期、兑现或关系反应中复用，但不新增事实"
        else:
            disposition = "preserved"
            rationale = "事件以短剧动作和短句保留"
        ledger.append(
            {
                "event_id": row.event_id,
                "disposition": disposition,
                "shot_indexes": indexes,
                "rationale": rationale,
            }
        )

    plan = EpisodePlan.model_validate(
        {
            "video_title": "陨落的天才·重排对照版",
            "hook": "全场先记起十岁九段的天才，再亲眼看见楚焱只剩三段低级。",
            "summary": "楚焱主动走上测验台，却以三段低级遭楚媚公开嘲讽；楚烟儿九段登顶后公开站到他身边，追上离场的楚焱。楚焱第一次把三年战气消失说成自己必须面对的问题。",
            "next_preview": "三年消失的战气，究竟去了哪里？",
            "creative_profile": "short-drama-adaptive-v1",
            "dramaturgy": {
                "genre_engine": "公开羞辱、阶层对抗与关系站队",
                "dramatic_question": "从十岁九段跌到三段的楚焱，会继续逃避还是开始追问？",
                "cold_open": "楚焱的名字让全场回头，楚媚先说出他曾是十岁九段的天才。",
                "cold_open_source_quote": quote("event_006"),
                "status_before": "楚焱曾是家族最年轻战者，却已被当成笑柄。",
                "status_after": "楚焱仍是三段，但楚烟儿公开站队，他开始主动追问战气消失。",
                "conflict_beats": ["天才预期", "三段打脸", "楚媚公开嘲讽", "楚烟儿公开站队", "楚焱章末追问"],
                "reveal_order": ["十岁九段", "如今三段", "楚媚七段", "楚烟儿九段", "三年战气失踪"],
                "cliffhanger": "楚焱握紧手掌，第一次问出战气究竟去了哪里。",
                "narration_budget_ratio": 0.05,
            },
            "shots": shots,
            "adaptation_ledger": ledger,
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "episode_plan.json", plan.model_dump(mode="json"))
    write_json(output / "chapter_diagnosis.json", diagnosis.model_dump(mode="json"))
    source_text = novel.episodes[0].source_text
    write_json(
        output / "content_trace.json",
        {
            "schema_version": 1,
            "video_id": output.name,
            "source_title": novel.episodes[0].source_title,
            "source_text_sha256": hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            "shots": [
                {
                    "shot_id": f"shot_{shot.index:03d}",
                    "shot_index": shot.index,
                    "event_ids": shot.event_ids,
                    "source_quote": shot.source_quote,
                    "turns": [
                        {
                            "turn_id": f"shot_{shot.index:03d}_turn_{turn_index:02d}",
                            "speaker": row.speaker_name,
                            "text": row.text,
                            "derivation": str(row.derivation),
                            "source_quote": row.source_quote or shot.source_quote,
                        }
                        for turn_index, row in enumerate(shot.turns, 1)
                    ],
                }
                for shot in plan.shots
            ],
        },
    )
    write_json(
        output / "user_recut_spec.json",
        {
            "schema_version": 1,
            "source_title": novel.episodes[0].source_title,
            "style_fingerprint": bible.style_fingerprint,
            "silent_marker": SILENT,
            "narrative_beat_count": 16,
            "meaningful_changes": [
                {"shot_index": index, "change": value}
                for index, value in enumerate(changes, 1)
            ],
            "protagonist_agency_shots": [4, 5, 6, 13, 14, 16],
            "named_conflict_shots": [2, 6, 8, 10, 15],
            "future_chapter_content_used": False,
            "physical_unit_policy": "reaction inserts and silent beats become separate local video groups",
        },
    )
    non_silent_turns = [
        row
        for shot in plan.shots
        for row in shot.turns
        if row.text != SILENT
    ]
    narration = [row for row in non_silent_turns if row.role == "narrator"]
    verbatim_turn_count = sum(
        str(row.derivation) == "verbatim" for row in non_silent_turns
    )
    write_json(
        output / "script_metrics.json",
        {
            "shot_count": len(plan.shots),
            "spoken_turn_count": len(non_silent_turns),
            "silent_visual_unit_count": sum(
                not shot.turns for shot in plan.shots
            )
            + sum(
                row.text == SILENT for shot in plan.shots for row in shot.turns
            ),
            "max_turn_char_count": max(len(row.text) for row in non_silent_turns),
            "narration_turn_count": len(narration),
            "verbatim_turn_count": verbatim_turn_count,
            "verbatim_turn_ratio": round(
                verbatim_turn_count / len(non_silent_turns),
                6,
            ),
            "verbatim_turn_ratio_max": 0.35,
            "derived_turn_count": sum(
                str(row.derivation) == "derived" for row in non_silent_turns
            ),
            "protagonist_agency_shot_count": 6,
            "named_conflict_shot_count": 5,
            "meaningful_change_coverage": 1.0,
            "visible_cliffhanger": True,
            "speech_strategy": "native",
            "future_content_used": False,
        },
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "shots": len(plan.shots),
                "spoken_turns": len(non_silent_turns),
                "style_fingerprint": bible.style_fingerprint,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
