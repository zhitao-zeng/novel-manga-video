#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
from pathlib import Path

import httpx

from novel_manga.script_planning import source_evidence_units


CONTENT_DIRECTION = """
你是中文竖屏3D国漫短剧的资深编剧、导演和分镜师。严格忠于当前章原文的人物、事实、因果、顺序和章末边界。
内容创作规则：
1. 每个shot只有一个叙事功能、一个主要可见动作和一个可见说话者；说话者或delivery_mode变化必须新建shot。
2. 对话按建立镜、说话者近景、无声反应、反打、道具插入或环境响应形成覆盖；同一角色保持屏幕侧，
   但在胸像、紧肩部近景和较宽腰上景间有动机地变化，不复制同一完整构图。
3. 抽象情绪必须翻译成不超过三个可见信号：停顿、视线、下颌、眉间、呼吸、重心或手部接触。
4. scene_job按真实作用使用建立、对峙、揭示、反转、决定或收束，不能全部写推进。
5. 碎裂、撞击、奔跑、战气和强视效按准备→发力→接触→反作用→落定组织，并给少量同方向环境反馈；
   时长不足必须拆镜，不得把动作、多人对白和内心声塞进一个长镜头。
6. 每镜摄影机只有一个主意图，写起点、路径和终点；移动镜头稀疏且必须由空间揭示、明确位移、信息揭示、
   权力或情绪转折触发。同场对话保持180度行动轴和屏幕方向。
7. 原文短对白可逐字保留；长原句允许按turn连续拆开，或用abridged只删子句而不改词序。叙述可外化成
   有原文依据的载体对白、获取过程、因果桥接和反应；derived必须用serves指向已有event_id或fact_id。
8. 扩写不能新增原文事实、事件或StoryBible外的具名角色。可以发明无名听者和无名群声作为信息载体，
   但删掉一条derived后若没有原文事实失去载体、也没有因果断裂，这条就是闲聊，必须删除。
9. native_dialogue下禁止narration和inner_voice；时间提示改title_card，不发声动作或反应写silent_action。
10. 除非原文明示回忆，所有视觉与动作保持在当前时空，禁止补少年版角色、新地点或蒙太奇前史。
11. 角色与场景资产控制3D国漫画风；参考不锁原姿势、构图和机位。画面不生成可读文字。
12. 只输出严格符合给定JSON Schema的一个JSON对象，不输出Markdown、解释、思考过程或代码围栏。
""".strip()


OPERATION_GUIDANCE = {
    "diagnose_episode": "先提取当前章事件、因果、人物动机、可外化信息和严格章节边界，不写具体镜头。",
    "develop_series": "只写系列压力引擎、主角默认策略、升级阶梯、关系压力网和逐章投影，不写分镜与台词。",
    "review_series_development": "独立审查系列引擎和逐章投影，重点阻止后文事实泄漏；只审不改。",
    "plan_showrunner": "读取系列引擎的当前章投影，规划观众信息差、留存节点、选择与代价；shot_indexes保持空；function只能用hook/question/pressure/escalation/payoff/reversal/cliffhanger；cause事件必须先于dependent，若冷开场提前dependent则必须在cause之后再次绑定该dependent。",
    "plan_beat_script": "只写当前RetentionBeat的1到6个短镜和turn，不写机位、表演或声音规划；derived逐条填写serves；shot_intent.dramatic_function只能用establish/advance/pressure/withhold/reveal/payoff/reaction/transition/cliffhanger。native_dialogue时严禁narration和inner_voice；叙述性前史必须在当前场景用无名听者问答、群声或证据物外化，禁止回忆画面。",
    "plan_beat_direction": "只给已接受的beat剧本做动作、表演、机位和五维handoff；turn文字和顺序不可改，可按turn边界拆镜；输出的source_shot_index集合必须与accepted_script完全一致，每个source shot至少一行，禁止把后续source shot吸收到前镜turn范围。",
    "plan_episode": "按章节诊断和Showrunner输出可拍摄的完整EpisodePlan，优先叙事清楚、镜头覆盖和自然表演。",
    "review_episode": "作为独立审稿人检查忠实度、因果、节奏和可拍性，并逐条对derived做删除测试；只审不改；输出紧凑ScriptQualityReport，issues最多8条，禁止复制原文、episode_plan、镜头或schema。",
    "blind_compare": "盲评A/B两个剧本，逐一回答输入的七个问题，不猜版本；只输出紧凑JSON，不复制剧本。",
    "update_series_state": "只记录当前章结束后有原文证据的稳定状态，不推测未来章节。",
    "build_bible": "从提供的小说内容建立稳定角色、地点、画风和连续性圣经。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("DeepSeek local planner must return one JSON object")
    return value


def normalize_diagnosis(result: dict, payload: dict) -> dict:
    episode = payload.get("episode", {})
    source_text = str(episode.get("source_text", ""))
    evidence = source_evidence_units(source_text)
    known = {
        str(row.get("name", ""))
        for row in payload.get("story_bible", {}).get("characters", [])
        if row.get("name")
    }

    def closest_quote(value: str, description: str = "") -> str:
        if value in evidence:
            return value
        target = value or description
        return max(
            evidence,
            key=lambda row: difflib.SequenceMatcher(
                None, target, row
            ).ratio(),
        ) if evidence else value

    result["hook_source_quote"] = closest_quote(
        str(result.get("hook_source_quote", "")),
        str(result.get("hook", "")),
    )
    for event in result.get("events", []):
        if not isinstance(event, dict):
            continue
        unknown = [
            str(name)
            for name in event.get("characters", [])
            if str(name) not in known
        ]
        event["characters"] = [
            str(name)
            for name in event.get("characters", [])
            if str(name) in known
        ]
        if unknown:
            description = str(event.get("description", ""))
            event["description"] = (
                description + "；现场还有" + "、".join(unknown)
            ).strip("；")
        event["source_quote"] = closest_quote(
            str(event.get("source_quote", "")),
            str(event.get("description", "")),
        )
    return result


def normalize_episode_plan(result: dict) -> dict:
    for shot in result.get("shots", []):
        if not isinstance(shot, dict):
            continue
        for turn in shot.get("turns", []):
            if not isinstance(turn, dict):
                continue
            source_quote = str(turn.get("source_quote", ""))
            quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", source_quote)
            candidates = [text for text in quoted if 0 < len(text) <= 60]
            current = str(turn.get("text", ""))
            derivation = str(turn.get("derivation", "verbatim"))
            visible = bool(turn.get("speaking")) or (
                turn.get("delivery_mode") == "visible_dialogue"
            )

            if derivation == "derived" and candidates:
                chosen = max(
                    candidates,
                    key=lambda text: difflib.SequenceMatcher(
                        None, current, text
                    ).ratio(),
                )
                turn["text"] = chosen
                turn["derivation"] = "verbatim"
                continue

            if not visible or derivation != "verbatim":
                continue
            compact_text = re.sub(r"\s+", "", current)
            compact_quote = re.sub(r"\s+", "", source_quote)
            if compact_text and compact_text in compact_quote:
                continue
            if candidates:
                chosen = max(
                    candidates,
                    key=lambda text: difflib.SequenceMatcher(
                        None, current, text
                    ).ratio(),
                )
                turn["text"] = chosen
            else:
                # This is an intentional staging of cited narration as speech,
                # not a quotation copied from the chapter.
                turn["derivation"] = "derived"
    return result


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    base_url = os.getenv("DEEPSEEK_LOCAL_ROUTER_URL", "http://127.0.0.1:4000")
    model = os.getenv("DEEPSEEK_LOCAL_MODEL", "deepseek-local")
    operation = args.operation
    system = CONTENT_DIRECTION + "\n\n当前操作：" + OPERATION_GUIDANCE.get(
        operation,
        "严格按请求中的requirements和schema完成当前规划操作。",
    )
    if operation == "diagnose_episode":
        evidence = source_evidence_units(
            str(payload.get("episode", {}).get("source_text", ""))
        )
        payload["source_evidence"] = evidence
        system += (
            "\n诊断中的hook_source_quote和每个event.source_quote必须从请求的"
            "source_evidence数组中选择一整行逐字复制。characters只能使用"
            "story_bible.characters中的name；匿名群众只能写在description。"
        )
    if operation == "plan_episode":
        source_chars = len(
            re.sub(
                r"\s+",
                "",
                str(payload.get("episode", {}).get("source_text", "")),
            )
        )
        system += (
            f"\n当前章有效长度约{source_chars}字。成片只允许14到20个shot；"
            "turn总数至少12个，所有turn.text合计至少260个汉字；不足时补充一个"
            "有原文依据的短对白、画外声或必要旁白，不得新增事件。"
            "匿名少年、族人和围观群众不得作为可见说话者，代表性嘲讽压缩为"
            "画外群声或楚焱的无声反应，不能逐人生成近景。"
        )
    if payload.get("repair"):
        system += (
            "\n上一稿未通过校验。必须逐项修复repair.validation_errors，不得重复"
            "被点名的未知角色、改写引用、字段缺失或Schema错误。"
        )
    default_max_tokens = {
        "plan_episode": "50000",
        "review_episode": "5000",
        "blind_compare": "4000",
        "review_series_development": "4000",
        "update_series_state": "8000",
    }.get(operation, "20000")
    request = {
        "model": model,
        "max_tokens": int(
            os.getenv("DEEPSEEK_LOCAL_MAX_TOKENS", default_max_tokens)
        ),
        "temperature": 0.2,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        ],
    }
    timeout = float(os.getenv("DEEPSEEK_LOCAL_TIMEOUT", "900"))
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": os.getenv("DEEPSEEK_LOCAL_ROUTER_KEY", "local"),
                "anthropic-version": "2023-06-01",
            },
            json=request,
        )
        response.raise_for_status()
        body = response.json()
    text = "".join(
        str(block.get("text", ""))
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    result = extract_json(text)
    if operation == "diagnose_episode":
        result = normalize_diagnosis(result, payload)
    elif operation == "plan_episode":
        result = normalize_episode_plan(result)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
