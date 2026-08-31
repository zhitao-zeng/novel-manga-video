from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageStat

from .admission import evaluate_episode_admission
from .config import NATIVE_VIDEO_AUDIO_POLICIES, Settings
from .face_consistency import evaluate_face_consistency
from .indextts import (
    INDEXTTS_SYNTHESIS_TEXT_POLICY,
    indextts_synthesis_identity,
    indextts_synthesis_text,
)
from .models import Episode, EpisodePlan, StoryBible, VisualStrategy
from .production import SeriesAssetFactory, compile_production_plan, sha256_file, sha256_text
from .production_models import (
    EpisodeSequenceContract,
    ImagePromptContract,
    ProductionPlan,
    ProviderPromptAdapter,
    ReferenceScope,
    RuntimeUnit,
    RuntimeVisualGroup,
    SeriesAssetManifest,
    ShotContract,
    ShotContractBeat,
)
from .providers.base import ImageResult, MediaProvider
from .qc import inspect_media
from .render import Renderer
from .runtime_backends import (
    RuntimeEvidenceBackends,
    aggregate_asr,
    measured_speech_bounds,
)
from .sd_dialogue import (
    build_sd_prompt,
    compile_performance_prompt,
    performance_action_only,
    timed_subtitle_pages,
)
from .util import atomic_write_json, media_duration, run


SILENT_ACTION_MARKER = "【无对白动作镜】"


LOCAL_VIDEO_PROMPT_POLICY_REVISION = "h3-drama-v2-camera-contract"


def is_direct_reference_audio_visual_cache(payload: dict) -> bool:
    if any(str(key).startswith("postprocess") for key in payload):
        return False
    backend_identity = "".join(
        character
        for character in json.dumps(payload, ensure_ascii=False).casefold()
        if character.isalnum()
    )
    return "latentsync" not in backend_identity


def copy_keyframe(source: Path, target: Path) -> None:
    """Copy a keyframe together with the provider metadata beside it.

    PhanRouter restores a reference image by task id read from a ``.task.json``
    sidecar next to the keyframe rather than re-uploading the pixels.  A
    keyframe copied without that sidecar therefore cannot be submitted at all,
    which turned every retry into a hard failure and took the episode with it.
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    for suffix in (".task.json", ".request.json", ".local.json"):
        source_sidecar = source.with_suffix(source.suffix + suffix)
        if source_sidecar.is_file():
            shutil.copy2(
                source_sidecar,
                target.with_suffix(target.suffix + suffix),
            )


def policy_safe_motion_prompt(prompt: str) -> str:
    """Generalize IP-specific wording for a prompt-only video retry.

    The locked keyframe still carries the approved character and scene art.
    This changes only remote video conditioning; the renderer continues to
    deliver the exact verified local TTS track and subtitles.
    """

    generalized = prompt
    for source, replacement in (
        ("萧薰儿", "紫衣少女"),
        ("薰儿小姐", "紫衣少女"),
        ("薰儿", "紫衣少女"),
        ("萧炎哥哥", "黑衣青年"),
        ("萧炎", "黑衣青年"),
        ("萧媚", "珊瑚衣少女"),
        ("萧家", "古代家族"),
        ("斗之气", "修炼能量"),
        ("斗气", "修炼能量"),
        ("斗者", "正式修行者"),
        ("测验员", "主持考官"),
    ):
        generalized = generalized.replace(source, replacement)
    generalized = re.sub(
        r"参考音频中的可见对白严格依次为：.*?。"
        r"只有这些可见对白驱动对应角色口型；",
        "人物只做与剧情相符的短句说话动作；",
        generalized,
        flags=re.DOTALL,
    )
    generalized = generalized.replace(
        "参考音频中的旁白、画外对白和内心声期间，",
        "没有可见对白时，",
    )
    return (
        "原创古装奇幻家族考核场景；不使用任何作品名或角色专名。"
        + generalized
    )


def is_real_person_privacy_rejection(error: Exception) -> bool:
    """Match Seedance's specific false positive for photo-like input art."""

    return "InputImageSensitiveContentDetected.PrivacyInformation" in str(error)


def is_image_generation_safety_rejection(error: Exception) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "flagged as sensitive",
            "blocked by safety review",
        )
    )


def anime_privacy_repair_prompt(unit: RuntimeUnit) -> str:
    """Ask the image API for a video-safe redraw without changing the shot."""

    return (
        "把参考图精确重绘成一张明确的原创二维国风电视动画正片截图。"
        "保持原图人物身份、年龄、发型、服装、表情、动作、道具、镜头机位、"
        "人物位置和背景结构不变，不新增或删除人物。"
        "必须使用有粗细变化的清晰手绘轮廓线、纯色平涂、哑光皮肤色块和两级硬边赛璐璐阴影；"
        "五官与手部采用概括的动画形状，明亮青蓝与暖金日光，人物中间调明亮可读。"
        "必须让画面一眼可辨为绘制的二维动画：禁止真人照片、写实皮肤、毛孔、摄影景深、"
        "半写实厚涂、3D、PBR、塑料高光、黏土和玩偶质感。"
        "内容保持健康克制，不出现血液、伤口、裸露、文字、数字、字幕、Logo或水印。"
        f"剧情和构图锚点：{unit.visual_prompt}"
    )


def anime_image_safety_fallback_prompt(unit: RuntimeUnit) -> str:
    subject = (
        "只显示第一张参考图中的一名当前说话者，胸像或中近景，嘴部无遮挡，"
        "望向画外对话对象，处于开口前一刻"
        if unit.speaking
        else "只显示第一张参考图中的一名主要人物，中景构图，处于下一段剧情动作开始前一刻"
    )
    return (
        "原创古代东方奇幻题材，健康克制的日常室内对话场面。"
        "生成一张明亮高完成度二维国风电视动画正片截图，竖屏9:16。"
        "第一张参考图只锁定主要人物身份、发型和服装；如有第二张参考图，只锁定空场建筑。"
        f"{subject}；姿态平静自然，皮肤和衣物完整洁净，不表现冲突结果。"
        "清晰且有粗细变化的手绘轮廓线，纯色平涂，两级硬边赛璐璐阴影，"
        "暖白浅金日光，脸部和服装中间调明亮可读。"
        "不得照抄人物设定图姿势，不新增人物、分身或背景人脸。"
        "禁止真人照片、写实皮肤、毛孔、摄影景深、半写实厚涂、3D、PBR、塑料高光、"
        "黏土、暴力、伤口、裸露、文字、数字、字幕、Logo或水印。"
    )


def _compact_prompt_clause(value: str | None, limit: int) -> str:
    text = re.sub(r"\s+", "", value or "").strip("；。")
    for boilerplate in (
        "，内容健康克制，无血腥、无裸露、无政治符号",
        "内容健康克制，无血腥、无裸露、无政治符号",
    ):
        text = text.replace(boilerplate, "")
    text = re.sub(r"[。；，,]+$", "", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_3d_guoman_unit(unit: RuntimeUnit) -> bool:
    style_text = " ".join(
        (
            unit.keyframe_prompt,
            unit.visual_prompt,
            unit.actor_description or "",
        )
    ).casefold()
    return any(
        marker in style_text
        for marker in (
            "3d国漫",
            "3d donghua",
            "3d动画",
            "三维国漫",
            "toon-pbr",
            "3d_guoman_rendering",
        )
    )


def _location_prompt_context(unit: RuntimeUnit) -> str:
    name = _compact_prompt_clause(unit.location_name or unit.location_asset_id, 36)
    if "大厅" in name:
        return "古代家族议事大厅室内"
    if "房间" in name:
        return "古代家族私人房间室内"
    if any(token in name for token in ("药材店", "店内")):
        return "古代城镇药材店室内"
    if "广场" in name:
        return "古代家族广场室外"
    if "大街" in name:
        return "古代城镇大街室外"
    if any(token in name for token in ("山崖", "草地")):
        return "山崖草地室外"
    if "室内" in name:
        return "当前登记场景室内"
    if "室外" in name:
        return "当前登记场景室外"
    return name


def _visible_performance_action(unit: RuntimeUnit) -> str:
    if unit.action_physics_plan is not None:
        physics = unit.action_physics_plan
        return _compact_prompt_clause(
            f"{physics.preparation}；{physics.force}；{physics.contact}；"
            f"{physics.reaction}；{physics.settling}",
            110,
        )
    action = _compact_prompt_clause(
        performance_action_only(unit.motion_instruction or unit.motion_prompt),
        82,
    )
    concrete_tokens = (
        "抬", "转", "指", "翻", "握", "松", "走", "退", "起身", "坐下",
        "拿", "放", "推", "拉", "触", "停", "闭", "看向", "移向",
    )
    abstract_tokens = ("内心", "意识", "感到", "渴望", "确认", "认为")
    if any(token in action for token in concrete_tokens) and not any(
        token in action for token in abstract_tokens
    ):
        return action
    emotion = unit.emotion
    if any(token in emotion for token in ("怒", "屈辱", "不甘")):
        return "目光停在画外对象，下颌收紧，眉间轻压，不增加手势"
    if any(token in emotion for token in ("震惊", "惊讶")):
        return "听见关键信息后短暂停顿，眼睑张开，肩线只后撤一次"
    if any(token in emotion for token in ("疑惑", "好奇")):
        return "视线从当前物件移向画外对象，眉间轻抬后停住"
    if any(token in emotion for token in ("安慰", "温柔")):
        return "保持柔和视线，语句结束时轻微放松肩线"
    if any(token in emotion for token in ("紧张", "警惕")):
        return "先短暂停顿，视线锁住画外目标，呼吸变浅后稳定"
    return action or "视线发生一次有明确目标的变化，随后稳定停住"


def _visible_close_state(unit: RuntimeUnit) -> str:
    emotion = unit.emotion
    if any(token in emotion for token in ("怒", "屈辱", "不甘")):
        return "下颌保持收紧，目光停住，嘴巴闭合"
    if any(token in emotion for token in ("震惊", "惊讶")):
        return "视线固定在信息来源，肩线停止后撤，嘴巴闭合"
    if any(token in emotion for token in ("疑惑", "好奇")):
        return "眉间轻抬，视线停在画外对象，嘴巴闭合"
    if any(token in emotion for token in ("安慰", "温柔")):
        return "视线保持柔和，肩线放松，嘴巴闭合"
    return "主要动作收住，视线稳定，嘴巴闭合"


def compile_phanrouter_runtime_motion_prompt(unit: RuntimeUnit) -> str:
    """Compile the internal directing contract into one Seedance-sized prompt."""

    duration = min(14.0, max(4.0, float(unit.audio_seconds or 0.0) + 0.5))
    composition = _compact_prompt_clause(
        unit.composition_prompt or "竖屏中近景，主体位于三分线，视线朝向画内",
        72,
    )
    scene = _compact_prompt_clause(unit.visual_prompt, 84)
    action = _visible_performance_action(unit)
    end_state = _compact_prompt_clause(
        unit.performance_plan.end_state if unit.performance_plan is not None else "动作收住并稳定停留",
        48,
    )
    if any(
        token in end_state
        for token in ("内心", "意识", "感到", "渴望", "确认", "认为", "脸色一变")
    ):
        end_state = _visible_close_state(unit)
    camera_text = "摄影机锁定，不切镜"
    if unit.camera_plan is not None and unit.camera_plan.mode != "locked":
        camera_contract = "".join(
            beat.trajectory for beat in unit.camera_plan.camera_beats
        )
        if any(token in camera_contract for token in ("推近", "推进")):
            camera_text = "摄影机只做一次约5%的极慢推近，完成后停住"
        elif "横移" in camera_contract:
            camera_text = "摄影机只做一次短距离缓慢横移，完成后停住"
        else:
            camera_text = "摄影机只做一次轻微重新构图，完成后停住"
    rendering = (
        "高品质风格化中国3D国漫动画，保持角色图与场景图的雕塑式造型、哑光Toon-PBR材质、服装和光线"
        if _is_3d_guoman_unit(unit)
        else "明亮二维国风赛璐璐动画，保持角色图与场景图的线稿、平涂、服装和白昼光线"
    )
    base = (
        f"{duration:.1f}秒竖屏，{rendering}。场景必须是{_location_prompt_context(unit)}。构图：{composition}。场面：{scene}。"
        f"唯一主要动作：{action}；结尾：{end_state}，稳定停留0.4秒。{camera_text}。"
    )
    if unit.speaking:
        audio = (
            f"外部音频是唯一口型、呼吸和节奏时间轴；只有{unit.speaker_name}开口，"
            f"原句：‘{unit.text}’；说完自然闭嘴。"
        )
    else:
        audio = "声音为旁白、画外声或内心声，画面内所有人物全程闭嘴。"
    return (
        base
        + audio
        + "保持身份、服装、人物数量、场景布局和屏幕方向；无额外手势、无新增人物、"
        "无切镜、无夜景、无真人照片、无文字。"
    )


def compile_seedance_native_audio_prompt(unit: RuntimeUnit) -> str:
    prompt = compile_phanrouter_runtime_motion_prompt(unit)
    if unit.speaking:
        old = (
            f"外部音频是唯一口型、呼吸和节奏时间轴；只有{unit.speaker_name}开口，"
            f"原句：‘{unit.text}’；说完自然闭嘴。"
        )
        new = (
            f"Seedance自行生成与人物一致的声音和当前空间环境声；只有{unit.speaker_name}开口，"
            f"直接自然说原句：‘{unit.text}’；不加词，句末闭嘴。"
        )
    else:
        old = "声音为旁白、画外声或内心声，画面内所有人物全程闭嘴。"
        new = (
            f"Seedance自行生成旁白、画外声或内心声，准确表达：‘{unit.text}’；"
            "画面内所有人物全程闭嘴，声音内容不视觉化。"
        )
    return prompt.replace(old, new)


def keyframe_brightness_report(image: Path, location_reference: Path) -> dict:
    """Reject a dark reinterpretation only when the locked location is bright."""

    def luma(path: Path) -> float | None:
        try:
            with Image.open(path) as source:
                rgb = source.convert("RGB").resize((64, 64))
                red, green, blue = ImageStat.Stat(rgb).mean
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue
        except (OSError, ValueError):
            return None

    image_luma = luma(image)
    reference_luma = luma(location_reference)
    threshold = reference_luma * 0.55 if reference_luma is not None else None
    applicable = reference_luma is not None and reference_luma >= 90.0
    passed = (
        image_luma is not None
        and reference_luma is not None
        and (not applicable or image_luma >= float(threshold))
    )
    return {
        "status": "passed" if passed else "failed",
        "applicable": applicable,
        "image_luma": round(image_luma, 3) if image_luma is not None else None,
        "location_reference_luma": (
            round(reference_luma, 3) if reference_luma is not None else None
        ),
        "minimum_luma": round(float(threshold), 3) if applicable else None,
        "policy": "bright-location-relative-luma-v1",
    }


def generation_iteration_record(
    *,
    unit_id: str,
    prompt_sha256: str,
    batch_id: str,
    failure_codes: list[str],
    responsibility_layer: str,
    changed_variables: list[str],
    hypothesis: str,
    expected_improvement: str,
    decision: str,
    next_action: str,
) -> dict:
    """Use the Hell-Grind iteration vocabulary without adding parallel CSV state."""

    return {
        "iteration_id": f"ITER-{unit_id}-{batch_id}",
        "shot_id": unit_id,
        "prompt_id": f"{unit_id}-P-{prompt_sha256[:8]}",
        "batch_id": batch_id,
        "observed_failure_codes": failure_codes,
        "responsibility_layer": responsibility_layer,
        "changed_variables": changed_variables,
        "hypothesis": hypothesis,
        "expected_improvement": expected_improvement,
        "result_generation_ids": [],
        "decision": decision,
        "next_action": next_action,
    }


_OFFSCREEN_VISUAL_MARKERS = (
    "画外",
    "不入前景",
    "不入画",
    "镜外",
    "画面外",
    "只作为视线对象",
)
_MULTI_FOREGROUND_MARKERS = (
    "双人同框",
    "两人同框",
    "多人同框",
    "三人同框",
    "双人镜头",
    "三人镜头",
)


def _direct_video_character_ids(units: list[RuntimeUnit]) -> list[str]:
    """Return one visibly speaking identity only when the scene is explicit.

    A narration turn may reference an off-screen relationship target and thus
    legitimately carry more character asset IDs than the frame contains.  We
    keep those IDs for provenance and Qwen fallback, but they must not disable
    the H3 character + empty-location path when the shot explicitly says that
    the extra character stays outside the frame.
    """

    visible_speaker_ids = list(
        dict.fromkeys(
            unit.character_asset_ids[0]
            for unit in units
            if unit.speaking and unit.character_asset_ids
        )
    )
    if len(visible_speaker_ids) != 1:
        return []
    visible_id = visible_speaker_ids[0]
    for unit in units:
        other_ids = [
            asset_id
            for asset_id in unit.character_asset_ids
            if asset_id != visible_id
        ]
        if not other_ids:
            continue
        visual = unit.visual_prompt
        if (
            not any(marker in visual for marker in _OFFSCREEN_VISUAL_MARKERS)
            or any(marker in visual for marker in _MULTI_FOREGROUND_MARKERS)
        ):
            return []
    return [visible_id]


def _visual_delivery_signature(unit: RuntimeUnit) -> tuple[str, str]:
    """Identify who, if anyone, may drive the visible performance."""

    if unit.speaking:
        return ("visible_dialogue", unit.speaker_name)
    if unit.role == "narrator":
        return ("narration", "narrator")
    return (str(unit.delivery_mode), unit.speaker_name)


def _build_shot_contract(
    *,
    units: list[RuntimeUnit],
    duration_seconds: float,
    camera_plan,
    spatial_anchor: str,
    series_assets: SeriesAssetManifest | None = None,
) -> ShotContract:
    first = units[0]
    visible_units = [unit for unit in units if unit.speaking]
    story_keyframe = any(
        unit.visual_strategy == VisualStrategy.STORY_KEYFRAME for unit in units
    )
    visible_ids = list(
        dict.fromkeys(
            asset_id
            for unit in (units if story_keyframe else visible_units)
            for asset_id in (
                unit.character_asset_ids
                if story_keyframe
                else unit.character_asset_ids[:1]
            )
        )
    )
    reference_character_ids = list(
        dict.fromkeys(
            asset_id
            for unit in units
            for asset_id in unit.character_asset_ids
        )
    )
    if not story_keyframe:
        reference_character_ids = (
            reference_character_ids[:1]
            if visible_units
            else reference_character_ids[:2]
        )
    audible_roles = list(dict.fromkeys(unit.speaker_name for unit in units))
    version_by_id = {
        record.asset_id: record.version
        for record in (
            [*series_assets.characters, *series_assets.locations]
            if series_assets is not None
            else []
        )
    }
    state_by_id = {
        record.asset_id: record.state_variables
        for record in (
            [*series_assets.characters, *series_assets.locations]
            if series_assets is not None
            else []
        )
    }
    visible_version_ids = [
        f"{asset_id}@{version_by_id.get(asset_id, 'v001')}"
        for asset_id in visible_ids
    ]
    reference_character_version_ids = [
        f"{asset_id}@{version_by_id.get(asset_id, 'v001')}"
        for asset_id in reference_character_ids
    ]
    location_version_id = (
        f"{first.location_asset_id}@"
        f"{version_by_id.get(first.location_asset_id, 'v001')}"
    )
    rendering_scope = (
        "3d_guoman_rendering" if _is_3d_guoman_unit(first) else "2d_rendering"
    )
    reference_scopes = [
        ReferenceScope(
            reference_id=version_id,
            kind="character",
            inherit=["identity", "hair", "costume", rendering_scope],
            exclude=["pose", "composition", "camera", "background", "lighting"],
        )
        for version_id in reference_character_version_ids
    ]
    reference_scopes.append(
        ReferenceScope(
            reference_id=location_version_id,
            kind="location",
            inherit=["architecture", "space", "color", "daylight", rendering_scope],
            exclude=["foreground_people", "composition", "camera", "text"],
        )
    )
    performance = next(
        (unit.performance_plan for unit in units if unit.performance_plan is not None),
        None,
    )
    physics = next(
        (
            unit.action_physics_plan
            for unit in units
            if unit.action_physics_plan is not None
        ),
        None,
    )
    source_beats = list(performance.motion_beats) if performance is not None else []
    physics_beats = (
        [
            (physics.trigger, physics.preparation, "", physics.preparation),
            (
                "准备完成",
                f"{physics.force}；{physics.contact}",
                physics.reaction,
                physics.contact,
            ),
            (
                "接触发生",
                physics.reaction,
                "；".join(physics.environment_feedback),
                physics.reaction,
            ),
            (
                "反作用结束",
                physics.settling,
                "；".join(physics.environment_feedback),
                physics.settling,
            ),
        ]
        if physics is not None
        else []
    )
    # Performance beats are the story. Physics may add contact/inertia detail,
    # but must never replace the causal action chain supplied by the shot.
    beat_count = max(1, len(source_beats) or len(physics_beats))
    timing_actions = [
        (
            source_beats[index].action
            if source_beats
            else physics_beats[index][1]
            if physics_beats
            else first.motion_instruction or first.motion_prompt
        )
        for index in range(beat_count)
    ]
    timing_weights = []
    for action in timing_actions:
        weight = 0.65 + min(0.8, len(action) / 32.0)
        if any(token in action for token in ("走", "跑", "追", "绕", "穿过", "转身")):
            weight += 0.45
        if any(token in action for token in ("按", "握", "挡", "行礼", "回头")):
            weight += 0.25
        timing_weights.append(weight)
    timing_total = sum(timing_weights) or 1.0
    timing_cursor = 0.0
    beats: list[ShotContractBeat] = []
    for index in range(beat_count):
        start = timing_cursor
        end = (
            duration_seconds
            if index == beat_count - 1
            else start + duration_seconds * timing_weights[index] / timing_total
        )
        timing_cursor = end
        beat = source_beats[index] if source_beats else None
        physics_beat = (
            physics_beats[min(index, len(physics_beats) - 1)]
            if physics_beats and not source_beats
            else None
        )
        beat_action = (
            physics_beat[1]
            if physics_beat is not None
            else (
                beat.action
                if beat is not None
                else performance_action_only(first.motion_instruction or first.motion_prompt)
            )
        )
        if any(
            token in beat_action
            for token in ("内心", "意识", "感到", "渴望", "确认", "认为")
        ):
            beat_action = _visible_performance_action(first)
        physics_supplement = ""
        if physics is not None and source_beats:
            if index == 0:
                physics_supplement = physics.preparation
            elif index == len(source_beats) - 1:
                physics_supplement = "；".join(
                    [physics.settling, *physics.environment_feedback]
                )
            else:
                physics_supplement = "；".join(
                    item for item in (physics.contact, physics.reaction) if item
                )
        reaction = (
            physics_beat[2]
            if physics_beat is not None
            else beat.reaction if beat is not None else ""
        )
        if physics_supplement:
            reaction = "；".join(
                item for item in (reaction, f"物理反馈：{physics_supplement}") if item
            )
        beats.append(
            ShotContractBeat(
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                actor_or_source=(
                    visible_units[0].speaker_name
                    if visible_units
                    else "画面主要角色" if first.character_asset_ids else "环境"
                ),
                trigger=(
                    physics_beat[0]
                    if physics_beat is not None
                    else beat.trigger if beat is not None else "镜头开始"
                ),
                action=beat_action,
                reaction=reaction,
                end_state=(
                    physics_beat[3]
                    if physics_beat is not None
                    else beat.expression_transition if beat is not None else "动作落定"
                ),
            )
        )
    camera_path = "；".join(beat.trajectory for beat in camera_plan.camera_beats)
    exact_dialogue = [
        f"{unit.speaker_name}：{unit.text}" for unit in units if unit.speaking
    ]
    change_actions = list(
        dict.fromkeys(
            beat.action for beat in beats if beat.action
        )
    )[:5]
    risks = ["F-ID-DRIFT", "F-COLOR-DRIFT"]
    if visible_units:
        risks.insert(1, "F-LIPSYNC")
    else:
        risks.insert(1, "F-DIALOGUE-VISUALIZED")
    open_state = (
        performance.start_state
        if performance is not None
        else "人物和场景处于主要动作开始前一瞬"
    )
    close_state = (
        performance.end_state if performance is not None else "主要动作完成并稳定停留"
    )
    vague_state_tokens = ("内心", "意识", "感到", "渴望", "确认", "认为", "脸色一变")
    if any(token in open_state for token in vague_state_tokens):
        open_state = "人物保持既定构图和视线方向，嘴巴闭合，主要反应尚未发生"
    if any(token in close_state for token in vague_state_tokens):
        close_state = _visible_close_state(first)
    return ShotContract(
        narrative_goal=(
            f"{first.shot_intent.dramatic_function}：{first.shot_intent.viewer_focus}"
        ),
        duration_seconds=round(duration_seconds, 3),
        visible_asset_ids=visible_version_ids,
        audible_roles=audible_roles,
        reference_scopes=reference_scopes,
        open_state=open_state,
        beat_timeline=beats,
        close_state=close_state,
        camera_start=camera_plan.start_position,
        camera_path=camera_path,
        camera_end=camera_plan.end_position,
        continuity_in=spatial_anchor,
        continuity_out=(
            f"{spatial_anchor}；尾帧状态："
            + close_state
        ),
        must_hold=(
            [
                f"角色资产：{','.join(visible_version_ids) or '无可见说话者'}",
                f"场景资产：{location_version_id}",
                camera_plan.screen_direction,
                "外部锁定音频与字幕文本不变",
            ]
            + [
                f"{asset_id}当前状态："
                + json.dumps(state_by_id.get(asset_id, {}), ensure_ascii=False, sort_keys=True)
                for asset_id in [*reference_character_ids, first.location_asset_id]
                if state_by_id.get(asset_id)
            ]
        )[:8],
        changes_here=change_actions,
        must_not_appear=(
            ["额外人物或分身", "非说话者口型", "夜景或真人皮肤", "画内文字"]
            if visible_units
            else ["旁白或内心声被视觉化", "额外人物或分身", "夜景或真人皮肤", "画内文字"]
        ),
        risk_focus=risks[:3],
        exact_dialogue=exact_dialogue,
    )


def _build_sequence_contract(
    *,
    plan: ProductionPlan,
    episode_plan: EpisodePlan,
    groups: list[RuntimeVisualGroup],
    units_by_id: dict[str, RuntimeUnit],
) -> EpisodeSequenceContract:
    narrative_progression = [
        group.shot_contract.narrative_goal
        for group in groups
        if group.shot_contract is not None
    ]
    camera_rhythm = []
    coverage_rhythm = []
    visual_motifs = []
    for group in groups:
        first = units_by_id[group.unit_ids[0]]
        mode = first.camera_plan.mode if first.camera_plan is not None else "locked"
        camera_rhythm.append(f"{group.group_id}:{mode}")
        function = first.shot_intent.dramatic_function
        if first.speaking:
            coverage = f"speaker_closeup:{first.speaker_name}"
        elif function == "establish":
            coverage = "establishing_or_spatial_context"
        elif function == "reaction":
            coverage = "silent_reaction"
        elif function in {"reveal", "payoff"}:
            coverage = "insert_or_result_reveal"
        else:
            coverage = "closed_mouth_story_broll"
        coverage_rhythm.append(f"{group.group_id}:{coverage}")
        focus = _compact_prompt_clause(first.shot_intent.viewer_focus, 72)
        if focus and focus not in visual_motifs:
            visual_motifs.append(focus)
    last_contract = groups[-1].shot_contract
    final_state = (
        last_contract.close_state if last_contract is not None else "最后一个动作落定"
    )
    return EpisodeSequenceContract(
        sequence_goal=episode_plan.summary,
        exact_generation_count=len(groups),
        ordered_group_ids=[group.group_id for group in groups],
        narrative_progression=narrative_progression,
        visual_motifs=visual_motifs[:8],
        coverage_rhythm=coverage_rhythm,
        camera_rhythm=camera_rhythm,
        lighting_continuity=(
            f"全章沿用同一场次真实光源和曝光重点；色彩母题：{plan.palette or '沿用场景资产'}"
        ),
        audio_continuity=(
            "外部锁定音频是对白、呼吸和节奏的唯一时间轴；同场环境底床跨切连续，"
            "不同说话者用硬切或声音桥连接。"
        ),
        transition_rules=[
            "不同可见说话者必须切为独立生成镜头",
            "上一镜close_state成为下一镜continuity_in",
            "同场对话保持行动轴与屏幕方向",
            "对白提及的过去或画外人物不得自动视觉化",
        ],
        final_landing=f"{final_state}；下一叙事承诺：{episode_plan.next_preview}",
    )


def _build_image_contract(
    *,
    units: list[RuntimeUnit],
    shot_contract: ShotContract,
    plan: ProductionPlan,
) -> ImagePromptContract:
    first = units[0]
    scene = next(
        (scene for scene in plan.scenes if scene.scene_id == first.scene_id),
        None,
    )
    spatial_anchors = (
        scene.spatial_contract.anchor_objects[:4]
        if scene is not None and scene.spatial_contract is not None
        else []
    )
    visible_count = min(6, len(shot_contract.visible_asset_ids))
    location_scope = next(
        scope for scope in shot_contract.reference_scopes if scope.kind == "location"
    )
    return ImagePromptContract(
        exact_subject_count=min(6, visible_count),
        subject_asset_version_ids=shot_contract.visible_asset_ids,
        location_asset_version_id=location_scope.reference_id,
        reference_scopes=shot_contract.reference_scopes,
        spatial_anchors=spatial_anchors,
        action_moment=shot_contract.open_state,
        composition=(
            first.composition_prompt
            or "竖屏中景，主体与前中后景关系清楚"
        ),
        perspective_focus=(
            "自然透视；焦点锁定当前可见说话者的脸和嘴"
            if any(unit.speaking for unit in units)
            else "自然透视；焦点锁定当前主要动作、道具或环境变化"
        ),
        lighting=(
            f"沿用{scene.spatial_contract.lighting_source if scene is not None and scene.spatial_contract is not None else '批准场景光源与方向'}；"
            f"曝光保护主体中间调；{plan.palette or '保持场景色温'}"
        ),
        color_material=(
            f"{plan.visual_style or '二维国风赛璐璐'}；人物、布料、木构和金属保持各自可读响应"
        ),
        risk_focus=shot_contract.risk_focus,
    )


def build_visual_groups(
    plan: ProductionPlan,
    *,
    series_assets: SeriesAssetManifest | None = None,
    target_seconds: float = 13.4,
    max_speed: float = 1.0,
    gap: float = 0.10,
    allow_cross_shot_merge: bool = False,
) -> list[RuntimeVisualGroup]:
    """Pack only turns that one generated performance can execute reliably."""
    limit = target_seconds * max_speed
    packed: list[list[RuntimeUnit]] = []
    current: list[RuntimeUnit] = []
    current_seconds = 0.0
    for unit in plan.units:
        seconds = float(unit.audio_seconds or 0.0)
        addition = seconds + (gap if current else 0.0)
        if current and (
            unit.shot_id != current[-1].shot_id
            or _visual_delivery_signature(unit)
            != _visual_delivery_signature(current[-1])
            or current_seconds + addition > limit
        ):
            packed.append(current)
            current = []
            current_seconds = 0.0
            addition = seconds
        current.append(unit)
        current_seconds += addition
    if current:
        packed.append(current)

    # Merge only genuinely short adjacent shots. This removes one-second cuts
    # while keeping long narrative beats as their own directed performances.
    merged: list[list[RuntimeUnit]] = []
    for group in packed:
        if allow_cross_shot_merge and merged:
            left = merged[-1]
            left_seconds = sum(float(unit.audio_seconds or 0.0) for unit in left) + gap * (len(left) - 1)
            right_seconds = sum(float(unit.audio_seconds or 0.0) for unit in group) + gap * (len(group) - 1)
            combined_shots = list(dict.fromkeys(unit.shot_id for unit in left + group))
            if (
                left[-1].scene_id == group[0].scene_id
                and _visual_delivery_signature(left[-1])
                == _visual_delivery_signature(group[0])
                and (left_seconds < 4.0 or right_seconds < 4.0)
                and left_seconds + right_seconds + gap <= limit
                and len(combined_shots) <= 4
            ):
                left.extend(group)
                continue
        merged.append(list(group))

    groups: list[RuntimeVisualGroup] = []
    for index, units in enumerate(merged, start=1):
        group_id = f"visual_{index:03d}"
        shot_ids = list(dict.fromkeys(unit.shot_id for unit in units))
        character_ids = list(
            dict.fromkeys(asset_id for unit in units for asset_id in unit.character_asset_ids)
        )
        direct_video_character_ids = _direct_video_character_ids(units)
        keyframe_reasons = list(
            dict.fromkeys(
                reason for unit in units for reason in unit.keyframe_reasons
            )
        )
        if any(unit.visual_strategy == VisualStrategy.STORY_KEYFRAME for unit in units):
            visual_strategy = VisualStrategy.STORY_KEYFRAME
        elif direct_video_character_ids:
            visual_strategy = VisualStrategy.DIRECT_ASSETS
        elif not character_ids:
            visual_strategy = VisualStrategy.SCENE_ONLY
        else:
            visual_strategy = VisualStrategy.AUTO
        visuals = list(dict.fromkeys(unit.visual_prompt for unit in units))
        actions = list(
            dict.fromkeys(
                performance_action_only(unit.motion_instruction)
                for unit in units
                if unit.motion_instruction
            )
        )
        spoken = [f"{unit.speaker_name}：{unit.text}" for unit in units if unit.speaking]
        nonvisible_character_voice = [
            f"{unit.speaker_name}：{unit.text}"
            for unit in units
            if not unit.speaking and unit.role != "narrator"
        ]
        ambience = list(
            dict.fromkeys(
                unit.audio_plan.ambience
                for unit in units
                if unit.audio_plan.ambience
            )
        )
        music_cues = list(
            dict.fromkeys(
                unit.audio_plan.music_cue
                for unit in units
                if unit.audio_plan.music_cue
            )
        )
        sfx_events = list(
            dict.fromkeys(
                event
                for unit in units
                for event in unit.audio_plan.sfx_events
            )
        )
        shot_intents = list(
            dict.fromkeys(
                f"{unit.shot_id}功能={unit.shot_intent.dramatic_function}，"
                f"权力关系={unit.shot_intent.power_relation}，"
                f"目标情绪={unit.shot_intent.emotion_target}，"
                f"观众焦点={unit.shot_intent.viewer_focus}，"
                f"留存节点={unit.shot_intent.retention_beat_id or '未绑定'}"
                for unit in units
            )
        )
        audio_timeline = list(
            dict.fromkeys(
                f"{unit.shot_id}@{beat.position_ratio:.0%} {beat.cue_type}："
                f"{beat.cue}，触发={beat.trigger}"
                for unit in units
                for beat in unit.audio_plan.audio_beats
            )
        )
        delivery_seconds = min(
            14.0,
            max(
                0.8,
                sum(float(unit.audio_seconds or 0.0) for unit in units)
                + gap * max(0, len(units) - 1)
                + 0.2,
            ),
        )
        visible_speaker = next((unit for unit in units if unit.speaking), None)
        framing_instruction = (
            visible_speaker.composition_prompt
            if visible_speaker is not None
            else units[0].composition_prompt
        )
        if (
            visible_speaker is not None
            and visual_strategy == VisualStrategy.STORY_KEYFRAME
            and len(character_ids) >= 2
        ):
            subject_instruction = (
                f"恰好保留{len(character_ids)}名已绑定具名角色；"
                f"可见说话者{visible_speaker.speaker_name}的完整嘴部无遮挡，"
                "其他人物闭嘴并只完成本镜走位或反应，不得新增群众脸或交换身份。"
            )
        elif visible_speaker is not None:
            subject_instruction = (
                f"可见说话者{visible_speaker.speaker_name}必须清楚位于这个构图的竖屏安全区，"
                "完整嘴部无遮挡；最终画面只保留这一名人物。对话对象、长辈、群众与其他具名角色"
                "全部留在画外，用该角色的视线、姿态和环境空间表达关系，不得生成背景人脸。"
            )
        else:
            subject_instruction = (
                "本镜主体必须由当前动作和情绪决定，不得把母版中的全部人物机械地重复画进前景。"
            )
        performance_plans: list[str] = []
        seen_performances: set[str] = set()
        for unit in units:
            if unit.performance_plan is None:
                continue
            identity = unit.shot_id + unit.performance_plan.model_dump_json()
            if identity in seen_performances:
                continue
            seen_performances.add(identity)
            shot_seconds = sum(
                float(row.audio_seconds or 0.0) for row in units if row.shot_id == unit.shot_id
            ) + gap * max(0, sum(row.shot_id == unit.shot_id for row in units) - 1)
            performance_plans.append(
                f"{unit.shot_id}："
                + compile_performance_prompt(
                    unit.performance_plan,
                    duration=max(0.8, min(14.0, shot_seconds + 0.2)),
                )
            )
        moving_candidates = [
            unit.camera_plan
            for unit in units
            if unit.camera_plan is not None and unit.camera_plan.mode != "locked"
        ]
        selected_camera = (
            max(moving_candidates, key=lambda plan: _camera_mode_rank(plan.mode))
            if moving_candidates
            else _locked_group_camera_plan(
                next((unit.camera_plan for unit in units if unit.camera_plan is not None), None)
            )
        )
        camera_directing = _compile_group_camera_prompt(selected_camera)
        spatial_anchor = (
            f"{units[0].scene_id}固定空间轴线：建筑、核心道具、光线方向和时间状态不得改变；"
            "人物在明确走动前保持首次建立的左右关系和前后距离；同一角色只出现一次；"
            "摄影机无论固定或移动都必须留在同一侧轴线，禁止镜像翻转、凭空换位或重置群众站位。"
        )
        style_direction = (
            f"视觉风格：{plan.visual_style}。色彩与光影：{plan.palette}。"
            if plan.visual_style or plan.palette
            else ""
        )
        keyframe_contract = (
            "【剧情锚点关键帧】原因："
            + "、".join(keyframe_reasons or ["连续镜头关键构图"])
            + "。"
            if visual_strategy == VisualStrategy.STORY_KEYFRAME
            else "【自适应视觉回退帧】仅在视频后端不能直接使用系列人物与空场资产时生成。"
        )
        keyframe_prompt = (
            f"{keyframe_contract}系列风格指纹 {plan.style_fingerprint}。{style_direction}"
            "这是连续长镜头的唯一动作起始帧，不是拼贴图。"
            f"【镜头戏剧意图】{'；'.join(shot_intents)}。"
            f"剧情画面：{'；'.join(visuals)}。{spatial_anchor}"
            "参考母版只锁定角色身份、服装、场景建筑、材质、色彩、光照和行动轴；"
            "绝对不要照抄参考母版的大全景、静态姿势或全员站位画面，必须从行动轴同侧重新取景。"
            f"本镜唯一构图要求：{framing_instruction}。{subject_instruction}"
            "人物位置按动作开始前一刻摆放，为后续连续表演留出空间。"
            "所有人物皮肤和衣物完整洁净；握拳只表现肌肉受力，不得出现血液、伤口、破皮或污渍。"
            "测试碑只保留无字的金色发光纹路，禁止任何可读文字、数字、字幕、气泡、Logo和水印；"
            "禁止多人设定稿、分身和不同画风混杂。"
        )
        motion_prompt = (
            "这是一个完整连续表演镜头，不是多张静态图片串联，也不是蒙太奇。"
            f"按顺序完成剧情动作：{'；'.join(actions or visuals)}。"
            + f"【镜头戏剧意图】{'；'.join(shot_intents)}。"
            + (
                f"【分镜表演计划】{'；'.join(performance_plans)}。"
                if performance_plans
                else ""
            )
            + f"【本连续镜头摄影机计划】{camera_directing}。"
            + (
                f"参考音频中的可见对白严格依次为：{'；'.join(spoken)}。"
                "只有这些可见对白驱动对应角色口型；参考音频中的旁白、画外对白和内心声期间，"
                "画面内所有人物必须保持闭嘴。"
                if spoken
                else "剧情声音全部为画外旁白、画外对白或内心声，画面内所有人物不得随声音做口型。"
            )
            + (
                f"其中画外角色声音依次为：{'；'.join(nonvisible_character_voice)}，不得让画中人物朗读。"
                if nonvisible_character_voice
                else ""
            )
            + (
                "【非语言声音设计】"
                + (f"环境底：{'；'.join(ambience)}。" if ambience else "")
                + (f"音乐提示：{'；'.join(music_cues)}。" if music_cues else "")
                + (f"同步音效：{'、'.join(sfx_events)}。" if sfx_events else "")
                + (f"相对音频节拍：{'；'.join(audio_timeline)}。" if audio_timeline else "")
                + "不得遮住、替换或重复锁定人声；对白出现时背景自动压低。"
                if ambience or music_cues or sfx_events or audio_timeline
                else ""
            )
            + f"{spatial_anchor}"
            f"所有主体动作、表情转折和摄影机运动必须在前{delivery_seconds:.2f}秒内完成并收势；"
            "若模型生成窗口更长，余下时间保持动作终点和稳定构图，不得继续追加动作。"
            "只有在台词、视线目标、道具状态或对方反应形成明确触发时才发生动作变化；"
            "一个节拍只保留一个主要动作，完成后允许短暂停顿，不得用无意义小动作填满时长；"
            "整组只执行上面唯一的摄影机计划，不得叠加其他推拉、横移、环绕、升降或数字缩放。"
            "眼睛先于头部，头部先于肩膀，衣发稍后响应；动作有停顿、加速和减速。"
            "参考图只锁定身份、服装、环境和画风，不锁定静态姿势；不得改变人物年龄、服饰和相对站位。"
            "全程不得出现血液、伤口、破皮或新增可读文字。"
        )
        shot_contract = _build_shot_contract(
            units=units,
            duration_seconds=delivery_seconds,
            camera_plan=selected_camera,
            spatial_anchor=spatial_anchor,
            series_assets=series_assets,
        )
        image_contract = _build_image_contract(
            units=units,
            shot_contract=shot_contract,
            plan=plan,
        )
        groups.append(
            RuntimeVisualGroup(
                group_id=group_id,
                scene_id=units[0].scene_id,
                shot_ids=shot_ids,
                unit_ids=[unit.unit_id for unit in units],
                location_asset_id=units[0].location_asset_id,
                character_asset_ids=character_ids,
                direct_video_character_asset_ids=direct_video_character_ids,
                spatial_anchor=spatial_anchor,
                combined_text="".join(unit.text for unit in units),
                keyframe_prompt=keyframe_prompt,
                motion_prompt=motion_prompt,
                audio_path=f"work/visual_group_audio/{group_id}.wav",
                video_audio_path=f"work/visual_group_audio_driver/{group_id}.wav",
                keyframe_path=f"work/visual_group_keyframes/{group_id}.jpeg",
                raw_video_path=f"work/visual_group_video/{group_id}.mp4",
                segment_path=f"work/visual_group_segments/{group_id}.mp4",
                visual_strategy=visual_strategy,
                keyframe_reasons=keyframe_reasons,
                shot_contract=shot_contract,
                image_contract=image_contract,
            )
        )
    return groups


def retime_group_timelines_to_native_audio(
    timings: list[dict],
    *,
    speech_start: float,
    speech_end: float,
) -> list[dict]:
    """Allocate exact subtitle text over measured Seedance speech bounds."""

    copied = [json.loads(json.dumps(timing, ensure_ascii=False)) for timing in timings]
    weights = [
        max(
            1,
            sum(
                len(re.sub(r"\s+", "", str(event.get("text", ""))))
                for event in timing.get("events", [])
            ),
        )
        for timing in copied
    ]
    total_weight = sum(weights)
    span = max(0.1, speech_end - speech_start)
    cursor = speech_start
    for timing, weight in zip(copied, weights, strict=True):
        unit_end = speech_end if timing is copied[-1] else cursor + span * weight / total_weight
        events = timing.get("events", [])
        event_weights = [
            max(1, len(re.sub(r"\s+", "", str(event.get("text", "")))))
            for event in events
        ]
        event_total = sum(event_weights) or 1
        event_cursor = cursor
        for event, event_weight in zip(events, event_weights, strict=True):
            event_end = (
                unit_end
                if event is events[-1]
                else event_cursor + (unit_end - cursor) * event_weight / event_total
            )
            event["start"] = round(event_cursor, 6)
            event["end"] = round(event_end, 6)
            event["alignment_evidence"] = "seedance_native_coarse_audio_bounds"
            event_cursor = event_end
        timing["offset"] = round(cursor, 6)
        timing["speech_start"] = round(cursor, 6)
        timing["speech_end"] = round(unit_end, 6)
        timing["alignment_evidence"] = "seedance_native_coarse_audio_bounds"
        cursor = unit_end
    return copied


def adaptive_tts_speed(
    current_speed: float,
    audio_seconds: float,
    target_seconds: float,
) -> float:
    """Choose the next model-native speed after an overlong TTS attempt."""

    if audio_seconds <= target_seconds + 0.03:
        return current_speed
    required = current_speed * audio_seconds / target_seconds * 1.02
    return min(2.0, max(current_speed + 0.05, required))


def _camera_mode_rank(mode: str) -> int:
    return {
        "locked": 0,
        "motivated_subtle": 1,
        "motivated_emphasis": 2,
    }.get(mode, 0)


def _locked_group_camera_plan(camera_plan):
    from .models import CameraBeat, CameraPlan

    start = camera_plan.start_position if camera_plan is not None else "沿首次建立的行动轴同侧稳定取景"
    axis = camera_plan.action_axis if camera_plan is not None else "沿首次建立的行动轴同侧取景"
    direction = (
        camera_plan.screen_direction
        if camera_plan is not None
        else "保持人物左右位置、视线和运动方向连续"
    )
    return CameraPlan(
        mode="locked",
        motivation="本连续镜头以人物表演承担动态，避免多条机位轨迹互相冲突",
        action_axis=axis,
        screen_direction=direction,
        start_position=start,
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory="锁定机位，摄影机全程保持完全静止",
                framing="人物通过视线、手势、身体重心和画内走位完成表演",
                parallax="不制造摄影机视差，前中远景与环境锚点保持固定",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="继续锁定机位，让最终动作和表情停留一拍",
                framing="不重新构图、不推拉、不环绕",
                parallax="背景结构、人物屏幕位置和行动轴保持稳定",
            ),
        ],
        end_position="与起始位置相同的稳定机位",
    )


def _compile_group_camera_prompt(camera_plan) -> str:
    beats = "；".join(
        f"轨迹={beat.trajectory}，构图={beat.framing}，空间={beat.parallax}"
        for beat in camera_plan.camera_beats
    )
    return (
        f"模式={camera_plan.mode}；动机={camera_plan.motivation}；"
        f"行动轴={camera_plan.action_axis}；屏幕方向={camera_plan.screen_direction}；"
        f"起点={camera_plan.start_position}；{beats}；终点={camera_plan.end_position}"
    )


class EpisodeProductionRuntime:
    def __init__(
        self,
        settings: Settings,
        media: MediaProvider,
        renderer: Renderer,
        assets: SeriesAssetFactory,
        evidence: RuntimeEvidenceBackends,
    ):
        self.settings = settings
        self.media = media
        self.renderer = renderer
        self.assets = assets
        self.evidence = evidence

    @staticmethod
    def _resolve(episode_dir: Path, path: str) -> Path:
        return episode_dir / path

    @staticmethod
    def _cover_title(source_title: str, video_title: str) -> str:
        """Prefer the source chapter title while removing only its ordinal prefix."""
        chapter_prefix = re.compile(
            r"^\s*(?:第[零〇一二三四五六七八九十百千万两\d]+[章节卷回集]|"
            r"chapter\s+\d+)\s*[:：\-—、.]?\s*",
            re.IGNORECASE,
        )
        source_candidate = chapter_prefix.sub("", source_title).strip()
        if source_candidate:
            return source_candidate
        video_candidate = video_title.rsplit("：", 1)[-1].rsplit(":", 1)[-1]
        video_candidate = chapter_prefix.sub("", video_candidate).strip()
        return video_candidate or source_title.strip() or "本集故事"

    @staticmethod
    def _select_cover_unit(plan: ProductionPlan) -> RuntimeUnit:
        """Choose an early, character-rich shot instead of blindly using frame one."""
        indexed = list(enumerate(plan.units))
        return max(
            indexed,
            key=lambda item: (
                min(len(item[1].character_asset_ids), 2),
                len(item[1].character_asset_ids),
                int(item[1].speaking),
                -item[0],
            ),
        )[1]

    @staticmethod
    def _cover_prompt(
        *,
        bible: StoryBible,
        episode: Episode,
        episode_plan: EpisodePlan,
    ) -> tuple[str, str, str]:
        art_title = EpisodeProductionRuntime._cover_title(
            episode.source_title, episode_plan.video_title
        )
        episode_label = f"第{episode.index:02d}集"
        prompt = (
            "用途：中文竖屏漫剧的独立传播封面无字底图，不是片头卡，也不是普通视频首帧。"
            f"保持参考图人物身份、年龄、发型、服装、场景结构以及{bible.style_fingerprint}画风一致；"
            "允许为封面重新构图，突出人物关系、情绪对峙和故事悬念，人物脸部清楚，主体位于竖屏安全区。"
            f"剧情锚点：{episode_plan.hook}；本集梗概：{episode_plan.summary}。"
            f"整体风格：{bible.visual_style}；色彩与光影：{bible.palette}；9:16国漫画报，高完成度插画。"
            "画面上方约三分之一保留较暗、层次清楚的标题净空，人物面部不得进入该净空。"
            "图像中不得生成任何文字；系列名、艺术标题和集数将在后续由排版引擎精确绘制。"
            "禁止出现旁白、人物台词、对白、字幕、气泡、引号、提示语、Logo、水印、二维码及任何可读文字。"
        )
        return prompt, art_title, episode_label

    def _prepare_cover(
        self,
        *,
        episode_dir: Path,
        plan: ProductionPlan,
        episode: Episode,
        episode_plan: EpisodePlan,
        bible: StoryBible,
        series_assets: SeriesAssetManifest,
        cover: Path,
    ) -> Path:
        reference_unit = self._select_cover_unit(plan)
        protagonist = series_assets.characters[0] if series_assets.characters else None
        reference = (
            episode_dir.parent / protagonist.primary_image
            if protagonist is not None
            else self._resolve(episode_dir, reference_unit.keyframe_path)
        )
        if not reference.is_file():
            reference = self._resolve(episode_dir, reference_unit.keyframe_path)
        prompt, art_title, episode_label = self._cover_prompt(
            bible=bible,
            episode=episode,
            episode_plan=episode_plan,
        )
        generated = self.assets._ensure_image(
            prompt,
            episode_dir / "work" / "cover_art_source.jpeg",
            reference=reference,
        )
        self.renderer.make_cover(
            generated.path,
            cover,
            novel_title=bible.novel_title,
            art_title=art_title,
            episode_label=episode_label,
        )
        atomic_write_json(
            episode_dir / "cover_generation_report.json",
            {
                "strategy": "text-free-generated-plate-deterministic-art-title-v2",
                "image_model": self.settings.image_model,
                "art_title": art_title,
                "series_title": bible.novel_title,
                "episode_label": episode_label,
                "reference_unit_id": reference_unit.unit_id,
                "reference_kind": (
                    "protagonist-reusable-asset" if protagonist is not None else "story-keyframe"
                ),
                "reference_keyframe": Path(
                    os.path.relpath(reference, episode_dir)
                ).as_posix(),
                "reference_sha256": sha256_file(reference),
                "prompt": prompt,
                "prompt_sha256": sha256_text(prompt),
                "output": cover.name,
                "output_sha256": sha256_file(cover),
                "visible_text_policy": "exact-programmatic-art-title-series-title-episode-label-only",
            },
        )
        return cover

    def _prepare_endpoint_cards(
        self,
        *,
        episode_dir: Path,
        plan: ProductionPlan,
        episode: Episode,
        episode_plan: EpisodePlan,
        bible: StoryBible,
        cover: Path,
        ending: Path,
        episode_count: int,
    ) -> tuple[Path, Path]:
        """Use episode artwork for endpoint cards, never asset turnarounds."""
        intro_card = episode_dir / "work" / "series_intro.jpeg"
        self.renderer.normalize_jpeg(cover, intro_card)
        if not plan.units:
            raise ValueError("production plan has no units for ending artwork")
        ending_background = self._resolve(episode_dir, plan.units[-1].keyframe_path)
        ending_copy = episode_plan.next_preview or "敬请期待后续"
        self.renderer.make_card(
            ending_background,
            ending,
            bible.novel_title,
            "未完待续" if episode.index < episode_count else "本集完",
            ending_copy,
        )
        return intro_card, ending_background

    def _audio_identity(self, unit: RuntimeUnit) -> str:
        cache_source_sha256 = None
        cache_dir = os.environ.get("NOVEL_QWEN_TTS_CACHE_DIR")
        if cache_dir:
            cache_source = Path(cache_dir) / f"{unit.unit_id}.wav"
            if cache_source.is_file():
                cache_source_sha256 = sha256_file(cache_source)
        identity_payload = {
            "text": unit.text,
            "voice": unit.voice,
            "emotion": unit.emotion,
            "audio_plan": unit.audio_plan.model_dump(mode="json"),
            "delivery_mode": unit.delivery_mode,
            "tts_model": self.settings.tts_model,
            "tts_command": self.settings.tts_command,
            "tts_speed": self._speech_speed(unit),
            "tts_max_audio_seconds": self._max_turn_audio_seconds(),
            "tts_duration_policy": "indextts-model-native-fit-v1",
            "model_lifecycle_command": self.settings.model_lifecycle_command,
            "provider": self.settings.provider,
            # A command string alone cannot identify a mutable local TTS
            # cache. Include the addressed WAV so speed/padding changes
            # invalidate stale attempts and downstream video.
            "tts_cache_source_sha256": cache_source_sha256,
        }
        identity_payload.update(indextts_synthesis_identity(unit.text))
        return sha256_text(
            json.dumps(identity_payload, ensure_ascii=False, sort_keys=True)
        )

    def _speech_speed(self, unit: RuntimeUnit) -> float | None:
        role_speed = (
            self.settings.tts_narration_speed
            if unit.role == "narrator"
            else self.settings.tts_dialogue_speed
        )
        return role_speed if role_speed is not None else self.settings.tts_speed

    def _max_turn_audio_seconds(self) -> float:
        return min(13.4, self.settings.video_max_seconds - 0.5)

    def _prepare_native_timing_audio(
        self,
        episode_dir: Path,
        unit: RuntimeUnit,
    ) -> tuple[dict, dict]:
        output = self._resolve(episode_dir, unit.audio_path)
        if unit.text == SILENT_ACTION_MARKER:
            actions = (
                [beat.action for beat in unit.performance_plan.motion_beats]
                if unit.performance_plan is not None
                else [unit.motion_instruction]
            )
            displacement = any(
                token in "".join(actions)
                for token in ("走", "跑", "追", "绕", "穿过", "转身")
            )
            seconds = min(
                2.5,
                max(1.5, 0.55 + len(actions) * (0.65 if displacement else 0.48)),
            )
        else:
            visible_chars = len(re.sub(r"\s+", "", unit.text))
            seconds = min(
                self._max_turn_audio_seconds(),
                max(2.0, visible_chars / 4.2 + 0.5),
            )
        if not output.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-t",
                    f"{seconds:.6f}",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
        seconds = media_duration(output)
        speech_start = 0.0 if unit.text == SILENT_ACTION_MARKER else 0.1
        speech_end = (
            0.0
            if unit.text == SILENT_ACTION_MARKER
            else max(speech_start + 0.1, seconds - 0.2)
        )
        events = (
            []
            if unit.text == SILENT_ACTION_MARKER
            else timed_subtitle_pages(unit.text, speech_start, speech_end)
        )
        unit.audio_seconds = round(seconds, 6)
        unit.speech_start = speech_start
        unit.speech_end = speech_end
        unit.subtitle_alignment = (
            "none-silent-action"
            if unit.text == SILENT_ACTION_MARKER
            else "native-video-audio-pending"
        )
        return (
            {
                "unit_id": unit.unit_id,
                "reference": unit.text,
                "hypothesis": "",
                "cer": 999.0,
                "status": "skipped",
                "backend": None,
                "reason": "Seedance native audio preview disables TTS and ASR",
            },
            {
                "unit_id": unit.unit_id,
                "backend": "seedance-native-provisional-timing",
                "evidence": "coarse_text_duration_before_native_audio",
                "speech_start": speech_start,
                "speech_end": speech_end,
                "events": events,
            },
        )

    def _prepare_audio(self, episode_dir: Path, unit: RuntimeUnit) -> tuple[dict, dict]:
        output = self._resolve(episode_dir, unit.audio_path)
        meta = output.with_suffix(output.suffix + ".request.json")
        identity = self._audio_identity(unit)
        selected_path: Path | None = None
        selected_attempt = 0
        selected_asr: dict | None = None
        base_speed = float(self._speech_speed(unit) or 1.0)
        selected_speed = base_speed
        next_speed = base_speed
        max_audio_seconds = self._max_turn_audio_seconds()
        if output.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                selected_path = output
                selected_attempt = int(saved.get("attempt", 0))
                selected_speed = float(saved.get("speed") or base_speed)
                selected_asr = self.evidence.transcribe(unit.unit_id, unit.text, output)
                if not (
                    selected_asr.get("status") == "passed"
                    and float(selected_asr.get("cer", float("inf"))) <= self.settings.max_turn_cer
                    and media_duration(output) <= max_audio_seconds + 0.03
                ):
                    selected_path = None
        attempts = (
            range(1, self.settings.max_unit_attempts + 1)
            if selected_path is None
            else ()
        )
        for attempt in attempts:
            attempt_path = (
                episode_dir
                / "work"
                / "turn_audio_attempts"
                / unit.unit_id
                / identity[:8]
                / f"attempt_{attempt:02d}.wav"
            )
            attempt_meta = attempt_path.with_suffix(".wav.request.json")
            attempt_speed = next_speed
            if attempt_path.is_file() and attempt_meta.is_file():
                attempt_speed = float(
                    json.loads(attempt_meta.read_text(encoding="utf-8")).get(
                        "speed", attempt_speed
                    )
                )
            if not attempt_path.is_file():
                instructions = (
                    f"标准普通话，逐字准确朗读：{unit.text}。人物和专有名词必须准确。"
                    f"表演意图：{unit.audio_plan.delivery_intent or unit.emotion}；"
                    f"语速：{unit.audio_plan.pace}；情绪能量0到1为{unit.audio_plan.energy:.2f}；"
                    + (
                        f"自然停顿位置：{'、'.join(unit.audio_plan.pauses)}；"
                        if unit.audio_plan.pauses
                        else "按语义和标点自然停顿；"
                    )
                    + (
                        "只做画外旁白。"
                        if unit.role == "narrator"
                        else (
                            "这是角色内心声，保持角色音色稳定，像心里完整想完这句话，不做可见口型。"
                            if str(unit.delivery_mode) == "inner_voice"
                            else (
                                "这是画外角色对白，保持角色音色稳定，不做可见口型。"
                                if not unit.speaking
                                else "保持角色音色稳定。"
                            )
                        )
                    )
                )
                self.media.synthesize(
                    unit.text,
                    attempt_path,
                    voice=unit.voice,
                    instructions=instructions,
                    speed=attempt_speed,
                )
                atomic_write_json(
                    attempt_meta,
                    {
                        "speed": attempt_speed,
                        "base_speed": base_speed,
                        "max_audio_seconds": max_audio_seconds,
                        "policy": "indextts-model-native-fit-v1",
                    },
                )
            row = self.evidence.transcribe(unit.unit_id, unit.text, attempt_path)
            attempt_seconds = media_duration(attempt_path)
            selected_path, selected_attempt, selected_asr = attempt_path, attempt, row
            selected_speed = attempt_speed
            if (
                row.get("status") == "passed"
                and float(row.get("cer", float("inf"))) <= self.settings.max_turn_cer
                and attempt_seconds <= max_audio_seconds + 0.03
            ):
                break
            next_speed = adaptive_tts_speed(
                attempt_speed,
                attempt_seconds,
                max_audio_seconds,
            )
        assert selected_path is not None and selected_asr is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        if selected_path.resolve() != output.resolve():
            shutil.copy2(selected_path, output)
        seconds = media_duration(output)
        if seconds > max_audio_seconds + 0.03:
            raise ValueError(
                f"{unit.unit_id} audio duration {seconds:.3f}s exceeds the "
                f"{max_audio_seconds:.3f}s model window after "
                f"{selected_attempt} attempts (last internal speed={selected_speed:.3f})"
            )
        if not (
            selected_asr.get("status") == "passed"
            and float(selected_asr.get("cer", float("inf")))
            <= self.settings.max_turn_cer
        ):
            raise ValueError(
                f"{unit.unit_id} TTS content CER "
                f"{float(selected_asr.get('cer', float('inf'))):.3f} exceeds "
                f"{self.settings.max_turn_cer:.3f} after {selected_attempt} attempts"
            )
        alignment = self.evidence.align(unit.unit_id, unit.text, output)
        unit.attempt = selected_attempt
        unit.audio_seconds = round(seconds, 6)
        unit.speech_start = round(float(alignment["speech_start"]), 6)
        unit.speech_end = round(float(alignment["speech_end"]), 6)
        unit.subtitle_alignment = str(alignment["evidence"])
        directed_seconds = float(math.ceil(min(14.0, max(4.0, seconds + 0.5))))
        unit.motion_prompt = build_sd_prompt(
            unit.speaker_name if unit.speaking else "narrator",
            unit.text,
            unit.motion_instruction,
            use_reference_audio=True,
            actor_description=unit.actor_description,
            composition_prompt=unit.composition_prompt,
            emotion=unit.emotion,
            performance_plan=unit.performance_plan,
            camera_plan=unit.camera_plan,
            shot_intent=unit.shot_intent,
            audio_plan=unit.audio_plan,
            duration=directed_seconds,
        )
        atomic_write_json(
            meta,
            {
                "request_sha256": identity,
                "attempt": selected_attempt,
                "audio_sha256": sha256_file(output),
                "voice": unit.voice,
                "speed": selected_speed,
                "base_speed": base_speed,
                "max_audio_seconds": max_audio_seconds,
                "duration_policy": "indextts-model-native-fit-v1",
                "synthesis_text_policy": (
                    INDEXTTS_SYNTHESIS_TEXT_POLICY
                    if indextts_synthesis_text(unit.text) != unit.text
                    else None
                ),
                "text_sha256": sha256_text(unit.text),
            },
        )
        return selected_asr, alignment

    def _visual_identity(
        self,
        unit: RuntimeUnit,
        audio: Path,
        reference_board: Path,
        additional_references: tuple[Path, ...] = (),
    ) -> str:
        return sha256_text(
            json.dumps(
                {
                    "keyframe_prompt": unit.keyframe_prompt,
                    "motion_prompt": unit.motion_prompt,
                    "audio_sha256": sha256_file(audio),
                    "reference_board_sha256": sha256_file(reference_board),
                    "additional_reference_sha256s": [
                        sha256_file(path) for path in additional_references
                    ],
                    "image_model": self.settings.image_model,
                    "image_transport": (
                        "phanrouter-gpt-image-2"
                        if self.settings.provider == "command"
                        and "".join(
                            character
                            for character in self.settings.image_model.casefold()
                            if character.isalnum()
                        )
                        == "gptimage2"
                        and (
                            self.settings.phanrouter_image_api_key
                            or self.settings.phanrouter_api_key
                        )
                        else self.settings.provider
                    ),
                    "local_image_prompt_policy": self.settings.local_image_prompt_policy,
                    "local_visual_strategy": self.settings.local_visual_strategy,
                    "local_video_prompt_policy_revision": (
                        LOCAL_VIDEO_PROMPT_POLICY_REVISION
                        if self.settings.provider == "command"
                        and "minimaxh3" in "".join(
                            character
                            for character in self.settings.video_model.casefold()
                            if character.isalnum()
                        )
                        else None
                    ),
                    "video_model": self.settings.video_model,
                    "final_audio_policy": self.settings.final_audio_policy,
                    "image_command_sha256": (
                        sha256_text(self.settings.image_command)
                        if self.settings.image_command
                        else None
                    ),
                    "video_command_sha256": (
                        sha256_text(self.settings.video_command)
                        if self.settings.video_command
                        else None
                    ),
                    "model_lifecycle_command_sha256": (
                        sha256_text(self.settings.model_lifecycle_command)
                        if self.settings.model_lifecycle_command
                        else None
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    @staticmethod
    def _two_reference_keyframe_prompt(unit: RuntimeUnit) -> str:
        if _is_3d_guoman_unit(unit):
            return (
                "Qwen Image Edit双参考3D国漫剧情关键帧。图1只锁定同一角色的脸型、五官、年龄感、"
                "发型、服装结构和批准的3D国漫渲染；图2只锁定场景建筑、空间、材质、色彩与光线。"
                f"把图1人物自然放入图2；场景必须明确是{_location_prompt_context(unit)}，不得改到其他地点。"
                f"构图：{_compact_prompt_clause(unit.composition_prompt, 80)}。"
                f"当前场面：{_compact_prompt_clause(unit.visual_prompt, 88)}。"
                f"只表现“{_compact_prompt_clause(unit.motion_instruction, 72)}”开始前一瞬，"
                f"神情为{_compact_prompt_clause(unit.emotion, 24)}，嘴巴闭合。"
                "最终恰好一名人物，画面明亮、清楚、自然；不复制人物卡站姿，不新增人物、"
                "背景人脸、分身、拼板、文字或水印。画法必须与图1、图2属于同一部高品质中国3D国漫："
                "简化雕塑式面部结构、略大但非Q版的表现型眼睛、哑光无毛孔皮肤、束状设计发丝、"
                "自然人体比例、克制Toon-PBR布料木石金属和统一电影光照；"
                "禁止二维线稿、真人照片、塑料玩偶、欧美卡通、写实游戏截图、角色创建界面或夜景。"
            )
        return (
            "GPT Image 2双参考剧情关键帧。图1只锁定同一角色的脸型、五官、年龄感、"
            "发型、服装和二维线稿平涂；图2只锁定场景建筑、空间、色彩与白昼光线。"
            f"把图1人物自然放入图2；场景必须明确是{_location_prompt_context(unit)}，"
            "不得改到门外、庭院或其他地点。"
            f"构图：{_compact_prompt_clause(unit.composition_prompt, 80)}。"
            f"当前场面：{_compact_prompt_clause(unit.visual_prompt, 88)}。"
            f"只表现“{_compact_prompt_clause(unit.motion_instruction, 72)}”开始前一瞬，"
            f"神情为{_compact_prompt_clause(unit.emotion, 24)}，嘴巴闭合。"
            "最终恰好一名人物，画面明亮、清楚、自然；不复制设定图姿势，不新增人物、"
            "背景人脸、分身、拼板、文字或水印。画法必须与图1、图2为同一部二维电视动画："
            "清晰有粗细的轮廓线、纯色平涂、最多两级硬边赛璐璐阴影、哑光皮肤与布料；"
            "禁止半写实厚涂、皮肤镜面高光、毛孔、玻璃眼、柔焦摄影景深、真人、3D、PBR或夜景。"
        )

    @staticmethod
    def _scene_reference_keyframe_prompt(
        unit: RuntimeUnit,
        character_reference_count: int,
    ) -> str:
        if _is_3d_guoman_unit(unit):
            character_scope = (
                f"图2至图{character_reference_count + 1}只锁定主要角色身份、发型、服装和批准的3D国漫渲染；"
                if character_reference_count
                else ""
            )
            return (
                "Qwen Image Edit 3D国漫剧情关键帧。图1只锁定空场建筑、空间布局、材质、色彩和光线；"
                f"{character_scope}所有参考只用于身份与环境，不继承原构图或姿势。"
                f"场景必须明确是{_location_prompt_context(unit)}，不得改到未登记地点。"
                f"场面：{_compact_prompt_clause(unit.visual_prompt, 110)}。"
                f"构图：{_compact_prompt_clause(unit.composition_prompt, 76)}。"
                f"只表现“{_compact_prompt_clause(unit.motion_instruction, 72)}”开始前一瞬。"
                "保持高品质风格化中国3D国漫、清晰主体和统一电影光；画面内人物闭嘴。"
                "角色使用简化雕塑式面部、哑光无毛孔皮肤、束状发丝和自然比例；环境使用克制Toon-PBR木石金属。"
                "禁止二维线稿、真人照片、塑料玩偶、写实游戏截图、夜景、新增人物、分身、文字或水印。"
            )
        character_scope = (
            f"图2至图{character_reference_count + 1}只锁定主要角色身份、发型、服装和二维画法；"
            if character_reference_count
            else ""
        )
        return (
            "GPT Image 2剧情关键帧。图1只锁定空场建筑、空间布局、色彩和白昼光线；"
            f"{character_scope}所有参考只用于身份与环境，不继承原构图或姿势。"
            f"场景必须明确是{_location_prompt_context(unit)}，不得改到未登记地点。"
            f"场面：{_compact_prompt_clause(unit.visual_prompt, 110)}。"
            f"构图：{_compact_prompt_clause(unit.composition_prompt, 76)}。"
            f"只表现“{_compact_prompt_clause(unit.motion_instruction, 72)}”开始前一瞬。"
            "保持明亮二维国风赛璐璐和清晰主体；画面内人物闭嘴。画法必须与参考为同一部"
            "二维电视动画：清晰有粗细的轮廓线、纯色平涂、最多两级硬边赛璐璐阴影、"
            "哑光皮肤与布料；禁止半写实厚涂、皮肤镜面高光、毛孔、玻璃眼、柔焦摄影景深、"
            "真人、3D、PBR或夜景，不新增人物、分身、文字或水印。"
        )

    @staticmethod
    def _character_asset_text_anchor(
        novel_dir: Path,
        series_assets: SeriesAssetManifest,
        asset_id: str,
    ) -> str:
        """Compile trusted asset metadata into a short anti-archetype anchor."""

        record = next(
            (row for row in series_assets.characters if row.asset_id == asset_id),
            None,
        )
        if record is None:
            return ""
        spec_path = novel_dir / record.spec_path
        if not spec_path.is_file():
            return ""
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        episode_costumes = spec.get("episode_costumes") or []
        episode_costume = (
            episode_costumes[0]
            if isinstance(episode_costumes, list) and episode_costumes
            else episode_costumes
        )
        hair = str(spec.get("hair") or "")
        costume = str(spec.get("base_costume") or spec.get("wardrobe") or "")
        if "短发" in hair:
            hair_guard = "禁止把短发改成长发或束发"
        elif "长发" in hair:
            hair_guard = "禁止把长发改成短发，发长与束发结构必须照图1"
        else:
            hair_guard = "发长、刘海和束发结构必须逐项照图1"
        costume_guard = (
            "禁止把粗布常服升级成华服、盔甲、披风或金纹礼服"
            if "粗布" in costume
            else "服装款式、材质和装饰等级必须照图1，不得升级成盔甲、披风或金纹礼服"
        )
        fields = [
            ("角色", spec.get("name") or record.name),
            ("年龄", spec.get("age")),
            ("发型", spec.get("hair")),
            ("本集服装", episode_costume),
            ("基础服装", spec.get("base_costume") or spec.get("wardrobe")),
            ("配色", spec.get("palette")),
        ]
        clauses = [
            f"{label}={_compact_prompt_clause(str(value), 48)}"
            for label, value in fields
            if value
        ]
        if not clauses:
            return ""
        return (
            "角色硬锚点（与图1同等优先，逐项照做）："
            + "；".join(clauses)
            + f"。{hair_guard}，{costume_guard}。"
        )

    def _direct_h3_assets(
        self,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
    ) -> tuple[Path, tuple[Path, ...]] | None:
        """Resolve reusable H3 assets without manufacturing a per-shot still.

        Picture 1 is a locked character asset and Picture 2 is the matching
        empty location.  Empty establishing shots can use the location as
        Picture 1.  Multi-character, prop-interaction and reveal shots keep the
        scene-aware keyframe workflow.
        """

        strategy = self.settings.local_visual_strategy
        if strategy not in {"adaptive", "h3-direct-single-character"}:
            return None
        backend_identity = "".join(
            character
            for character in f"{self.settings.video_model} {self.settings.video_command or ''}".casefold()
            if character.isalnum()
        )
        if self.settings.provider != "command" or not any(
            token in backend_identity for token in ("minimaxh3", "h3ref2va")
        ):
            return None
        if unit.visual_strategy == VisualStrategy.STORY_KEYFRAME:
            return None
        if strategy == "h3-direct-single-character" and not unit.reference_audio_required:
            return None
        direct_character_ids = (
            unit.direct_video_character_asset_ids
            or (
                unit.character_asset_ids
                if len(unit.character_asset_ids) == 1
                else []
            )
        )
        character_map = {record.asset_id: record for record in series_assets.characters}
        location_map = {record.asset_id: record for record in series_assets.locations}
        location = location_map.get(unit.location_asset_id)
        if location is None:
            return None
        location_path = novel_dir / location.primary_image
        if not location_path.is_file():
            return None
        if unit.visual_strategy == VisualStrategy.SCENE_ONLY and not direct_character_ids:
            return location_path, ()
        if len(direct_character_ids) != 1:
            return None
        character = character_map.get(direct_character_ids[0])
        if character is None:
            return None
        character_path = novel_dir / character.primary_image
        if not character_path.is_file():
            return None
        return character_path, (location_path,)

    def _direct_phanrouter_assets(
        self,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
    ) -> tuple[Path, tuple[Path, ...]] | None:
        """Use approved series assets directly when no story keyframe is needed."""

        if self.settings.provider != "phanrouter":
            return None
        if unit.visual_strategy == VisualStrategy.STORY_KEYFRAME:
            return None
        if unit.visual_strategy not in {
            VisualStrategy.DIRECT_ASSETS,
            VisualStrategy.SCENE_ONLY,
        }:
            return None
        character_map = {record.asset_id: record for record in series_assets.characters}
        location_map = {record.asset_id: record for record in series_assets.locations}
        location = location_map.get(unit.location_asset_id)
        if location is None:
            return None
        location_path = novel_dir / location.primary_image
        if not location_path.is_file():
            return None
        character_paths = [
            novel_dir / character_map[asset_id].primary_image
            for asset_id in unit.character_asset_ids
            if asset_id in character_map
            and (novel_dir / character_map[asset_id].primary_image).is_file()
        ]
        if unit.visual_strategy == VisualStrategy.SCENE_ONLY or not character_paths:
            return location_path, ()
        return character_paths[0], tuple([location_path, *character_paths[1:]])

    def _ensure_keyframe_with_safety_retry(
        self,
        *,
        episode_dir: Path,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
        output: Path,
        prompt: str,
        reference: Path,
        additional_references: tuple[Path, ...],
    ) -> Path:
        image_kwargs: dict[str, object] = {"reference": reference}
        if additional_references:
            image_kwargs["additional_references"] = additional_references
        try:
            return self.assets._ensure_image(
                prompt,
                output,
                **image_kwargs,
            ).path
        except Exception as error:
            if not (
                self.settings.provider == "phanrouter"
                and is_image_generation_safety_rejection(error)
            ):
                raise

            character_map = {
                record.asset_id: record for record in series_assets.characters
            }
            location_map = {
                record.asset_id: record for record in series_assets.locations
            }
            fallback_reference = reference
            fallback_additional: tuple[Path, ...] = ()
            for character_id in unit.character_asset_ids:
                record = character_map.get(character_id)
                if record is None:
                    continue
                candidate = novel_dir / record.primary_image
                if candidate.is_file():
                    fallback_reference = candidate
                    break
            location = location_map.get(unit.location_asset_id)
            if location is not None:
                location_path = novel_dir / location.primary_image
                if location_path.is_file():
                    if fallback_reference == reference and not unit.character_asset_ids:
                        fallback_reference = location_path
                    elif location_path != fallback_reference:
                        fallback_additional = (location_path,)

            fallback_prompt = anime_image_safety_fallback_prompt(unit)
            fallback_kwargs: dict[str, object] = {"reference": fallback_reference}
            if fallback_additional:
                fallback_kwargs["additional_references"] = fallback_additional
            result = self.assets._ensure_image(
                fallback_prompt,
                output,
                **fallback_kwargs,
            ).path
            atomic_write_json(
                output.parent / "image_safety_fallback_report.json",
                {
                    "reason": "hosted-image-safety-rejection",
                    "original_error": str(error)[:500],
                    "original_prompt_sha256": sha256_text(prompt),
                    "fallback_prompt": fallback_prompt,
                    "fallback_reference": Path(
                        os.path.relpath(fallback_reference, episode_dir)
                    ).as_posix(),
                    "fallback_additional_references": [
                        Path(os.path.relpath(path, episode_dir)).as_posix()
                        for path in fallback_additional
                    ],
                    "fallback_sha256": sha256_file(result),
                    "policy": "calm-single-subject-anime-start-frame-v1",
                    "iteration_record": generation_iteration_record(
                        unit_id=unit.unit_id,
                        prompt_sha256=sha256_text(fallback_prompt),
                        batch_id="image-safety-repair-v1",
                        failure_codes=["F-PROMPT-SAFETY"],
                        responsibility_layer="prompt",
                        changed_variables=[
                            "image_prompt.story_context",
                            "reference_scope.visible_subjects",
                        ],
                        hypothesis=(
                            "Removing non-visible backstory and retaining only the current "
                            "calm start state should pass hosted image safety review."
                        ),
                        expected_improvement=(
                            "A valid single-subject anime keyframe without changing the "
                            "locked role or location references."
                        ),
                        decision="new_prompt_version",
                        next_action="continue only if the fallback image is generated",
                    ),
                },
            )
            return result

    def _enforce_bright_location_keyframe(
        self,
        *,
        episode_dir: Path,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
        keyframe: Path,
        reference: Path,
        additional_references: tuple[Path, ...],
        location_reference: Path,
    ) -> Path:
        before = keyframe_brightness_report(keyframe, location_reference)
        report_path = keyframe.parent / "keyframe_style_report.json"
        if before["status"] == "passed":
            atomic_write_json(
                report_path,
                {"before": before, "after": before, "repaired": False},
            )
            return keyframe

        rejected = keyframe.parent / "rejected_style_v1" / "keyframe.jpeg"
        if keyframe.is_file():
            copy_keyframe(keyframe, rejected)
        repaired_output = keyframe.parent / (
            "bright_3d_keyframe.jpeg"
            if _is_3d_guoman_unit(unit)
            else "bright_2d_keyframe.jpeg"
        )
        repair_prompt = unit.keyframe_prompt + (
            " 上一结果错误地变暗或偏向真人。重新生成同一3D国漫剧情起始帧：严格沿用角色图和"
            "场景图已有的简化雕塑式造型、哑光Toon-PBR材质、人物身份、建筑与批准光线；"
            "提高脸部、服装和背景中间调，但不漂白、不改构图、不新增人物。"
            if _is_3d_guoman_unit(unit)
            else (
                " 上一结果错误地变暗或偏向真人。重新生成同一剧情起始帧：严格沿用角色图和"
                "场景图已有的二维线稿、平涂、人物身份、建筑与白昼暖光；提高脸部、服装和背景"
                "中间调，但不漂白、不改构图、不新增人物。"
            )
        )
        repaired = self._ensure_keyframe_with_safety_retry(
            episode_dir=episode_dir,
            novel_dir=novel_dir,
            unit=unit,
            series_assets=series_assets,
            output=repaired_output,
            prompt=repair_prompt,
            reference=reference,
            additional_references=additional_references,
        )
        after = keyframe_brightness_report(repaired, location_reference)
        atomic_write_json(
            report_path,
            {
                "before": before,
                "after": after,
                "repaired": True,
                "rejected_keyframe": Path(
                    os.path.relpath(rejected, episode_dir)
                ).as_posix(),
                "repair_prompt": repair_prompt,
                "iteration_record": generation_iteration_record(
                    unit_id=unit.unit_id,
                    prompt_sha256=sha256_text(repair_prompt),
                    batch_id="brightness-repair-v1",
                    failure_codes=["F-EXPOSURE", "F-COLOR-DRIFT"],
                    responsibility_layer="prompt",
                    changed_variables=[
                        "image_prompt.exposure",
                        "reference_scope.location_daylight",
                    ],
                    hypothesis=(
                        "If the same character and location references are retained while "
                        "the prompt explicitly restores the location's daylight exposure, "
                        "the keyframe should stop drifting into a dark realistic rendering."
                    ),
                    expected_improvement=(
                        "Keyframe luminance returns above the bright-location relative gate "
                        "without changing identity or composition."
                    ),
                    decision="new_prompt_version",
                    next_action="submit to video only when the repaired keyframe passes",
                ),
            },
        )
        if after["status"] != "passed":
            raise RuntimeError(
                f"{unit.unit_id} keyframe remains inconsistent with its bright "
                "location reference after one repair"
            )
        copy_keyframe(repaired, keyframe)
        return keyframe

    def _prepare_keyframe(
        self,
        episode_dir: Path,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
    ) -> dict:
        cast_guard = SeriesAssetFactory.keyframe_cast_guard(unit, series_assets)
        if cast_guard:
            unit = unit.model_copy(
                update={"keyframe_prompt": unit.keyframe_prompt + cast_guard}
            )
        audio = self._resolve(episode_dir, unit.audio_path)
        canonical_video = self._resolve(episode_dir, unit.raw_video_path)
        canonical_keyframe = self._resolve(episode_dir, unit.keyframe_path)
        reference_board = self.assets.reference_board(
            episode_dir, unit, series_assets, novel_dir
        )
        location_record = next(
            (
                record
                for record in (
                    series_assets.locations if series_assets is not None else ()
                )
                if record.asset_id == unit.location_asset_id
            ),
            None,
        )
        location_style_reference = (
            novel_dir / location_record.primary_image
            if location_record is not None
            else None
        )
        additional_keyframe_references: tuple[Path, ...] = ()
        character_map = {
            record.asset_id: record
            for record in (
                series_assets.characters if series_assets is not None else ()
            )
        }
        character_paths = tuple(
            novel_dir / character_map[asset_id].primary_image
            for asset_id in unit.character_asset_ids[:6]
            if asset_id in character_map
            and (novel_dir / character_map[asset_id].primary_image).is_file()
        )
        single_character_anchor = (
            self._character_asset_text_anchor(
                novel_dir,
                series_assets,
                unit.character_asset_ids[0],
            )
            if len(unit.character_asset_ids) == 1
            else ""
        )
        multi_character_story_keyframe = (
            unit.visual_strategy == VisualStrategy.STORY_KEYFRAME
            and len(character_paths) >= 2
        )
        if multi_character_story_keyframe and self.settings.provider == "phanrouter":
            reference_board = character_paths[0]
            additional_keyframe_references = tuple(
                [
                    *character_paths[1:],
                    *(
                        [location_style_reference]
                        if location_style_reference is not None
                        and location_style_reference.is_file()
                        else []
                    ),
                ]
            )
        elif unit.speaking and self.settings.provider in {"command", "phanrouter"}:
            location = location_record
            if location is not None:
                location_path = novel_dir / location.primary_image
                if location_path.is_file() and location_path != reference_board:
                    additional_keyframe_references = (location_path,)
        if (
            unit.speaking
            and additional_keyframe_references
            and not multi_character_story_keyframe
        ):
            unit = unit.model_copy(
                update={
                    "keyframe_prompt": (
                        self._two_reference_keyframe_prompt(unit)
                        + single_character_anchor
                    )
                }
            )
        elif not unit.speaking and self.settings.provider == "phanrouter":
            location = location_record
            if location is not None:
                location_path = novel_dir / location.primary_image
                if location_path.is_file():
                    if len(character_paths) == 1:
                        # GPT Image 2 follows the primary image more strongly in
                        # single-character B-roll. Put identity/costume first and
                        # the empty location second, exactly as for dialogue; the
                        # old scene-first order frequently replaced short-haired,
                        # plain-costume characters with generic long-haired heroes.
                        reference_board = character_paths[0]
                        additional_keyframe_references = (location_path,)
                    else:
                        reference_board = location_path
                        additional_keyframe_references = character_paths
            if len(character_paths) == 1 and additional_keyframe_references:
                unit = unit.model_copy(
                    update={
                        "keyframe_prompt": (
                            self._two_reference_keyframe_prompt(unit)
                            + single_character_anchor
                        )
                    }
                )
            else:
                unit = unit.model_copy(
                    update={
                        "keyframe_prompt": self._scene_reference_keyframe_prompt(
                            unit,
                            len(additional_keyframe_references),
                        )
                    }
                )
        if self.settings.provider == "phanrouter":
            unit = unit.model_copy(
                update={
                    "motion_prompt": (
                        compile_seedance_native_audio_prompt(unit)
                        if self.settings.final_audio_policy
                        in NATIVE_VIDEO_AUDIO_POLICIES
                        else compile_phanrouter_runtime_motion_prompt(unit)
                    )
                }
            )
        direct_h3_assets = self._direct_h3_assets(novel_dir, unit, series_assets)
        direct_phanrouter_assets = self._direct_phanrouter_assets(
            novel_dir,
            unit,
            series_assets,
        )
        if direct_phanrouter_assets is not None:
            reference_board, additional_keyframe_references = direct_phanrouter_assets
        identity = self._visual_identity(
            unit,
            audio,
            reference_board,
            additional_keyframe_references,
        )
        meta = canonical_video.with_suffix(canonical_video.suffix + ".request.json")
        selected_video: Path | None = None
        selected_keyframe: Path | None = None
        selected_attempt = 0
        selected_used_reference_audio = False
        reused_keyframe = False
        additional_video_images: tuple[Path, ...] = ()
        visual_input_strategy = "scene-aware-keyframe"
        use_reference_audio = (
            unit.speaking
            or unit.reference_audio_required
            or self.settings.video_requires_audio
        )
        generate_native_audio_without_reference = (
            self.settings.provider == "phanrouter"
            and self.settings.final_audio_policy in NATIVE_VIDEO_AUDIO_POLICIES
        )
        existing_keyframe_attempts: set[int] = set()
        if canonical_video.is_file() and canonical_keyframe.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if (
                saved.get("request_sha256") == identity
                and is_direct_reference_audio_visual_cache(saved)
            ):
                selected_video = canonical_video
                selected_keyframe = canonical_keyframe
                selected_attempt = int(saved.get("attempt", 0))
        if selected_video is not None and direct_h3_assets is not None:
            _, additional_video_images = direct_h3_assets
            visual_input_strategy = (
                "h3-empty-location-asset"
                if not additional_video_images
                else "h3-character-plus-location-assets"
            )
        if selected_video is not None and direct_phanrouter_assets is not None:
            _, additional_video_images = direct_phanrouter_assets
            visual_input_strategy = "phanrouter-direct-series-assets"
        if selected_video is None:
            attempt_root = (
                episode_dir / "work" / "visual_attempts" / unit.unit_id / identity[:8]
            )
            for attempt in range(1, self.settings.max_unit_attempts + 1):
                if (attempt_root / f"attempt_{attempt:02d}" / "keyframe.jpeg").is_file():
                    existing_keyframe_attempts.add(attempt)
            first_keyframe = next(
                (
                    attempt_root / f"attempt_{attempt:02d}" / "keyframe.jpeg"
                    for attempt in sorted(existing_keyframe_attempts)
                ),
                None,
            )
            if direct_h3_assets is not None:
                selected_keyframe, additional_video_images = direct_h3_assets
                reused_keyframe = True
                visual_input_strategy = (
                    "h3-empty-location-asset"
                    if not additional_video_images
                    else "h3-character-plus-location-assets"
                )
            elif direct_phanrouter_assets is not None:
                selected_keyframe, additional_video_images = direct_phanrouter_assets
                reused_keyframe = True
                visual_input_strategy = "phanrouter-direct-series-assets"
            elif first_keyframe is None:
                first_keyframe = attempt_root / "attempt_01" / "keyframe.jpeg"
                if self.settings.reuse_existing_keyframes and canonical_keyframe.is_file():
                    saved = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
                    expected_sha = saved.get("keyframe_sha256")
                    if not expected_sha or expected_sha == sha256_file(canonical_keyframe):
                        copy_keyframe(canonical_keyframe, first_keyframe)
                        reused_keyframe = True
                    else:
                        first_keyframe = self._ensure_keyframe_with_safety_retry(
                            episode_dir=episode_dir,
                            novel_dir=novel_dir,
                            unit=unit,
                            series_assets=series_assets,
                            output=first_keyframe,
                            prompt=(
                                unit.keyframe_prompt
                                + " 本单元为独立构图第1次生成，禁止复用其他台词的完整构图。"
                            ),
                            reference=reference_board,
                            additional_references=additional_keyframe_references,
                        )
                else:
                    first_keyframe = self._ensure_keyframe_with_safety_retry(
                        episode_dir=episode_dir,
                        novel_dir=novel_dir,
                        unit=unit,
                        series_assets=series_assets,
                        output=first_keyframe,
                        prompt=(
                            unit.keyframe_prompt
                            + " 本单元为独立构图第1次生成，禁止复用其他台词的完整构图。"
                        ),
                        reference=reference_board,
                        additional_references=additional_keyframe_references,
                    )
                selected_keyframe = first_keyframe
            else:
                reused_keyframe = True
                selected_keyframe = first_keyframe

        assert selected_keyframe is not None
        if (
            self.settings.provider == "phanrouter"
            and selected_video is None
            and location_style_reference is not None
            and location_style_reference.is_file()
            and direct_h3_assets is None
            and direct_phanrouter_assets is None
        ):
            selected_keyframe = self._enforce_bright_location_keyframe(
                episode_dir=episode_dir,
                novel_dir=novel_dir,
                unit=unit,
                series_assets=series_assets,
                keyframe=selected_keyframe,
                reference=reference_board,
                additional_references=additional_keyframe_references,
                location_reference=location_style_reference,
            )
        return {
            "episode_dir": episode_dir,
            "unit": unit,
            "audio": audio,
            "canonical_video": canonical_video,
            "canonical_keyframe": canonical_keyframe,
            "reference_board": reference_board,
            "additional_keyframe_references": additional_keyframe_references,
            "identity": identity,
            "meta": meta,
            "selected_video": selected_video,
            "selected_keyframe": selected_keyframe,
            "selected_attempt": selected_attempt,
            "selected_used_reference_audio": selected_used_reference_audio,
            "existing_keyframe_attempts": existing_keyframe_attempts,
            "reused_keyframe": reused_keyframe,
            "use_reference_audio": use_reference_audio,
            "generate_native_audio_without_reference": (
                generate_native_audio_without_reference
            ),
            "additional_video_images": additional_video_images,
            "visual_input_strategy": visual_input_strategy,
        }

    def _prepare_video(self, prepared: dict) -> dict:
        episode_dir: Path = prepared["episode_dir"]
        unit: RuntimeUnit = prepared["unit"]
        audio: Path = prepared["audio"]
        canonical_video: Path = prepared["canonical_video"]
        canonical_keyframe: Path = prepared["canonical_keyframe"]
        reference_board: Path = prepared["reference_board"]
        additional_keyframe_references: tuple[Path, ...] = prepared.get(
            "additional_keyframe_references", ()
        )
        identity: str = prepared["identity"]
        meta: Path = prepared["meta"]
        selected_video: Path | None = prepared["selected_video"]
        selected_keyframe: Path = prepared["selected_keyframe"]
        selected_attempt: int = prepared["selected_attempt"]
        selected_used_reference_audio: bool = prepared["selected_used_reference_audio"]
        existing_keyframe_attempts: set[int] = prepared["existing_keyframe_attempts"]
        reused_keyframe: bool = prepared["reused_keyframe"]
        use_reference_audio: bool = prepared["use_reference_audio"]
        generate_native_audio_without_reference: bool = prepared.get(
            "generate_native_audio_without_reference",
            False,
        )
        additional_video_images: tuple[Path, ...] = prepared.get(
            "additional_video_images", ()
        )
        visual_input_strategy: str = prepared.get(
            "visual_input_strategy", "scene-aware-keyframe"
        )
        # Reusable character/location assets intentionally live at the novel
        # level, beside the episode directory.  Store a portable relative path
        # even when the selected reference is outside this one episode; using
        # ``Path.relative_to(episode_dir)`` incorrectly rejected that valid
        # cross-episode reuse during parallel H3 cache merges.
        reference_board_audit_path = Path(
            os.path.relpath(reference_board, episode_dir)
        ).as_posix()
        visual_source = ""
        last_error: Exception | None = None

        if selected_video is None:
            for attempt in range(1, self.settings.max_unit_attempts + 1):
                directory = (
                    episode_dir
                    / "work"
                    / "visual_attempts"
                    / unit.unit_id
                    / identity[:8]
                    / f"attempt_{attempt:02d}"
                )
                keyframe_path = directory / "keyframe.jpeg"
                video_path = directory / "clip.mp4"
                video_task_path = video_path.with_suffix(video_path.suffix + ".task.json")
                if (
                    self.settings.provider != "command"
                    and use_reference_audio
                    and attempt <= 2
                    and attempt in existing_keyframe_attempts
                    and not video_path.is_file()
                    and not video_task_path.is_file()
                ):
                    reused_keyframe = True
                    continue
                if not keyframe_path.is_file():
                    copy_keyframe(selected_keyframe, keyframe_path)
                    reused_keyframe = True
                try:
                    attempt_uses_reference_audio = use_reference_audio and (
                        self.settings.provider == "command" or attempt <= 2
                    )
                    attempt_uses_reference_audio = (
                        attempt_uses_reference_audio
                        and not generate_native_audio_without_reference
                    )
                    if not video_path.is_file():
                        video_kwargs = {}
                        if additional_video_images:
                            video_kwargs["additional_images"] = additional_video_images
                        self.media.create_video(
                            (
                                unit.motion_prompt
                                if attempt_uses_reference_audio
                                or generate_native_audio_without_reference
                                else policy_safe_motion_prompt(unit.motion_prompt)
                            ),
                            ImageResult(path=keyframe_path),
                            video_path,
                            duration=min(
                                14.0,
                                max(4.0, float(unit.audio_seconds or 0.0) + 0.5),
                            ),
                            reference_audio=audio if attempt_uses_reference_audio else None,
                            **video_kwargs,
                        )
                except Exception as error:
                    last_error = error
                    if (
                        self.settings.provider == "phanrouter"
                        and attempt < self.settings.max_unit_attempts
                        and is_real_person_privacy_rejection(error)
                    ):
                        next_directory = (
                            episode_dir
                            / "work"
                            / "visual_attempts"
                            / unit.unit_id
                            / identity[:8]
                            / f"attempt_{attempt + 1:02d}"
                        )
                        repaired_source = next_directory / "privacy_safe_keyframe.jpeg"
                        next_keyframe = next_directory / "keyframe.jpeg"
                        try:
                            repaired = self.media.create_image(
                                anime_privacy_repair_prompt(unit),
                                repaired_source,
                                reference=keyframe_path,
                            )
                            copy_keyframe(repaired.path, next_keyframe)
                            atomic_write_json(
                                next_directory / "privacy_repair_report.json",
                                {
                                    "reason": (
                                        "InputImageSensitiveContentDetected."
                                        "PrivacyInformation"
                                    ),
                                    "source_attempt": attempt,
                                    "source_keyframe": keyframe_path.name,
                                    "source_sha256": sha256_file(keyframe_path),
                                    "repair_model": self.settings.image_model,
                                    "repair_prompt": anime_privacy_repair_prompt(unit),
                                    "repair_sha256": sha256_file(next_keyframe),
                                    "policy": "explicit-flat-2d-anime-redraw-v1",
                                    "iteration_record": generation_iteration_record(
                                        unit_id=unit.unit_id,
                                        prompt_sha256=sha256_text(
                                            anime_privacy_repair_prompt(unit)
                                        ),
                                        batch_id=f"video-privacy-repair-{attempt + 1:02d}",
                                        failure_codes=["F-MATERIAL", "F-REF-SCOPE"],
                                        responsibility_layer="asset",
                                        changed_variables=[
                                            "keyframe.rendering_material",
                                        ],
                                        hypothesis=(
                                            "Redrawing only the current keyframe as explicit flat "
                                            "2D cel animation should remove the real-person false "
                                            "positive while preserving the shot contract."
                                        ),
                                        expected_improvement=(
                                            "Seedance accepts the same shot without identity, "
                                            "composition, dialogue, or camera changes."
                                        ),
                                        decision="revise_asset",
                                        next_action="retry the same video adapter once",
                                    ),
                                },
                            )
                        except Exception as repair_error:
                            last_error = repair_error
                        continue
                    if attempt == self.settings.max_unit_attempts:
                        if self.settings.admission_mode == "production":
                            raise RuntimeError(
                                f"{unit.unit_id} has no real generated video after "
                                f"{attempt} attempts; static fallback is forbidden in production; "
                                f"last error: {type(error).__name__}: {str(error)[:500]}"
                            ) from error
                        fallback_seconds = min(
                            14.0,
                            max(4.0, float(unit.audio_seconds or 0.0) + 0.5),
                        )
                        self.renderer._silent_card_segment(
                            keyframe_path, video_path, fallback_seconds
                        )
                        visual_source = "local-keyframe-motion-fallback-after-video-failures"
                        selected_video = video_path
                        selected_keyframe = keyframe_path
                        selected_attempt = attempt
                        break
                    continue
                selected_video = video_path
                selected_keyframe = keyframe_path
                selected_attempt = attempt
                selected_used_reference_audio = attempt_uses_reference_audio
                break
        if selected_video is None:
            raise RuntimeError(
                f"{unit.unit_id} exhausted previously consumed video attempts"
            ) from last_error

        canonical_video.parent.mkdir(parents=True, exist_ok=True)
        canonical_keyframe.parent.mkdir(parents=True, exist_ok=True)
        if selected_video.resolve() != canonical_video.resolve():
            shutil.copy2(selected_video, canonical_video)
        selected_provider_audit = selected_video.with_suffix(
            selected_video.suffix + ".local.json"
        )
        canonical_provider_audit = canonical_video.with_suffix(
            canonical_video.suffix + ".local.json"
        )
        provider_audit_payload = None
        if selected_provider_audit.is_file():
            provider_audit_payload = json.loads(
                selected_provider_audit.read_text(encoding="utf-8")
            )
            if selected_provider_audit.resolve() != canonical_provider_audit.resolve():
                shutil.copy2(selected_provider_audit, canonical_provider_audit)
        if selected_keyframe.resolve() != canonical_keyframe.resolve():
            # The canonical copy is what a later run reuses, so it needs the
            # provider sidecar too or the reuse path reintroduces the failure.
            copy_keyframe(selected_keyframe, canonical_keyframe)
        if selected_video.resolve() == canonical_video.resolve() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            selected_used_reference_audio = bool(
                saved.get("reference_audio_used", use_reference_audio)
            )
            visual_source = str(saved.get("visual_source", ""))
        if not visual_source.startswith("local-keyframe-motion-fallback"):
            visual_source = (
                f"{self.settings.video_model}-native-audio-no-reference"
                if generate_native_audio_without_reference
                else
                f"{self.settings.video_model}-direct-reusable-assets-"
                + ("reference-audio" if selected_used_reference_audio else "no-audio-reference")
                if visual_input_strategy.startswith("h3-")
                or visual_input_strategy == "phanrouter-direct-series-assets"
                else (
                    f"{self.settings.video_model}-reference-audio"
                    if selected_used_reference_audio
                    else (
                        f"{self.settings.video_model}-policy-safe-prompt-dialogue-final-local-audio"
                        if use_reference_audio
                        else f"{self.settings.video_model}-narration-motion-no-audio-reference"
                    )
                )
            )
        unit.attempt = max(unit.attempt, selected_attempt)
        meta_payload = {
                "request_sha256": identity,
                "attempt": selected_attempt,
                "video_sha256": sha256_file(canonical_video),
                "audio_sha256": sha256_file(audio),
                "keyframe_sha256": sha256_file(canonical_keyframe),
                "keyframe_prompt": unit.keyframe_prompt,
                "motion_prompt": unit.motion_prompt,
                "reference_board": reference_board_audit_path,
                "reference_board_sha256": sha256_file(reference_board),
                "additional_keyframe_references": [
                    {
                        "path": str(path.relative_to(episode_dir.parent.parent))
                        if path.is_relative_to(episode_dir.parent.parent)
                        else str(path),
                        "sha256": sha256_file(path),
                    }
                    for path in additional_keyframe_references
                ],
                "visual_input_strategy": visual_input_strategy,
                "provider_video_audit": provider_audit_payload,
                "additional_video_images": [
                    {
                        "path": str(path.relative_to(episode_dir.parent.parent))
                        if path.is_relative_to(episode_dir.parent.parent)
                        else str(path),
                        "sha256": sha256_file(path),
                    }
                    for path in additional_video_images
                ],
                "workflow": (
                    "direct-reusable-assets-reference-audio-video-no-lip-review-v1"
                    if selected_used_reference_audio and additional_video_images
                    else "direct-reference-audio-video-no-lip-review-v2"
                    if selected_used_reference_audio
                    else (
                        "real-prompt-dialogue-video-final-local-audio-v1"
                        if use_reference_audio
                        else "narration-motion-video-no-audio-reference-v1"
                    )
                ),
                "reference_audio_used": selected_used_reference_audio,
                "visual_source": visual_source,
                "remote_failure": (
                    f"{type(last_error).__name__}: {last_error}"[:500]
                    if visual_source.startswith("local-keyframe-motion-fallback")
                    and last_error is not None
                    else None
                ),
                "keyframe_source": (
                    "reusable-series-asset"
                    if visual_input_strategy.startswith("h3-")
                    or visual_input_strategy == "phanrouter-direct-series-assets"
                    else "locked-existing-keyframe" if reused_keyframe else "generated"
                ),
        }
        atomic_write_json(meta, meta_payload)
        return {
            "unit_id": unit.unit_id,
            "role": unit.role,
            "speaker_name": unit.speaker_name,
            "speaking": unit.speaking,
            "text": unit.text,
            "clip": unit.raw_video_path,
            "audio": unit.audio_path,
            "attempt": selected_attempt,
            "visual_source": visual_source,
        }

    def _prepare_visual(
        self,
        episode_dir: Path,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
    ) -> dict:
        """Compatibility helper; production batches image and video stages."""

        return self._prepare_video(
            self._prepare_keyframe(episode_dir, novel_dir, unit, series_assets)
        )

    @staticmethod
    def _visual_group_proxy(
        group: RuntimeVisualGroup,
        units_by_id: dict[str, RuntimeUnit],
    ) -> RuntimeUnit:
        first = units_by_id[group.unit_ids[0]]
        visible_units = [
            units_by_id[unit_id]
            for unit_id in group.unit_ids
            if units_by_id[unit_id].speaking
        ]
        visible_identities = {
            (
                unit.speaker_name,
                tuple(unit.character_asset_ids),
            )
            for unit in visible_units
        }
        visible_speaker = visible_units[0] if len(visible_identities) == 1 else None
        ordered_character_ids = list(group.character_asset_ids)
        if visible_speaker is not None:
            ordered_character_ids = list(
                dict.fromkeys(
                    [
                        *visible_speaker.character_asset_ids,
                        *group.character_asset_ids,
                    ]
                )
            )
        return first.model_copy(
            update={
                "unit_id": group.group_id,
                "shot_id": group.shot_ids[0],
                "role": visible_speaker.role if visible_speaker else "narrator",
                "speaker_name": (
                    visible_speaker.speaker_name if visible_speaker else "旁白"
                ),
                "speaking": visible_speaker is not None,
                "reference_audio_required": any(
                    units_by_id[unit_id].speaking for unit_id in group.unit_ids
                ),
                "text": group.combined_text[:500],
                "character_asset_ids": ordered_character_ids,
                "direct_video_character_asset_ids": (
                    group.direct_video_character_asset_ids
                ),
                "location_asset_id": group.location_asset_id,
                "keyframe_prompt": (
                    group.prompt_adapter.image_prompt
                    if group.prompt_adapter is not None
                    else group.keyframe_prompt
                ),
                "motion_prompt": (
                    group.prompt_adapter.video_prompt
                    if group.prompt_adapter is not None
                    else group.motion_prompt
                ),
                # This is the H3 performance track, not the delivery track:
                # visible dialogue remains audible while narration and
                # off-screen voices are duration-preserving silence. Final
                # rendering remuxes the complete locked group.audio_path.
                "audio_path": group.video_audio_path,
                "keyframe_path": group.keyframe_path,
                "raw_video_path": group.raw_video_path,
                "segment_path": group.segment_path,
                "audio_seconds": group.audio_seconds,
                "delivery_mode": (
                    visible_speaker.delivery_mode
                    if visible_speaker is not None
                    else first.delivery_mode
                ),
                "emotion": (
                    visible_speaker.emotion
                    if visible_speaker is not None
                    else first.emotion
                ),
                "actor_description": (
                    visible_speaker.actor_description
                    if visible_speaker is not None
                    else first.actor_description
                ),
                "composition_prompt": (
                    visible_speaker.composition_prompt
                    if visible_speaker is not None
                    else first.composition_prompt
                ),
                "visual_strategy": group.visual_strategy,
                "keyframe_reasons": group.keyframe_reasons,
            }
        )

    def _build_provider_prompt_adapter(
        self,
        group: RuntimeVisualGroup,
        proxy: RuntimeUnit,
    ) -> ProviderPromptAdapter:
        if group.shot_contract is None:
            raise ValueError(f"{group.group_id} has no internal shot contract")
        if (
            group.visual_strategy == VisualStrategy.STORY_KEYFRAME
            and len(proxy.character_asset_ids) >= 2
        ):
            image_prompt = group.keyframe_prompt
            reference_order = [
                *proxy.character_asset_ids,
                proxy.location_asset_id,
            ]
        elif proxy.speaking:
            image_prompt = self._two_reference_keyframe_prompt(proxy)
            reference_order = [
                *proxy.character_asset_ids[:1],
                proxy.location_asset_id,
            ]
        else:
            character_count = min(2, len(proxy.character_asset_ids))
            image_prompt = self._scene_reference_keyframe_prompt(
                proxy,
                character_count,
            )
            reference_order = [
                proxy.location_asset_id,
                *proxy.character_asset_ids[:character_count],
            ]
        video_prompt = (
            compile_seedance_native_audio_prompt(proxy)
            if self.settings.final_audio_policy in NATIVE_VIDEO_AUDIO_POLICIES
            else compile_phanrouter_runtime_motion_prompt(proxy)
        )
        if group.image_contract is not None:
            if group.image_contract.spatial_anchors:
                anchors = _compact_prompt_clause(
                    "、".join(group.image_contract.spatial_anchors[:2]),
                    54,
                )
                image_prompt += f" 空间保持：{anchors}。"
                video_prompt += f" 空间锚点保持：{anchors}。"
            lighting = _compact_prompt_clause(group.image_contract.lighting, 56)
            image_prompt += f" 光线保持：{lighting}。"
            video_prompt += f" 光线保持：{lighting}。"
        if proxy.action_physics_plan is not None:
            feedback = _compact_prompt_clause(
                "、".join(proxy.action_physics_plan.environment_feedback[:2]),
                48,
            )
            if feedback:
                video_prompt += f" 环境只响应：{feedback}。"
        return ProviderPromptAdapter(
            provider=self.settings.provider,
            image_model=self.settings.image_model,
            video_model=self.settings.video_model,
            contract_sha256=sha256_text(
                group.shot_contract.model_dump_json()
            ),
            reference_order=reference_order,
            image_prompt=image_prompt,
            video_prompt=video_prompt,
        )

    @staticmethod
    def _audio_levels(path: Path) -> tuple[float | None, float | None]:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                "-af", "volumedetect", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        mean = re.search(r"mean_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB", result.stderr)
        peak = re.search(r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB", result.stderr)

        def parse(match: re.Match[str] | None) -> float | None:
            if match is None or match.group(1) in {"-inf", "inf"}:
                return None
            return float(match.group(1))

        return parse(mean), parse(peak)

    def _select_group_audio(
        self,
        *,
        episode_dir: Path,
        group: RuntimeVisualGroup,
        raw_video: Path,
        locked_tts: Path,
    ) -> tuple[Path, dict]:
        if (
            self.settings.final_audio_policy
            not in NATIVE_VIDEO_AUDIO_POLICIES
            or self.settings.provider != "phanrouter"
        ):
            return locked_tts, {
                "group_id": group.group_id,
                "selected_source": "locked_tts",
                "reason": "configured locked_tts policy",
            }
        native = (
            episode_dir
            / "work"
            / "visual_group_native_audio"
            / f"{group.group_id}.wav"
        )
        native.parent.mkdir(parents=True, exist_ok=True)
        try:
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(raw_video),
                    "-vn",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(native),
                ]
            )
            native_duration = media_duration(native)
            speech_start, speech_end = measured_speech_bounds(native)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return locked_tts, {
                "group_id": group.group_id,
                "selected_source": "silent_timing_fallback",
                "reason": f"native audio unavailable: {type(error).__name__}",
            }
        return native, {
            "group_id": group.group_id,
            "selected_source": self.settings.final_audio_policy,
            "reason": "configured to keep video-model native audio without ASR",
            "native_audio": str(native.relative_to(episode_dir)),
            "native_duration": round(native_duration, 6),
            "speech_start": speech_start,
            "speech_end": speech_end,
        }

    def _audit_delivered_asr(
        self,
        episode_dir: Path,
        final_video: Path,
        plan: ProductionPlan,
        delivery_timeline: list[dict],
    ) -> dict:
        rows = []
        delivered_dir = episode_dir / "work" / "delivered_turn_audio"
        units_by_id = {unit.unit_id: unit for unit in plan.units}
        for timing in delivery_timeline:
            unit = units_by_id[str(timing["unit_id"])]
            start = self.settings.intro_seconds + float(timing["speech_start"])
            end = self.settings.intro_seconds + float(timing["speech_end"])
            output = delivered_dir / f"{unit.unit_id}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            run([
                "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.6f}",
                "-i", str(final_video), "-t", f"{max(0.1, end - start):.6f}",
                "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", str(output),
            ])
            row = self.evidence.transcribe(unit.unit_id, unit.text, output)
            mean_volume, max_volume = self._audio_levels(output)
            rows.append(
                {
                    **row,
                    "audio": str(output.relative_to(episode_dir)),
                    "delivered_start": round(start, 6),
                    "delivered_end": round(end, 6),
                    "mean_volume_db": mean_volume,
                    "max_volume_db": max_volume,
                }
            )
        report = aggregate_asr(rows)
        report["audio_source"] = "delivered_final_video_per_turn_extract"
        return report

    def run(
        self,
        *,
        novel_dir: Path,
        episode_dir: Path,
        episode: Episode,
        episode_plan: EpisodePlan,
        bible: StoryBible,
        series_assets: SeriesAssetManifest,
        final_video: Path,
        cover: Path,
        ending: Path,
        video_id: str,
        episode_count: int,
    ) -> dict:
        plan = compile_production_plan(video_id, episode, episode_plan, bible, series_assets)
        plan_path = episode_dir / "production_plan.json"
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        self.media.enter_stage("audio")
        asr_rows = []
        alignments = []
        for unit in plan.units:
            if self.settings.final_audio_policy in NATIVE_VIDEO_AUDIO_POLICIES:
                asr_row, alignment = self._prepare_native_timing_audio(
                    episode_dir,
                    unit,
                )
            else:
                asr_row, alignment = self._prepare_audio(episode_dir, unit)
            asr_rows.append(asr_row)
            alignments.append(alignment)
        tts_asr_report = aggregate_asr(asr_rows)
        tts_asr_report["audio_source"] = (
            "native_video_audio_preview_no_tts_no_asr"
            if self.settings.final_audio_policy in NATIVE_VIDEO_AUDIO_POLICIES
            else "locked_tts_reference_before_video"
        )
        atomic_write_json(episode_dir / "tts_asr_report.json", tts_asr_report)
        atomic_write_json(episode_dir / "alignment_report.json", {"units": alignments})
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        visual_target_seconds = min(13.4, self.settings.video_max_seconds - 0.5)
        plan.visual_groups = build_visual_groups(
            plan,
            series_assets=series_assets,
            target_seconds=visual_target_seconds,
            allow_cross_shot_merge=self.settings.provider == "command",
        )
        units_by_id = {unit.unit_id: unit for unit in plan.units}
        alignment_by_id = {str(row["unit_id"]): row for row in alignments}
        group_timelines: dict[str, list[dict]] = {}
        for group in plan.visual_groups:
            audios = [self._resolve(episode_dir, units_by_id[unit_id].audio_path) for unit_id in group.unit_ids]
            _, seconds, offsets, speed = self.renderer.compose_visual_group_audio(
                audios,
                self._resolve(episode_dir, group.audio_path),
                target_seconds=visual_target_seconds,
            )
            driver_path, driver_seconds, driver_offsets, driver_speed = (
                self.renderer.compose_visual_group_audio(
                    audios,
                    self._resolve(episode_dir, group.video_audio_path),
                    audible=[
                        units_by_id[unit_id].speaking for unit_id in group.unit_ids
                    ],
                    target_seconds=visual_target_seconds,
                )
            )
            if abs(driver_seconds - seconds) > 0.03 or driver_offsets != offsets:
                raise RuntimeError(
                    f"{group.group_id} video audio driver drifted from locked TTS timeline"
                )
            if abs(driver_speed - speed) > 1e-6 or not driver_path.is_file():
                raise RuntimeError(
                    f"{group.group_id} video audio driver does not match group speed"
                )
            group.audio_seconds = round(seconds, 6)
            group.speed_factor = round(speed, 8)
            timings = []
            for unit_id, offset in zip(group.unit_ids, offsets, strict=True):
                unit = units_by_id[unit_id]
                alignment = alignment_by_id[unit_id]
                speech_start = offset + float(alignment["speech_start"]) / speed
                speech_end = offset + float(alignment["speech_end"]) / speed
                events = [
                    {
                        "unit_id": unit_id,
                        "role": unit.role,
                        "start": offset + float(event["start"]) / speed,
                        "end": offset + float(event["end"]) / speed,
                        "text": str(event["text"]),
                    }
                    for event in alignment["events"]
                ]
                timings.append(
                    {
                        "unit_id": unit_id,
                        "offset": round(offset, 6),
                        "speech_start": round(speech_start, 6),
                        "speech_end": round(speech_end, 6),
                        "events": events,
                    }
                )
            group_timelines[group.group_id] = timings
        previous_continuity_out: str | None = None
        for group in plan.visual_groups:
            if group.shot_contract is None:
                continue
            if previous_continuity_out is not None:
                group.shot_contract = group.shot_contract.model_copy(
                    update={"continuity_in": previous_continuity_out}
                )
            previous_continuity_out = group.shot_contract.continuity_out
        if self.settings.provider == "phanrouter":
            for group in plan.visual_groups:
                proxy = self._visual_group_proxy(group, units_by_id)
                group.prompt_adapter = self._build_provider_prompt_adapter(
                    group,
                    proxy,
                )
        plan.sequence_contract = _build_sequence_contract(
            plan=plan,
            episode_plan=episode_plan,
            groups=plan.visual_groups,
            units_by_id=units_by_id,
        )
        atomic_write_json(
            episode_dir / "visual_group_plan.json",
            {
                "policy": "shot-contract-provider-adapter-v2",
                "turn_count": len(plan.units),
                "visual_group_count": len(plan.visual_groups),
                "sequence_contract": plan.sequence_contract.model_dump(mode="json"),
                "groups": [group.model_dump(mode="json") for group in plan.visual_groups],
                "timelines": group_timelines,
            },
        )
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        estimated_seconds = (
            self.settings.intro_seconds
            + self.settings.outro_seconds
            + sum(float(group.audio_seconds or 0.0) + 0.2 for group in plan.visual_groups)
        )
        if estimated_seconds > 300.5:
            raise ValueError(
                f"audio-bound episode estimate {estimated_seconds:.1f}s exceeds the five-minute limit"
            )

        group_proxies = [
            self._visual_group_proxy(group, units_by_id) for group in plan.visual_groups
        ]
        self.media.enter_stage("image-edit")
        prepared_visuals: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.media_workers) as executor:
            futures = {
                executor.submit(
                    self._prepare_keyframe,
                    episode_dir,
                    novel_dir,
                    unit,
                    series_assets,
                ): unit.unit_id
                for unit in group_proxies
            }
            for future in as_completed(futures):
                prepared_visuals.append(future.result())
        prepared_visuals.sort(key=lambda row: str(row["unit"].unit_id))

        # Materialize the selected keyframes before video generation.  This
        # keeps cover art, ending art and face consistency in the image stage,
        # so a one-GPU runtime never reloads the image model after H3 starts.
        selected_keyframes = {
            str(row["unit"].unit_id): Path(row["selected_keyframe"])
            for row in prepared_visuals
        }
        for group in plan.visual_groups:
            source = selected_keyframes[group.group_id]
            group_keyframe = self._resolve(episode_dir, group.keyframe_path)
            group_keyframe.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != group_keyframe.resolve():
                shutil.copy2(source, group_keyframe)
            for unit_id in group.unit_ids:
                target = self._resolve(episode_dir, units_by_id[unit_id].keyframe_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(group_keyframe, target)
        face_consistency_report = evaluate_face_consistency(
            novel_dir=novel_dir,
            episode_dir=episode_dir,
            plan=plan,
            assets=series_assets,
        )
        atomic_write_json(episode_dir / "face_consistency_report.json", face_consistency_report)
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        self._prepare_cover(
            episode_dir=episode_dir,
            plan=plan,
            episode=episode,
            episode_plan=episode_plan,
            bible=bible,
            series_assets=series_assets,
            cover=cover,
        )
        intro_card, _ = self._prepare_endpoint_cards(
            episode_dir=episode_dir,
            plan=plan,
            episode=episode,
            episode_plan=episode_plan,
            bible=bible,
            cover=cover,
            ending=ending,
            episode_count=episode_count,
        )

        visual_rows: list[dict] = []
        if self.settings.model_lifecycle_command:
            # Local deployments expose one video capability. MiniMax H3 uses
            # either real dialogue or a silent timing driver, so the Core no
            # longer branches into separate video checkpoints.
            self.media.enter_stage("video")
            with ThreadPoolExecutor(max_workers=self.settings.video_workers) as executor:
                futures = [
                    executor.submit(self._prepare_video, row)
                    for row in prepared_visuals
                ]
                visual_rows.extend(future.result() for future in as_completed(futures))
        else:
            with ThreadPoolExecutor(max_workers=self.settings.video_workers) as executor:
                futures = [
                    executor.submit(self._prepare_video, row) for row in prepared_visuals
                ]
                visual_rows.extend(future.result() for future in as_completed(futures))
        visual_rows.sort(key=lambda row: str(row["unit_id"]))
        # The generation proxy deliberately collapses a merged group to a single
        # narrator voice, but that left every row in this report reading as
        # 旁白 — including plain visible-dialogue shots — so the report could not
        # be used to audit who actually speaks in a shot.  Restore the real
        # per-turn cast here, where the underlying units are still in scope.
        units_by_id = {unit.unit_id: unit for unit in plan.units}
        speakers_by_group = {
            group.group_id: [
                {
                    "unit_id": unit_id,
                    "role": units_by_id[unit_id].role,
                    "speaker_name": units_by_id[unit_id].speaker_name,
                    "speaking": units_by_id[unit_id].speaking,
                }
                for unit_id in group.unit_ids
                if unit_id in units_by_id
            ]
            for group in plan.visual_groups
        }
        for row in visual_rows:
            row["turn_cast"] = speakers_by_group.get(str(row["unit_id"]), [])
        iteration_records = []
        for report_path in sorted(
            (episode_dir / "work" / "visual_attempts").glob("**/*report.json")
        ):
            try:
                report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            record = report_payload.get("iteration_record")
            if isinstance(record, dict):
                iteration_records.append(record)
        atomic_write_json(
            episode_dir / "visual_generation_report.json",
            {
                "workflow": "shot-contract-provider-adapter-v2",
                "turn_count": len(plan.units),
                "visual_group_count": len(plan.visual_groups),
                "groups": visual_rows,
                "iteration_records": iteration_records,
            },
        )
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        selected_group_audio: dict[str, Path] = {}
        native_audio_rows = []
        for group in plan.visual_groups:
            selected_audio, audio_row = self._select_group_audio(
                episode_dir=episode_dir,
                group=group,
                raw_video=self._resolve(episode_dir, group.raw_video_path),
                locked_tts=self._resolve(episode_dir, group.audio_path),
            )
            selected_group_audio[group.group_id] = selected_audio
            native_audio_rows.append(audio_row)
            if audio_row.get("selected_source") in NATIVE_VIDEO_AUDIO_POLICIES:
                group_timelines[group.group_id] = retime_group_timelines_to_native_audio(
                    group_timelines[group.group_id],
                    speech_start=float(audio_row["speech_start"]),
                    speech_end=float(audio_row["speech_end"]),
                )
                group.audio_seconds = round(media_duration(selected_audio), 6)
        atomic_write_json(
            episode_dir / "native_audio_selection_report.json",
            {
                "policy": self.settings.final_audio_policy,
                "groups": native_audio_rows,
            },
        )
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        audio_source_by_group = {
            str(row["group_id"]): str(row["selected_source"])
            for row in native_audio_rows
        }

        turn_segments = []
        delivery_timeline = []
        story_cursor = 0.0
        for group in plan.visual_groups:
            segment, duration = self.renderer.mux_visual_group(
                self._resolve(episode_dir, group.raw_video_path),
                selected_group_audio[group.group_id],
                self._resolve(episode_dir, group.segment_path),
            )
            group.segment_seconds = round(duration, 6)
            local_events = [
                event
                for timing in group_timelines[group.group_id]
                for event in timing["events"]
            ]
            turn_segments.append(
                {
                    "unit_id": group.group_id,
                    "role": "narrator",
                    "segment": str(segment),
                    "duration": duration,
                    "audio_source": audio_source_by_group[group.group_id],
                    "subtitle_events": local_events,
                }
            )
            for timing in group_timelines[group.group_id]:
                delivery_timeline.append(
                    {
                        "unit_id": timing["unit_id"],
                        "speech_start": story_cursor + float(timing["speech_start"]),
                        "speech_end": story_cursor + float(timing["speech_end"]),
                    }
                )
            story_cursor += duration
        story_seconds = sum(float(row["duration"]) for row in turn_segments)
        if self.settings.intro_seconds + story_seconds + self.settings.outro_seconds > 300.5:
            raise ValueError("planned episode exceeds the five-minute admission limit")

        final_video, ass, joined, subtitle_events = self.renderer.assemble_production(
            intro_card, ending, turn_segments, final_video, episode_dir / "work"
        )
        if self.settings.final_audio_policy in NATIVE_VIDEO_AUDIO_POLICIES:
            asr_report = {
                "status": "skipped",
                "reason": "Seedance native audio creative preview disables ASR",
                "cer": 999.0,
                "turns": [],
            }
        else:
            self.media.enter_stage("audio-evidence")
            asr_report = self._audit_delivered_asr(
                episode_dir, final_video, plan, delivery_timeline
            )
        atomic_write_json(episode_dir / "asr_report.json", asr_report)
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        trace = {
            "novel_id": video_id.rsplit("_", 1)[0],
            "video_id": video_id,
            "source_title": episode.source_title,
            "source_start": episode.source_start,
            "source_end": episode.source_end,
            "source_text_sha256": plan.source_text_sha256,
            "style_fingerprint": plan.style_fingerprint,
            "scenes": [scene.model_dump(mode="json") for scene in plan.scenes],
            "shots": [shot.model_dump(mode="json") for shot in plan.shots],
            "visual_groups": [
                group.model_dump(mode="json") for group in plan.visual_groups
            ],
            "turns": [
                {
                    "unit_id": unit.unit_id,
                    "source_quote": unit.source_quote,
                    "source_quote_sha256": hashlib.sha256(unit.source_quote.encode("utf-8")).hexdigest(),
                    "text": unit.text,
                    "speaker_name": unit.speaker_name,
                    "speaking": unit.speaking,
                    "character_asset_ids": unit.character_asset_ids,
                    "location_asset_id": unit.location_asset_id,
                    "audio_path": unit.audio_path,
                    "keyframe_path": unit.keyframe_path,
                    "clip_path": next(
                        group.raw_video_path
                        for group in plan.visual_groups
                        if unit.unit_id in group.unit_ids
                    ),
                    "subtitle_alignment": unit.subtitle_alignment,
                }
                for unit in plan.units
            ],
        }
        atomic_write_json(episode_dir / "content_trace.json", trace)
        media_qc = inspect_media(
            final_video,
            cover,
            ending,
            ass,
            self.settings,
            episode_dir / "media_qc_report.json",
        )
        admission = evaluate_episode_admission(
            settings=self.settings,
            plan=plan,
            media_qc=media_qc,
            ass=ass,
            clean_video=joined,
            delivered_video=final_video,
            subtitle_events=subtitle_events,
            asr_report=asr_report,
            face_consistency_report=face_consistency_report,
        )
        atomic_write_json(episode_dir / "admission_report.json", admission)
        atomic_write_json(episode_dir / "qc_report.json", admission)
        return admission
