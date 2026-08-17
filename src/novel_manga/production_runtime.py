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

from .admission import evaluate_episode_admission
from .config import Settings
from .face_consistency import evaluate_face_consistency
from .models import Episode, EpisodePlan, StoryBible, VisualStrategy
from .production import SeriesAssetFactory, compile_production_plan, sha256_file, sha256_text
from .production_models import (
    ProductionPlan,
    RuntimeUnit,
    RuntimeVisualGroup,
    SeriesAssetManifest,
)
from .providers.base import ImageResult, MediaProvider
from .qc import inspect_media
from .render import Renderer
from .runtime_backends import RuntimeEvidenceBackends, aggregate_asr
from .sd_dialogue import (
    build_sd_prompt,
    compile_performance_prompt,
    performance_action_only,
)
from .util import atomic_write_json, media_duration, run


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


def build_visual_groups(
    plan: ProductionPlan,
    *,
    target_seconds: float = 13.4,
    max_speed: float = 1.12,
    gap: float = 0.10,
) -> list[RuntimeVisualGroup]:
    """Pack audio turns into continuous shots without crossing scene boundaries."""
    limit = target_seconds * max_speed
    packed: list[list[RuntimeUnit]] = []
    current: list[RuntimeUnit] = []
    current_seconds = 0.0
    for unit in plan.units:
        seconds = float(unit.audio_seconds or 0.0)
        addition = seconds + (gap if current else 0.0)
        if current and (unit.shot_id != current[-1].shot_id or current_seconds + addition > limit):
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
        if merged:
            left = merged[-1]
            left_seconds = sum(float(unit.audio_seconds or 0.0) for unit in left) + gap * (len(left) - 1)
            right_seconds = sum(float(unit.audio_seconds or 0.0) for unit in group) + gap * (len(group) - 1)
            combined_shots = list(dict.fromkeys(unit.shot_id for unit in left + group))
            if (
                left[-1].scene_id == group[0].scene_id
                and (left_seconds < 4.0 or right_seconds < 4.0)
                and left_seconds + right_seconds + gap <= limit
                and len(combined_shots) <= 4
            ):
                left.extend(group)
                continue
        merged.append(list(group))

    groups: list[RuntimeVisualGroup] = []
    previous_group_moved = False
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
        visible_speaker = next((unit for unit in units if unit.speaking), None)
        framing_instruction = (
            visible_speaker.composition_prompt
            if visible_speaker is not None
            else units[0].composition_prompt
        )
        subject_instruction = (
            f"可见说话者{visible_speaker.speaker_name}必须清楚位于这个构图的竖屏安全区，"
            "完整嘴部无遮挡；其他人物只作为必要的关系对象或背景，不得抢占主体。"
            if visible_speaker is not None
            else "本镜主体必须由当前动作和情绪决定，不得把母版中的全部人物机械地重复画进前景。"
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
                    duration=max(4.0, min(14.0, shot_seconds + 0.5)),
                )
            )
        moving_candidates = [
            unit.camera_plan
            for unit in units
            if unit.camera_plan is not None and unit.camera_plan.mode != "locked"
        ]
        selected_camera = (
            max(moving_candidates, key=lambda plan: _camera_mode_rank(plan.mode))
            if moving_candidates and not previous_group_moved
            else _locked_group_camera_plan(
                next((unit.camera_plan for unit in units if unit.camera_plan is not None), None)
            )
        )
        group_moved = selected_camera.mode != "locked"
        previous_group_moved = group_moved
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
                + "不得遮住、替换或重复锁定人声；对白出现时背景自动压低。"
                if ambience or music_cues or sfx_events
                else ""
            )
            + f"{spatial_anchor}"
            "只有在台词、视线目标、道具状态或对方反应形成明确触发时才发生动作变化；"
            "一个节拍只保留一个主要动作，完成后允许短暂停顿，不得用无意义小动作填满时长；"
            "整组只执行上面唯一的摄影机计划，不得叠加其他推拉、横移、环绕、升降或数字缩放。"
            "眼睛先于头部，头部先于肩膀，衣发稍后响应；动作有停顿、加速和减速。"
            "参考图只锁定身份、服装、环境和画风，不锁定静态姿势；不得改变人物年龄、服饰和相对站位。"
            "全程不得出现血液、伤口、破皮或新增可读文字。"
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
            )
        )
    return groups


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
        return sha256_text(
            json.dumps(
                {
                    "text": unit.text,
                    "voice": unit.voice,
                    "emotion": unit.emotion,
                    "audio_plan": unit.audio_plan.model_dump(mode="json"),
                    "delivery_mode": unit.delivery_mode,
                    "tts_model": self.settings.tts_model,
                    "tts_command": self.settings.tts_command,
                    "tts_speed": self._speech_speed(unit),
                    "model_lifecycle_command": self.settings.model_lifecycle_command,
                    "provider": self.settings.provider,
                    # A command string alone cannot identify a mutable local
                    # TTS cache.  Include the addressed WAV so speed/padding
                    # changes invalidate stale attempts and downstream video.
                    "tts_cache_source_sha256": cache_source_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    def _speech_speed(self, unit: RuntimeUnit) -> float | None:
        role_speed = (
            self.settings.tts_narration_speed
            if unit.role == "narrator"
            else self.settings.tts_dialogue_speed
        )
        return role_speed if role_speed is not None else self.settings.tts_speed

    def _prepare_audio(self, episode_dir: Path, unit: RuntimeUnit) -> tuple[dict, dict]:
        output = self._resolve(episode_dir, unit.audio_path)
        meta = output.with_suffix(output.suffix + ".request.json")
        identity = self._audio_identity(unit)
        selected_path: Path | None = None
        selected_attempt = 0
        selected_asr: dict | None = None
        if output.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                selected_path = output
                selected_attempt = int(saved.get("attempt", 0))
                selected_asr = self.evidence.transcribe(unit.unit_id, unit.text, output)
                if not (
                    selected_asr.get("status") == "passed"
                    and float(selected_asr.get("cer", float("inf"))) <= self.settings.max_turn_cer
                ):
                    selected_path = None
        for attempt in range(1, self.settings.max_unit_attempts + 1):
            if selected_path is not None:
                break
            attempt_path = (
                episode_dir
                / "work"
                / "turn_audio_attempts"
                / unit.unit_id
                / identity[:8]
                / f"attempt_{attempt:02d}.wav"
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
                    speed=self._speech_speed(unit),
                )
            row = self.evidence.transcribe(unit.unit_id, unit.text, attempt_path)
            selected_path, selected_attempt, selected_asr = attempt_path, attempt, row
            if row.get("status") == "passed" and float(row.get("cer", float("inf"))) <= self.settings.max_turn_cer:
                break
        assert selected_path is not None and selected_asr is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        if selected_path.resolve() != output.resolve():
            shutil.copy2(selected_path, output)
        seconds = media_duration(output)
        if seconds > 13.5:
            selected_asr = {
                **selected_asr,
                "status": "failed",
                "cer": 999.0,
                "error": f"audio duration {seconds:.3f}s exceeds the 13.5s speaking-turn limit",
            }
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
                "speed": self._speech_speed(unit),
                "text_sha256": sha256_text(unit.text),
            },
        )
        return selected_asr, alignment

    def _visual_identity(
        self,
        unit: RuntimeUnit,
        audio: Path,
        reference_board: Path,
    ) -> str:
        return sha256_text(
            json.dumps(
                {
                    "keyframe_prompt": unit.keyframe_prompt,
                    "motion_prompt": unit.motion_prompt,
                    "audio_sha256": sha256_file(audio),
                    "reference_board_sha256": sha256_file(reference_board),
                    "image_model": self.settings.image_model,
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
        direct_h3_assets = self._direct_h3_assets(novel_dir, unit, series_assets)
        identity = self._visual_identity(unit, audio, reference_board)
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
            elif first_keyframe is None:
                first_keyframe = attempt_root / "attempt_01" / "keyframe.jpeg"
                if self.settings.reuse_existing_keyframes and canonical_keyframe.is_file():
                    saved = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
                    expected_sha = saved.get("keyframe_sha256")
                    if not expected_sha or expected_sha == sha256_file(canonical_keyframe):
                        first_keyframe.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(canonical_keyframe, first_keyframe)
                        reused_keyframe = True
                    else:
                        first_keyframe = self.assets._ensure_image(
                            unit.keyframe_prompt
                            + " 本单元为独立构图第1次生成，禁止复用其他台词的完整构图。",
                            first_keyframe,
                            reference=reference_board,
                        ).path
                else:
                    first_keyframe = self.assets._ensure_image(
                        unit.keyframe_prompt
                        + " 本单元为独立构图第1次生成，禁止复用其他台词的完整构图。",
                        first_keyframe,
                        reference=reference_board,
                    ).path
                selected_keyframe = first_keyframe
            else:
                reused_keyframe = True
                selected_keyframe = first_keyframe

        assert selected_keyframe is not None
        return {
            "episode_dir": episode_dir,
            "unit": unit,
            "audio": audio,
            "canonical_video": canonical_video,
            "canonical_keyframe": canonical_keyframe,
            "reference_board": reference_board,
            "identity": identity,
            "meta": meta,
            "selected_video": selected_video,
            "selected_keyframe": selected_keyframe,
            "selected_attempt": selected_attempt,
            "selected_used_reference_audio": selected_used_reference_audio,
            "existing_keyframe_attempts": existing_keyframe_attempts,
            "reused_keyframe": reused_keyframe,
            "use_reference_audio": use_reference_audio,
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
        identity: str = prepared["identity"]
        meta: Path = prepared["meta"]
        selected_video: Path | None = prepared["selected_video"]
        selected_keyframe: Path = prepared["selected_keyframe"]
        selected_attempt: int = prepared["selected_attempt"]
        selected_used_reference_audio: bool = prepared["selected_used_reference_audio"]
        existing_keyframe_attempts: set[int] = prepared["existing_keyframe_attempts"]
        reused_keyframe: bool = prepared["reused_keyframe"]
        use_reference_audio: bool = prepared["use_reference_audio"]
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
                    keyframe_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(selected_keyframe, keyframe_path)
                    reused_keyframe = True
                try:
                    attempt_uses_reference_audio = use_reference_audio and (
                        self.settings.provider == "command" or attempt <= 2
                    )
                    if not video_path.is_file():
                        video_kwargs = {}
                        if additional_video_images:
                            video_kwargs["additional_images"] = additional_video_images
                        self.media.create_video(
                            (
                                unit.motion_prompt
                                if attempt_uses_reference_audio
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
            shutil.copy2(selected_keyframe, canonical_keyframe)
        if selected_video.resolve() == canonical_video.resolve() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            selected_used_reference_audio = bool(
                saved.get("reference_audio_used", use_reference_audio)
            )
            visual_source = str(saved.get("visual_source", ""))
        if not visual_source.startswith("local-keyframe-motion-fallback"):
            visual_source = (
                f"{self.settings.video_model}-direct-reusable-assets-"
                + ("reference-audio" if selected_used_reference_audio else "no-audio-reference")
                if visual_input_strategy.startswith("h3-")
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
        return first.model_copy(
            update={
                "unit_id": group.group_id,
                "shot_id": group.shot_ids[0],
                "role": "narrator",
                "speaker_name": "旁白",
                "speaking": False,
                "reference_audio_required": any(
                    units_by_id[unit_id].speaking for unit_id in group.unit_ids
                ),
                "text": group.combined_text[:500],
                "character_asset_ids": group.character_asset_ids,
                "direct_video_character_asset_ids": (
                    group.direct_video_character_asset_ids
                ),
                "location_asset_id": group.location_asset_id,
                "keyframe_prompt": group.keyframe_prompt,
                "motion_prompt": group.motion_prompt,
                # This is the H3 performance track, not the delivery track:
                # visible dialogue remains audible while narration and
                # off-screen voices are duration-preserving silence. Final
                # rendering remuxes the complete locked group.audio_path.
                "audio_path": group.video_audio_path,
                "keyframe_path": group.keyframe_path,
                "raw_video_path": group.raw_video_path,
                "segment_path": group.segment_path,
                "audio_seconds": group.audio_seconds,
                "visual_strategy": group.visual_strategy,
                "keyframe_reasons": group.keyframe_reasons,
            }
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
            asr_row, alignment = self._prepare_audio(episode_dir, unit)
            asr_rows.append(asr_row)
            alignments.append(alignment)
        tts_asr_report = aggregate_asr(asr_rows)
        tts_asr_report["audio_source"] = "locked_tts_reference_before_video"
        atomic_write_json(episode_dir / "tts_asr_report.json", tts_asr_report)
        atomic_write_json(episode_dir / "alignment_report.json", {"units": alignments})
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        visual_target_seconds = min(13.4, self.settings.video_max_seconds - 0.5)
        plan.visual_groups = build_visual_groups(
            plan,
            target_seconds=visual_target_seconds,
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
        atomic_write_json(
            episode_dir / "visual_group_plan.json",
            {
                "policy": "continuous-long-shot-v1",
                "turn_count": len(plan.units),
                "visual_group_count": len(plan.visual_groups),
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
        atomic_write_json(
            episode_dir / "visual_generation_report.json",
            {
                "workflow": "continuous-long-shot-v1",
                "turn_count": len(plan.units),
                "visual_group_count": len(plan.visual_groups),
                "groups": visual_rows,
            },
        )
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        turn_segments = []
        delivery_timeline = []
        story_cursor = 0.0
        for group in plan.visual_groups:
            segment, duration = self.renderer.mux_visual_group(
                self._resolve(episode_dir, group.raw_video_path),
                self._resolve(episode_dir, group.audio_path),
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
