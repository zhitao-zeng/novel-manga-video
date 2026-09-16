"""planning.outputs responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.models import EpisodePlan
from novel_manga.models import ScriptTurn
from novel_manga.models import Shot
from novel_manga.models import TurnDelivery
from novel_manga.models import TurnDerivation
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.text as pc_text

def to_episode_plan(raw: dict, shots: list[dict], location_map: dict[str, str], chapter_text: str, chapter_title: str, *, ctx: PlannerContext) -> EpisodePlan:
    chapter_key = pc_text.quote_key(chapter_text)
    plan_shots: list[Shot] = []
    for index, shot in enumerate(shots, start=1):
        turns: list[ScriptTurn] = []
        for turn in shot["turns"]:
            mode = turn["delivery_mode"]
            text = turn["text"]
            common = {
                "text": text,
                "emotion": turn.get("emotion") or "克制自然",
                "source_quote": shot["source_quote"][:500],
            }
            if mode == "silent_action":
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **common))
            elif mode == "singing":
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **{**common, "text": f"{turn['speaker_name']}哼唱：{text}"}))
            elif mode == "chat_message":  # the legacy plan model has no chat kind; keep it as a silent on-screen action
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **{**common, "text": f"屏幕消息 {turn['speaker_name']}：{text}"}))
            elif mode == "title_card":
                turns.append(ScriptTurn(role="narrator", speaker_name="旁白", speaking=False, delivery_mode=TurnDelivery.TITLE_CARD, derivation=TurnDerivation.DERIVED, **common))
            else:
                derivation = TurnDerivation.VERBATIM if pc_text.quote_key(text) in chapter_key else TurnDerivation.DERIVED
                turns.append(
                    ScriptTurn(
                        role=turn["speaker_name"],
                        speaker_name=turn["speaker_name"],
                        speaking=(mode == "visible_dialogue"),
                        delivery_mode=TurnDelivery(mode),
                        derivation=derivation,
                        **common,
                    )
                )
        first_text = turns[0].text if turns else ""
        narration = (shot.get("end_state") or first_text or "推进")[:80]
        plan_shots.append(
            Shot(
                index=index,
                narration=narration or "推进",
                subtitle=(first_text or narration)[:80] or "……",
                visual_prompt=shot["visual_prompt"] or narration,
                motion_prompt=shot["motion_prompt"] or narration,
                characters=shot["characters"],
                extras=list(shot.get("extras") or []),
                listeners=list(shot.get("listeners") or []),
                location=location_map[shot["location"]],
                source_quote=shot["source_quote"][:500],
                scene_job="推进",
                change=(shot.get("end_state") or "")[:240],
                shot_scale=shot["shot_scale"],
                turns=turns,
            )
        )
    return EpisodePlan(
        video_title=str(raw.get("video_title") or chapter_title),
        hook=str(raw.get("hook") or ""),
        summary=str(raw.get("summary") or ""),
        shots=plan_shots,
        creative_profile=ctx.policy,
    )




def render_markdown(raw: dict, shots: list[dict], report: dict, chapter_title: str) -> str:
    lines = [
        f"# {raw.get('video_title') or chapter_title}",
        "",
        f"钩子：{raw.get('hook', '')}",
        "",
        f"梗概：{raw.get('summary', '')}",
        "",
        f"镜数 {report['metrics']['shot_count']} · turn {report['metrics']['turn_count']} · 发声字数 {report['metrics']['spoken_chars']} · 逐字率 {report['metrics']['verbatim_ratio']}",
        "",
    ]
    for index, shot in enumerate(shots, start=1):
        cast = "、".join(shot["characters"]) or "无人物"
        lines.append(f"## 镜{index} · {shot.get('clip_hint') or ''} · {shot['location']} · {shot['shot_scale']} · {cast}")
        lines.append(f"开始时：{shot['visual_prompt']}")
        lines.append(f"主要事件：{shot['motion_prompt']}")
        lines.append(f"结束时：{shot['end_state']}")
        if shot.get("camera"):
            lines.append(f"机位：{shot['camera']}")
        if shot.get("light"):
            lines.append(f"光源：{shot['light']}")
        if shot.get("avoid"):
            lines.append(f"本段不要：{shot['avoid']}")
        if shot.get("sfx"):
            lines.append(f"音效：{shot['sfx']}")
        for turn in shot["turns"]:
            label = pc_constants.MODE_LABEL.get(turn["delivery_mode"], turn["delivery_mode"])
            who = turn["speaker_name"] or label
            prefix = f"【{who}·{label}】" if turn["speaker_name"] else f"【{label}】"
            lines.append(f"- {prefix}{turn['text']}")
        lines.append(f"原文（{shot['segment_id']}）：{shot['source_quote']}")
        lines.append("")
    if report.get("warnings"):
        lines.append("## 自动修正与提示")
        lines.extend(f"- {item}" for item in report["warnings"])
        lines.append("")
    return "\n".join(lines)
