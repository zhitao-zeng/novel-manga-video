#!/usr/bin/env python3
"""Compile episode 1 into direct-asset or hybrid-keyframe SD2.5 groups."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from novel_manga.ingest import read_novel
from novel_manga.models import EpisodePlan, SpeechStrategy, StoryBible, VisualStrategy
from novel_manga.production import (
    compile_production_plan,
    visible_character_names_for_shot,
)
from novel_manga.production_models import (
    ProviderPromptAdapter,
    SeriesAssetManifest,
    ShotContractBeat,
)
from novel_manga.production_runtime import build_visual_groups
from novel_manga.util import atomic_write_json


SILENT = "【无对白动作镜】"


def _compact(value: str, limit: int = 120) -> str:
    text = "".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _group_performance_contract(
    shot,
    *,
    group_position: int,
    group_count: int,
    current_is_silent_action: bool,
    preceding_silent_action: bool,
    current_is_establishing_narration: bool,
    preceding_establishing_narration: bool,
) -> dict[str, object]:
    plan = shot.performance_plan
    if plan is None:
        action = _compact(shot.motion_prompt, 160)
        return {
            "start_state": "本组主要动作开始前一瞬",
            "actions": [action],
            "end_state": "本组主要动作完成并落定",
        }
    beats = list(plan.motion_beats)
    if not beats:
        return {
            "start_state": plan.start_state,
            "actions": [_compact(shot.motion_prompt, 160)],
            "end_state": plan.end_state,
        }
    if current_is_establishing_narration:
        visible_actor = next(iter(shot.characters), "画面主体")
        return {
            "start_state": plan.start_state,
            "actions": [
                f"建立{shot.location or '当前场景'}、核心道具与{visible_actor}的空间位置"
            ],
            "end_state": plan.start_state,
        }
    if current_is_silent_action or preceding_establishing_narration:
        selected = beats
        start_state = plan.start_state
        end_state = plan.end_state
    elif preceding_silent_action:
        visible_actor = next(iter(shot.characters), "画面主体")
        return {
            "start_state": plan.end_state,
            "actions": [
                f"{visible_actor}保持落位并完成本组声音与反应，不重复前组走位"
            ],
            "end_state": plan.end_state,
        }
    elif group_count <= 1:
        selected = beats
        start_state = plan.start_state
        end_state = plan.end_state
    else:
        start_index = group_position * len(beats) // group_count
        end_index = (group_position + 1) * len(beats) // group_count
        if end_index <= start_index:
            end_index = min(len(beats), start_index + 1)
        selected = beats[start_index:end_index]
        start_state = (
            plan.start_state
            if start_index == 0
            else f"{beats[start_index - 1].action}已经完成"
        )
        end_state = (
            plan.end_state
            if end_index >= len(beats)
            else f"{selected[-1].action}完成"
        )
    return {
        "start_state": start_state,
        "actions": [beat.action for beat in selected],
        "end_state": end_state,
    }


def _group_performance_text(contract: dict[str, object]) -> tuple[str, int]:
    actions = [str(value) for value in contract["actions"]]
    return (
        f"起点={_compact(str(contract['start_state']), 32)}；"
        f"动作={'→'.join(_compact(action, 48) for action in actions)}；"
        f"终点={_compact(str(contract['end_state']), 32)}",
        len(actions),
    )


def _camera_text(shot) -> str:
    plan = shot.camera_plan
    if plan is None:
        return "固定机位，人物动作承担动态"
    trajectories = "→".join(
        _compact(beat.trajectory, 28) for beat in plan.camera_beats
    )
    return (
        f"{plan.mode}；{_compact(plan.start_position, 32)}；"
        f"{trajectories}；终点={_compact(plan.end_position, 32)}；"
        f"屏幕方向={_compact(plan.screen_direction, 36)}"
    )


def _duration_contract(
    spoken: list,
    silent: bool,
    performance_actions: list[str],
) -> dict:
    beat_count = max(1, len(performance_actions))
    action_text = "".join(performance_actions)
    displacement = any(token in action_text for token in ("走", "跑", "追", "绕", "穿过", "转身"))
    action_seconds = 0.55 + beat_count * (0.65 if displacement else 0.48)
    if silent:
        delivery = min(2.5, max(1.5, action_seconds))
        return {
            "generation_duration": 4.0,
            "delivery_duration": round(delivery, 3),
            "duration_basis": "silent_action_beats_then_trim_provider_minimum",
            "tail_reaction_seconds": 0.0,
        }
    speech_seconds = sum(max(1.0, len(row.text) / 4.6) for row in spoken)
    tail = 0.65
    delivery = min(14.0, max(3.0, speech_seconds + tail, action_seconds))
    return {
        "generation_duration": round(max(4.0, delivery), 3),
        "delivery_duration": round(delivery, 3),
        "duration_basis": "max(speech,performance_beats)+tail_reaction",
        "tail_reaction_seconds": tail,
    }


def _shot_contract_beats(
    performance_contract: dict[str, object],
    *,
    duration: float,
    actor: str,
    existing_beats: list,
) -> list[ShotContractBeat]:
    actions = [str(value) for value in performance_contract["actions"]]
    weights = []
    for action in actions:
        weight = 0.65 + min(0.8, len(action) / 32.0)
        if any(token in action for token in ("走", "跑", "追", "绕", "穿过", "转身")):
            weight += 0.45
        if any(token in action for token in ("按", "握", "挡", "行礼", "回头")):
            weight += 0.25
        weights.append(weight)
    total = sum(weights) or 1.0
    existing_by_action = {beat.action: beat for beat in existing_beats}
    cursor = 0.0
    compiled = []
    for index, (action, weight) in enumerate(zip(actions, weights, strict=True)):
        end = (
            duration
            if index == len(actions) - 1
            else cursor + duration * weight / total
        )
        existing = existing_by_action.get(action)
        reaction = existing.reaction if existing is not None else ""
        if "不重复前组走位" in action:
            reaction = "说话者完成本组声音，其他人物闭嘴并保持上一组落位状态"
        compiled.append(
            ShotContractBeat(
                start_seconds=round(cursor, 3),
                end_seconds=round(end, 3),
                actor_or_source=actor,
                trigger=(
                    existing.trigger
                    if existing is not None and existing.trigger
                    else "本组开始" if index == 0 else "上一动作完成"
                ),
                action=action,
                reaction=reaction,
                end_state=(
                    str(performance_contract["end_state"])
                    if index == len(actions) - 1
                    else existing.end_state if existing is not None else "动作完成"
                ),
            )
        )
        cursor = end
    return compiled


def _hybrid_keyframe_reason(shot, character_ids: list[str]) -> str | None:
    if len(character_ids) >= 2:
        return "multi_character_blocking"
    text = " ".join((shot.visual_prompt, shot.motion_prompt))
    if any(
        token in text
        for token in (
            "按碑", "触摸", "按住", "行礼", "挡住", "绕到", "追上", "并肩",
        )
    ):
        return "critical_prop_or_blocking_interaction"
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--hybrid-keyframes", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.video_id
    novel = read_novel(
        args.source.resolve(),
        novel_id="ftj-anime-api10-3d-script-ab",
        title="焚天纪",
    )
    plan = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    bible = StoryBible.model_validate_json(
        (novel_dir / "story_bible.json").read_text(encoding="utf-8")
    )
    assets = SeriesAssetManifest.model_validate_json(
        (novel_dir / "series_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    cards = json.loads(
        Path(
            "outputs/ftj-anime-api10-v1/ftj-anime-api10/series_assets/"
            "3d_asset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    character_cards = {row["asset_id"]: row for row in cards["characters"]}
    location_cards = {row["asset_id"]: row for row in cards["locations"]}
    character_id_by_name = {row.name: row.asset_id for row in assets.characters}
    asset_record_by_id = {
        row.asset_id: row for row in [*assets.characters, *assets.locations]
    }
    asset_version_by_id = {
        row.asset_id: row.version for row in [*assets.characters, *assets.locations]
    }
    episode_shot_by_id = {
        f"shot_{shot.index:03d}": shot for shot in plan.shots
    }
    runtime = compile_production_plan(
        args.video_id,
        novel.episodes[0],
        plan,
        bible,
        assets,
    )
    runtime.visual_groups = build_visual_groups(
        runtime,
        series_assets=assets,
        allow_cross_shot_merge=False,
    )
    units = {row.unit_id: row for row in runtime.units}
    groups_by_shot: dict[str, list] = {}
    for group in runtime.visual_groups:
        groups_by_shot.setdefault(group.shot_ids[0], []).append(group)

    rows = []
    for group_index, group in enumerate(runtime.visual_groups, 1):
        group_units = [units[unit_id] for unit_id in group.unit_ids]
        source_shot = episode_shot_by_id[group.shot_ids[0]]
        shot_groups = groups_by_shot[group.shot_ids[0]]
        group_position = shot_groups.index(group)
        silent_group_positions = {
            index
            for index, shot_group in enumerate(shot_groups)
            if any(units[unit_id].text == SILENT for unit_id in shot_group.unit_ids)
        }
        current_is_silent_action = group_position in silent_group_positions
        preceding_silent_action = any(
            index < group_position for index in silent_group_positions
        )
        narration_group_positions = {
            index
            for index, shot_group in enumerate(shot_groups)
            if len(shot_groups) > 1
            and all(
                units[unit_id].role == "narrator"
                and units[unit_id].text != SILENT
                for unit_id in shot_group.unit_ids
            )
        }
        current_is_establishing_narration = (
            group_position in narration_group_positions
        )
        preceding_establishing_narration = any(
            index < group_position for index in narration_group_positions
        )
        visible = [row for row in group_units if row.speaking]
        # Visibility belongs to the shot, not to the speaker.  This preserves
        # listeners, blockers and reaction characters in multi-person shots.
        visible_names = visible_character_names_for_shot(
            source_shot,
            tuple(character_id_by_name),
        )
        character_ids = list(
            dict.fromkeys(
                character_id_by_name[name]
                for name in visible_names
                if name in character_id_by_name
            )
        )
        speaker_id = (
            character_id_by_name.get(visible[0].speaker_name) if visible else None
        )
        if speaker_id in character_ids:
            character_ids = [speaker_id, *[item for item in character_ids if item != speaker_id]]
        group.character_asset_ids = character_ids
        keyframe_reason = (
            _hybrid_keyframe_reason(source_shot, character_ids)
            if args.hybrid_keyframes
            else None
        )
        needs_keyframe = keyframe_reason is not None
        group.visual_strategy = (
            VisualStrategy.STORY_KEYFRAME
            if needs_keyframe
            else VisualStrategy.DIRECT_ASSETS
        )
        group.keyframe_reasons = [keyframe_reason] if keyframe_reason else []
        group.direct_video_character_asset_ids = (
            []
            if needs_keyframe
            else ([speaker_id] if speaker_id is not None else character_ids[:1])
        )
        for unit in group_units:
            unit.audio_plan = unit.audio_plan.model_copy(
                update={"speech_strategy": SpeechStrategy.NATIVE}
            )
        if group.image_contract is not None:
            group.image_contract = group.image_contract.model_copy(
                update={
                    "exact_subject_count": len(character_ids),
                    "subject_asset_version_ids": [
                        f"{asset_id}@{asset_version_by_id[asset_id]}"
                        for asset_id in character_ids
                    ],
                    "purpose": (
                        "shot_start_keyframe"
                        if needs_keyframe
                        else "direct_asset_conditioning"
                    ),
                }
            )
        location = location_cards[group.location_asset_id]
        location_view_name = (
            "dialogue_angle_a"
            if visible and group_index % 2
            else (
                "dialogue_reverse_b"
                if visible
                else "performance_zone"
            )
        )
        location_ref = "series_assets/" + location["views"][location_view_name]
        references = []
        if needs_keyframe:
            for asset_id in character_ids:
                character = character_cards[asset_id]
                references.append(
                    {
                        "path": "series_assets/" + character["views"]["front_fullbody"],
                        "role": "character_identity_costume",
                        "asset_id": asset_id,
                        "view": "front_fullbody",
                    }
                )
            references.append(
                {
                    "path": location_ref,
                    "role": "location_space_lighting",
                    "asset_id": group.location_asset_id,
                    "view": location_view_name,
                }
            )
        elif visible and speaker_id is not None:
            character = character_cards[speaker_id]
            references.append(
                {
                    "path": "series_assets/" + character["views"]["portrait"],
                    "role": "character_identity_costume",
                    "asset_id": speaker_id,
                    "view": "portrait",
                }
            )
            references.append(
                {
                    "path": location_ref,
                    "role": "location_space_lighting",
                    "asset_id": group.location_asset_id,
                    "view": location_view_name,
                }
            )
            for asset_id in character_ids:
                if asset_id == speaker_id:
                    continue
                character = character_cards[asset_id]
                references.append(
                    {
                        "path": "series_assets/" + character["views"]["front_fullbody"],
                        "role": "character_identity_costume",
                        "asset_id": asset_id,
                        "view": "front_fullbody",
                    }
                )
        else:
            references.append(
                {
                    "path": location_ref,
                    "role": "location_space_lighting",
                    "asset_id": group.location_asset_id,
                    "view": location_view_name,
                }
            )
            for asset_id in character_ids:
                character = character_cards[asset_id]
                references.append(
                    {
                        "path": "series_assets/" + character["views"]["front_fullbody"],
                        "role": "character_identity_costume",
                        "asset_id": asset_id,
                        "view": "front_fullbody",
                    }
                )

        spoken = [row for row in group_units if row.text != SILENT]
        exact_lines = [f"{row.speaker_name}：{row.text}" for row in spoken]
        silent = not spoken
        reference_rules = []
        for index, reference in enumerate(references, 1):
            if reference["role"] == "character_identity_costume":
                reference_rules.append(
                    f"P{index}={reference['asset_id']}身份/服装，不继承站姿和背景"
                )
            else:
                reference_rules.append(
                    f"P{index}={reference['asset_id']}空间/光线"
                )
        performance_contract = _group_performance_contract(
            source_shot,
            group_position=group_position,
            group_count=len(shot_groups),
            current_is_silent_action=current_is_silent_action,
            preceding_silent_action=preceding_silent_action,
            current_is_establishing_narration=current_is_establishing_narration,
            preceding_establishing_narration=preceding_establishing_narration,
        )
        performance_text, performance_beat_count = _group_performance_text(
            performance_contract
        )
        emotions = list(
            dict.fromkeys(
                row.emotion for row in spoken if row.emotion and row.emotion != "克制自然"
            )
        )
        intent = source_shot.shot_intent
        audio_plan = source_shot.audio_plan.model_copy(
            update={"speech_strategy": SpeechStrategy.NATIVE}
        )
        sound_parts = [
            f"环境={_compact(audio_plan.ambience, 36)}" if audio_plan.ambience else "",
            f"音乐={_compact(audio_plan.music_cue, 36)}" if audio_plan.music_cue else "",
            (
                f"音效={'、'.join(_compact(item, 20) for item in audio_plan.sfx_events)}"
                if audio_plan.sfx_events
                else ""
            ),
        ]
        sound_design = "；".join(item for item in sound_parts if item) or "保留场景自然原声"
        if silent:
            voice_rule = f"SD2.5原声：无人声，所有人物闭嘴；{sound_design}。"
        elif visible:
            voice_rule = (
                f"SD2.5原声：只有{visible[0].speaker_name}开口，准确说"
                f"{'；'.join(exact_lines)}，句末闭嘴；{sound_design}。"
            )
        else:
            voice_rule = (
                f"SD2.5原声：画外准确说{'；'.join(exact_lines)}；"
                f"画内人物闭嘴；{sound_design}。"
            )
        keyframe_id = f"{group.group_id}_start_frame" if needs_keyframe else None
        identity_contracts = []
        for asset_id in character_ids:
            record = asset_record_by_id[asset_id]
            costume = record.state_variables.get("costume", "")
            invariants = "；".join(record.identity_invariants)
            identity_contracts.append(
                f"{record.name}必须严格继承对应参考图：{_compact(invariants, 190)}；"
                f"服装={_compact(costume, 70)}"
            )
        subject_names = "、".join(
            asset_record_by_id[asset_id].name for asset_id in character_ids
        )
        crowd_subject_rule = {
            "visual_012": (
                "楚焱在现实空间只能出现一个实例，前景只能有一个楚焱后脑和肩膀；"
                "楚烟儿与楚媚各出现一次；背景群众只能是远处无脸小剪影，"
                "前景和中景不得增加第四个人、额外头部或重复身体。"
            ),
            "visual_017": (
                "恰好楚烟儿与楚焱两人中景：楚烟儿位于画面左前方，楚焱已经站到她右肩旁，"
                "身体侧向前进方向，一只脚已迈到她身侧，明确为下一步越过她的动作起点；"
                "禁止把楚焱放在她正后方静止。"
            ),
            "visual_019": (
                "锁定机位的紧凑三人中景，三人从第一帧全部完整可见且互不遮挡："
                "楚焱与楚烟儿在前方并肩同向，楚媚独自站在他们后三步；"
                "禁止背对镜头的大头遮挡、禁止任何人位于画外。"
            ),
        }.get(group.group_id, "")
        image_prompt = (
            "原创东方3D动画剧情起始帧，执行严格的多参考图像编辑与空间合成，不重新设计角色；"
            + "；".join(reference_rules)
            + f"。恰好{len(character_ids)}名具名角色：{subject_names}。"
            + "；".join(identity_contracts)
            + "。地点必须严格继承楚家广场参考图：开阔白昼户外石质广场、蓝天群山、深木殿堂、"
            "中央黑色长方灵碑与金色裂纹；禁止室内、夜景、宫殿大厅、长廊、樱花、牌匾和任何可读文字。"
            + f"动作前一瞬：{_compact(str(performance_contract['start_state']), 64)}。"
            + f"构图：{_compact(source_shot.camera_plan.start_position if source_shot.camera_plan else source_shot.shot_scale, 48)}。"
            + "脸型、年龄、发型、发色、服装颜色与结构逐人匹配参考，不得性别互换、换装、长发化、铠甲化或增加皇冠；"
            + crowd_subject_rule
            + "无动作终点、额外具名人物、伪文字、字幕、水印。"
            if needs_keyframe
            else "disabled:no-keyframe"
        )
        video_execution_override = {
            "visual_009": (
                "本组唯一主动作必须在前0.6秒清楚发生：楚媚张开的手掌向前移动并完整贴到黑色灵碑表面，"
                "掌心与碑面保持可见接触至少一拍；禁止只向镜头挥手或隔空抬手。"
            ),
            "visual_010": (
                "灵碑表面始终只保留抽象金色裂纹与无字光芒，绝不出现‘七段’、汉字、数字、等级、"
                "符号或任何可读文字；测验结果只通过原声表达。"
            ),
            "visual_017": (
                "0到2秒楚烟儿说话且保持原地；2秒后楚焱从她右肩旁迈出一步，"
                "身体越过她的肩线并继续向画面深处前行，镜末楚焱必须在她前方至少一个身位；"
                "禁止楚焱全程站在她身后或原地不动。"
            ),
            "visual_019": (
                "摄影机完全锁定；三人从第一帧起已经落位并全程同时可见：楚焱与楚烟儿前方并肩，"
                "楚媚单独留在后三步；本组只完成原声对白与小幅转头，"
                "禁止任何人跑入、追上、重新进场、消失、被遮挡或重复前组走位。"
            ),
        }.get(group.group_id, "")
        prompt_components = {
            "style": "风格化中国3D国漫，9:16连续剧情镜头。",
            "references": (
                f"首帧={keyframe_id}锁定人物站位、道具和空间；"
                if needs_keyframe
                else "；".join(reference_rules) + "。"
            ),
            "scene": (
                f"场面：{_compact(source_shot.visual_prompt, 110)}。"
                f"本镜变化：{_compact(source_shot.scene_job, 48)}。"
            ),
            "performance": f"表演：{performance_text}。",
            "emotion_intent": (
                f"情绪：{'→'.join(emotions) if emotions else _compact(intent.emotion_target, 36)}；"
                f"权力关系：{_compact(intent.power_relation, 30)}；"
                f"观众焦点：{_compact(intent.viewer_focus, 30)}。"
            ),
            "camera": f"摄影机：{_camera_text(source_shot)}。",
            "audio": voice_rule,
            "constraints": (
                "身份、服装、人数、地点照参考；无拼版、分身、伪文字、水印。"
                + video_execution_override
            ),
        }
        prompt = "".join(prompt_components.values())
        duration_contract = _duration_contract(
            spoken,
            silent,
            [str(value) for value in performance_contract["actions"]],
        )
        adapter = ProviderPromptAdapter(
            adapter_version=(
                "sd25-hybrid-keyframe-p1-v1"
                if needs_keyframe
                else "sd25-direct-p1-v1"
            ),
            provider="phanrouter",
            image_model="gpt-image-2" if needs_keyframe else "disabled-no-keyframe",
            video_model="sd2.5",
            contract_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            reference_order=[row["asset_id"] for row in references],
            image_prompt=image_prompt,
            video_prompt=prompt,
        )
        group.prompt_adapter = adapter
        group.keyframe_prompt = image_prompt
        group.motion_prompt = prompt
        if group.shot_contract is not None:
            contract_actions = [
                str(value) for value in performance_contract["actions"]
            ]
            contract_duration = float(duration_contract["delivery_duration"])
            visible_version_ids = [
                f"{asset_id}@{asset_version_by_id[asset_id]}"
                for asset_id in character_ids
            ]
            must_hold = [
                (
                    "SD2.5原声为最终音轨，禁止外部TTS覆盖"
                    if "外部锁定音频" in item
                    else f"角色资产：{','.join(visible_version_ids) or '无具名角色'}"
                    if item.startswith("角色资产：")
                    else item
                )
                for item in group.shot_contract.must_hold
            ]
            group.shot_contract = group.shot_contract.model_copy(
                update={
                    "duration_seconds": contract_duration,
                    "visible_asset_ids": visible_version_ids,
                    "open_state": str(performance_contract["start_state"]),
                    "beat_timeline": _shot_contract_beats(
                        performance_contract,
                        duration=contract_duration,
                        actor=(
                            visible[0].speaker_name
                            if visible
                            else next(iter(source_shot.characters), "环境")
                        ),
                        existing_beats=group.shot_contract.beat_timeline,
                    ),
                    "close_state": str(performance_contract["end_state"]),
                    "changes_here": contract_actions[:5],
                    "external_audio_is_master": False,
                    "must_hold": must_hold,
                }
            )
        if group.image_contract is not None:
            group.image_contract = group.image_contract.model_copy(
                update={
                    "action_moment": str(performance_contract["start_state"]),
                }
            )
        rows.append(
            {
                "group_id": group.group_id,
                "shot_ids": group.shot_ids,
                "unit_ids": group.unit_ids,
                "silent": silent,
                "speaker": visible[0].speaker_name if visible else None,
                "exact_lines": exact_lines,
                "references": references,
                "prompt": prompt,
                "prompt_components": prompt_components,
                "prompt_component_chars": {
                    key: len(value) for key, value in prompt_components.items()
                },
                "prompt_adapter": adapter.model_dump(mode="json"),
                "emotion": emotions,
                "performance_beat_count": performance_beat_count,
                "performance_contract": performance_contract,
                "shot_intent": intent.model_dump(mode="json"),
                "audio_plan": audio_plan.model_dump(mode="json"),
                **duration_contract,
                "audio_path": None,
                "video_audio_path": None,
                "native_audio_source": group.raw_video_path,
                "raw_video_path": group.raw_video_path,
                "segment_path": group.segment_path,
                "keyframe_generation": needs_keyframe,
                "keyframe_id": keyframe_id,
                "keyframe_reason": keyframe_reason,
                "input_strategy": (
                    "hybrid_story_keyframe" if needs_keyframe else "direct_assets"
                ),
                "final_audio_policy": "sd25_native_original",
                "locked_tts_used_in_final": False,
                "provider": "phanrouter",
                "model": "sd2.5",
            }
        )

    keyframe_group_count = sum(row["keyframe_generation"] for row in rows)
    direct_group_count = len(rows) - keyframe_group_count
    payload = {
        "schema_version": 1,
        "video_id": args.video_id,
        "source_title": novel.episodes[0].source_title,
        "source_text_sha256": runtime.source_text_sha256,
        "style_fingerprint": runtime.style_fingerprint,
        "provider": "phanrouter",
        "model": "sd2.5",
        "video_workers": 2,
        "plan_mode": "hybrid" if args.hybrid_keyframes else "direct-assets",
        "keyframe_generation": keyframe_group_count > 0,
        "keyframe_mode": "conditional" if args.hybrid_keyframes else "disabled",
        "keyframe_provider": "phanrouter" if keyframe_group_count else "disabled",
        "keyframe_image_model": "gpt-image-2" if keyframe_group_count else "disabled",
        "keyframe_group_count": keyframe_group_count,
        "direct_group_count": direct_group_count,
        "qwen_image_used": False,
        "minimax_h3_used": False,
        "final_audio_policy": "sd25_native_original",
        "external_audio_is_master": False,
        "locked_tts_used_in_final": False,
        "audio_plan_backend": "sd25_native_audio_prompt",
        "duration_policy": "action_beats_and_speech_with_provider_minimum_trim",
        "physical_unit_count": len(runtime.units),
        "video_group_count": len(rows),
        "groups": rows,
    }
    atomic_write_json(episode_dir / "sd25_direct_plan.json", payload)
    atomic_write_json(
        episode_dir / "production_plan_sd25.json",
        runtime.model_dump(mode="json"),
    )
    print(
        json.dumps(
            {
                "physical_units": len(runtime.units),
                "video_groups": len(rows),
                "silent_groups": sum(row["silent"] for row in rows),
                "video_workers": 2,
                "keyframe_generation": keyframe_group_count > 0,
                "keyframe_groups": keyframe_group_count,
                "direct_groups": direct_group_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
