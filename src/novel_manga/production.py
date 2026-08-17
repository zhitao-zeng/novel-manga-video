from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from .config import Settings
from .models import (
    CameraBeat,
    CameraPlan,
    Character,
    Episode,
    EpisodePlan,
    MotionBeat,
    PerformancePlan,
    ScriptTurn,
    StoryBible,
    TurnDelivery,
)
from .production_models import (
    AssetRecord,
    ProductionPlan,
    RuntimeScene,
    RuntimeShot,
    RuntimeUnit,
    SeriesAssetManifest,
)
from .providers.base import ImageResult, MediaProvider
from .sd_dialogue import build_sd_prompt
from .util import atomic_write_json


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_stale(path: Path, old_hash: str) -> None:
    if not path.exists():
        return
    archived = path.with_name(f"{path.stem}.stale-{old_hash[:8]}{path.suffix}")
    if archived.exists():
        archived = path.with_name(f"{path.stem}.stale-{old_hash[:12]}{path.suffix}")
    shutil.move(path, archived)


class SeriesAssetFactory:
    def __init__(self, settings: Settings, provider: MediaProvider):
        self.settings = settings
        self.provider = provider

    @staticmethod
    def keyframe_cast_guard(
        unit: RuntimeUnit,
        assets: SeriesAssetManifest | None,
    ) -> str:
        """Compile a named-cast identity gate for an episode keyframe.

        Scene references can contain recurring characters that are absent
        from the current shot. Without an explicit gate an image editor may
        select the most salient person in the scene instead of the scripted
        actor. Anonymous background extras remain allowed.
        """

        if assets is None:
            return ""
        character_map = {record.asset_id: record for record in assets.characters}
        current = [
            character_map[asset_id]
            for asset_id in unit.character_asset_ids
            if asset_id in character_map
        ]
        if not current:
            return ""
        current_ids = {record.asset_id for record in current}
        excluded = [
            record.name for record in assets.characters if record.asset_id not in current_ids
        ]
        allowed = "、".join(
            f"{record.name}（对应{record.asset_id}定妆资产）" for record in current
        )
        guard = (
            f"【具名角色身份门禁】本镜允许出现的具名角色严格限定为：{allowed}。"
            "每个角色必须逐一匹配自己的定妆资产，禁止互换脸型、发型、年龄、服装或主色；"
            "角色资产的排列顺序不是随意候选列表，不得用参考板中更显眼的人替换当前演员。"
        )
        if excluded:
            guard += (
                f"本系列其他具名角色（{'、'.join(excluded)}）本镜不得出场、不得操作核心道具、"
                "不得替换当前演员；场景如需群众，只能使用不具名且不抢主体的背景群众。"
            )
        return guard

    def _ensure_image(
        self,
        prompt: str,
        output: Path,
        *,
        reference: Path | None = None,
    ) -> ImageResult:
        identity = {
            "prompt_sha256": sha256_text(prompt),
            "reference_sha256": sha256_file(reference) if reference and reference.is_file() else None,
            "provider": self.settings.provider,
            "image_model": self.settings.image_model,
            "image_command_sha256": (
                sha256_text(self.settings.image_command) if self.settings.image_command else None
            ),
            "local_image_prompt_policy": (
                self.settings.local_image_prompt_policy
                if self.settings.provider == "command"
                else None
            ),
            "model_lifecycle_command_sha256": (
                sha256_text(self.settings.model_lifecycle_command)
                if self.settings.model_lifecycle_command
                else None
            ),
        }
        identity_hash = sha256_text(json.dumps(identity, sort_keys=True))
        meta = output.with_suffix(output.suffix + ".request.json")
        if output.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity_hash:
                if "provider_reference" in saved:
                    saved.pop("provider_reference", None)
                    atomic_write_json(meta, saved)
                return ImageResult(path=output)
            if self.settings.reuse_existing_assets:
                atomic_write_json(
                    meta,
                    {
                        **identity,
                        "request_sha256": identity_hash,
                        "artifact_sha256": sha256_file(output),
                        "origin": "locked-existing-asset",
                        "previous_request_sha256": saved.get("request_sha256"),
                    },
                )
                return ImageResult(path=output)
            _archive_stale(output, str(saved.get("request_sha256", "unknown")))
            _archive_stale(meta, str(saved.get("request_sha256", "unknown")))
            output.with_suffix(output.suffix + ".task.json").unlink(missing_ok=True)
        elif output.is_file() and self.settings.reuse_existing_assets:
            atomic_write_json(
                meta,
                {
                    **identity,
                    "request_sha256": identity_hash,
                    "artifact_sha256": sha256_file(output),
                    "origin": "locked-existing-asset",
                    "previous_request_sha256": None,
                },
            )
            return ImageResult(path=output)
        result = self.provider.create_image(prompt, output, reference=reference)
        atomic_write_json(
            meta,
            {
                **identity,
                "request_sha256": identity_hash,
            },
        )
        return result

    @staticmethod
    def _rendering_direction(bible: StoryBible) -> str:
        style = bible.visual_style.casefold()
        if any(token in style for token in ("3d", "三维", "cg", "pbr")):
            return (
                "高精度半写实3D国漫CG人物资产，国产仙侠游戏过场动画质感，"
                "东方审美面孔、自然人体比例、细腻发丝、自然皮肤明暗，"
                "PBR丝绸、皮革、石材与金属材质，电影柔光和真实景深；"
                "不要二维插画、墨线赛璐璐、真人照片、Q版或欧美卡通"
            )
        return (
            "抖音国风漫剧常见的精致二维赛璐璐人物资产，清晰墨线、平涂色块、"
            "适度电影光影；不要真人照片、写实短剧、3D、Q版或欧美卡通"
        )

    @staticmethod
    def _character_prompt(bible: StoryBible, name: str, appearance: str, wardrobe: str) -> str:
        return (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"角色资产：{name}；固定外貌：{appearance}；固定服装：{wardrobe}。"
            "只画一个人物且只出现一次，单人四分之三正面全身站姿，脸部清晰，头脚完整，"
            "身体比例自然，双手自然放松，不拿食物、纸袋、武器或任何剧情道具。"
            "纯色简洁背景，不要多视角设定表、分身、镜像人物、局部小头像或拼贴。"
            f"{SeriesAssetFactory._rendering_direction(bible)}；"
            "不要文字、Logo或水印。"
        )

    @staticmethod
    def _expression_prompt(bible: StoryBible, name: str) -> str:
        return (
            f"保持参考图中{ name }的脸型、年龄、发型、服装和{bible.style_fingerprint}风格完全一致。"
            "只画这个人物且只出现一次，生成四分之三正面单人半身表情锚点：克制的中性神情，"
            "眼睛、眉形和嘴部清晰无遮挡，肩颈与服装领口完整。"
            "不要表情九宫格、多头像、分身、拼贴；双手不持任何物品，简单背景，"
            f"{SeriesAssetFactory._rendering_direction(bible)}；不要文字、Logo或水印。"
        )

    @staticmethod
    def _location_prompt(bible: StoryBible, location: str) -> str:
        return (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"场景资产：{location}。固定建筑结构、空间布局、关键物品、天气、时间和光线方向。"
            "竖屏建立镜头与对话主角度可复用背景板，前景不出现人物。"
            f"{SeriesAssetFactory._rendering_direction(bible)}；不要文字、Logo或水印。"
        )

    def build(self, root: Path, bible: StoryBible) -> SeriesAssetManifest:
        root.mkdir(parents=True, exist_ok=True)
        characters: list[AssetRecord] = []
        locations: list[AssetRecord] = []
        voice_assignments = {"narrator": self.settings.voice_map.get("narrator", self.settings.tts_voice)}
        fallback_voices = ("coral", "verse", "sage", "ash", "nova", "alloy")
        source_characters = bible.characters or [
            Character(
                name="主角",
                role="主角",
                appearance="黑发、清晰稳定的东亚面孔",
                wardrobe="符合故事时代的固定主色服装",
            )
        ]
        character_rows = []
        location_rows = []
        self.provider.enter_stage("image-base")
        for index, character in enumerate(source_characters, start=1):
            asset_id = f"character_{index:03d}"
            directory = root / "characters" / asset_id
            prompt = self._character_prompt(bible, character.name, character.appearance, character.wardrobe)
            spec = {
                "asset_id": asset_id,
                "name": character.name,
                "role": character.role,
                "gender": character.gender,
                "age": character.age,
                "appearance": character.appearance,
                "wardrobe": character.wardrobe,
                "style_fingerprint": bible.style_fingerprint,
                "prompt": prompt,
            }
            atomic_write_json(directory / "spec.json", spec)
            primary = self._ensure_image(prompt, directory / "turnaround.jpeg")
            character_rows.append((character, asset_id, directory, prompt, primary))
            voice_assignments[character.name] = self.settings.voice_map.get(
                character.name, fallback_voices[(index - 1) % len(fallback_voices)]
            )
        for index, location in enumerate(dict.fromkeys(bible.locations or ["原文主要场景"]), start=1):
            asset_id = f"location_{index:03d}"
            directory = root / "locations" / asset_id
            prompt = self._location_prompt(bible, location)
            atomic_write_json(
                directory / "spec.json",
                {
                    "asset_id": asset_id,
                    "name": location,
                    "style_fingerprint": bible.style_fingerprint,
                    "continuity": "固定空间布局、物品锚点、天气、时间、光线方向",
                    "prompt": prompt,
                },
            )
            image = self._ensure_image(prompt, directory / "establishing.jpeg")
            location_rows.append(
                (asset_id, location, directory, prompt, image)
            )

        self.provider.enter_stage("image-edit")
        for character, asset_id, directory, prompt, primary in character_rows:
            expression_prompt = self._expression_prompt(bible, character.name)
            secondary = self._ensure_image(
                expression_prompt,
                directory / "expressions.jpeg",
                reference=primary.path,
            )
            characters.append(
                AssetRecord(
                    asset_id=asset_id,
                    kind="character",
                    name=character.name,
                    spec_path=str((directory / "spec.json").relative_to(root.parent)),
                    primary_image=str(primary.path.relative_to(root.parent)),
                    secondary_image=str(secondary.path.relative_to(root.parent)),
                    prompt_sha256=sha256_text(prompt + expression_prompt),
                )
            )
        for asset_id, location, directory, prompt, image in location_rows:
            locations.append(
                AssetRecord(
                    asset_id=asset_id,
                    kind="location",
                    name=location,
                    spec_path=str((directory / "spec.json").relative_to(root.parent)),
                    primary_image=str(image.path.relative_to(root.parent)),
                    prompt_sha256=sha256_text(prompt),
                )
            )
        # Voice-only roles such as an off-screen crowd or a phone caller need
        # stable casting even when no reusable character image is required.
        for role, voice in self.settings.voice_map.items():
            voice_assignments.setdefault(role, voice)
        manifest = SeriesAssetManifest(
            style_fingerprint=bible.style_fingerprint,
            characters=characters,
            locations=locations,
            voice_assignments=voice_assignments,
        )
        atomic_write_json(root / "manifest.json", manifest.model_dump(mode="json"))
        return manifest

    def reference_board(
        self,
        episode_dir: Path,
        unit: RuntimeUnit,
        assets: SeriesAssetManifest,
        novel_dir: Path,
    ) -> Path:
        output = episode_dir / "work" / "reference_boards" / f"{unit.unit_id}.jpeg"
        character_map = {record.asset_id: record for record in assets.characters}
        location_map = {record.asset_id: record for record in assets.locations}
        if unit.speaking:
            if not unit.character_asset_ids or unit.character_asset_ids[0] not in character_map:
                raise ValueError(f"{unit.unit_id} has no locked visible-speaker asset")
            # A dialogue keyframe must never receive another character as a
            # visual reference. Passing the full speaker turnaround directly
            # also avoids the old vertical-board crop that could remove the
            # speaker's face while preserving a second character's face.
            paths = [novel_dir / character_map[unit.character_asset_ids[0]].primary_image]
            board_mode = "visible_speaker_identity_only"
        else:
            paths = [novel_dir / location_map[unit.location_asset_id].primary_image]
            paths.extend(
                novel_dir / character_map[asset_id].primary_image
                for asset_id in unit.character_asset_ids[:2]
                if asset_id in character_map
            )
            board_mode = "narration_scene_and_cast"
        identity = sha256_text(
            board_mode + "|" + "|".join(sha256_file(path) for path in paths)
        )
        meta = output.with_suffix(output.suffix + ".request.json")
        if output.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                return output
        if unit.speaking:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths[0], output)
            atomic_write_json(
                meta,
                {
                    "request_sha256": identity,
                    "mode": board_mode,
                    "sources": [str(path) for path in paths],
                },
            )
            return output
        canvas = Image.new("RGB", (self.settings.width, self.settings.height), (20, 22, 30))
        draw = ImageDraw.Draw(canvas)
        panel_height = self.settings.height // len(paths)
        for index, path in enumerate(paths):
            with Image.open(path).convert("RGB") as source:
                scale = max(self.settings.width / source.width, panel_height / source.height)
                resized = source.resize((round(source.width * scale), round(source.height * scale)))
                left = (resized.width - self.settings.width) // 2
                top = (resized.height - panel_height) // 2
                crop = resized.crop((left, top, left + self.settings.width, top + panel_height))
                canvas.paste(crop, (0, index * panel_height))
            draw.rectangle((0, index * panel_height, self.settings.width - 1, (index + 1) * panel_height - 1), outline=(245, 210, 120), width=4)
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, "JPEG", quality=94, subsampling=0)
        atomic_write_json(
            meta,
            {
                "request_sha256": identity,
                "mode": board_mode,
                "sources": [str(path) for path in paths],
            },
        )
        return output


def _resolve_location_id(location: str, assets: SeriesAssetManifest) -> str:
    exact = next((record.asset_id for record in assets.locations if record.name == location), None)
    return exact or assets.locations[0].asset_id


def _turns_for_shot(plan_shot) -> list[ScriptTurn]:
    if plan_shot.turns:
        return plan_shot.turns
    return [
        ScriptTurn(
            text=plan_shot.subtitle,
            source_quote=plan_shot.source_quote,
        )
    ]


_DIALOGUE_COMPOSITIONS = (
    "平视正面胸像，人物居中，场景纵深位于身后并保持浅景深",
    "左前方四分之三胸像，人物位于右侧三分线，左后方保留场景标志物",
    "右前方四分之三胸像，人物位于左侧三分线，右后方保留场景标志物",
    "轻微低机位正面近景，人物略偏左，背景建筑线条向人物汇聚",
    "轻微高机位四分之三近景，人物略偏右，背景地面纹理形成纵深",
    "较紧的肩部正面近景，人物位于中央上半部，背景只保留柔化色块",
    "较宽的胸像近景，人物位于左侧，右侧留出与剧情方向一致的空间",
    "较宽的胸像近景，人物位于右侧，左侧留出与剧情方向一致的空间",
    "左侧轮廓光的正面近景，人物居中，远景光源形成稳定层次",
    "右侧轮廓光的四分之三近景，人物略偏左，背景保持克制",
    "平视侧前方近景，人物目光朝画面内侧，背后保留一处环境锚点",
    "轻微仰拍的肩部近景，人物目光稳定，场景旗帜或立柱在远处虚化",
    "轻微俯拍的胸像近景，人物位于中轴，场景台阶或地面在远处虚化",
)

_DIALOGUE_AXIS_COMPOSITIONS = (
    "左前方四分之三胸像，人物位于画面右侧三分线，目光朝画面左侧内收，左后方保留场景标志物",
    "右前方四分之三胸像，人物位于画面左侧三分线，目光朝画面右侧内收，右后方保留场景标志物",
)


def _dialogue_composition(
    shot_index: int,
    turn_index: int,
    speaker_name: str,
    speaker_slot: int | None = None,
) -> str:
    """Pick a repeatable side of the action axis for one visible speaker.

    The old index-only rotation could put the same speaker on alternating sides
    of the frame in one conversation.  A stable speaker hash keeps eyelines and
    screen direction continuous while retaining some episode-level variety.
    """

    del shot_index, turn_index
    if speaker_slot is not None:
        return _DIALOGUE_AXIS_COMPOSITIONS[speaker_slot % 2]
    stable_index = int(hashlib.sha256(speaker_name.encode("utf-8")).hexdigest()[:8], 16)
    return _DIALOGUE_COMPOSITIONS[stable_index % len(_DIALOGUE_COMPOSITIONS)]


def _character_identity(character: Character) -> str:
    appearance = character.appearance.removeprefix("固定")
    wardrobe = character.wardrobe.removeprefix("固定")
    return (
        f"{character.name}，{character.gender}，{character.age}，"
        f"外貌锚点：{appearance}；服装锚点：{wardrobe}"
    )


def _dialogue_visual_context(shot, speaker_name: str) -> str:
    """Keep environment/blocking clauses without leaking another named actor.

    Dialogue units intentionally use a single visible-speaker reference.  The
    shot-level visual prompt can still carry useful scene anchors, but clauses
    naming another actor would fight that identity gate and reintroduce cast
    swaps.  Clause filtering preserves the current speaker's position and
    props while leaving the listener off screen.
    """
    other_characters = [name for name in shot.characters if name != speaker_name]
    clauses = [
        clause.strip()
        for clause in re.split(r"[，,；;。]+", shot.visual_prompt)
        if clause.strip()
    ]
    safe = [
        clause
        for clause in clauses
        if not any(name in clause for name in other_characters)
    ]
    return "，".join(safe)


def _performance_plan_for(shot, turn: ScriptTurn) -> PerformancePlan:
    other_characters = (
        [name for name in shot.characters if name != turn.speaker_name]
        if turn.speaking
        else []
    )
    if shot.performance_plan is not None and not any(
        name in shot.performance_plan.model_dump_json() for name in other_characters
    ):
        return shot.performance_plan
    visible_action = shot.visual_prompt.split("，内容健康克制", 1)[0].rstrip("。，")
    if turn.speaking:
        if any(name in visible_action for name in other_characters):
            visible_action = (
                "说到核心信息时，一只手完成与本句道具或事件有关的明确动作，"
                "上身和身体重心随动作自然变化"
            )
        return PerformancePlan(
            objective=(
                f"让{turn.speaker_name}在说话过程中完成与剧情有关的动作，"
                f"情绪从克制自然过渡到{turn.emotion}，不是固定姿势说完整段台词"
            ),
            start_state=(
                f"{turn.speaker_name}处于动作开始前一瞬，嘴巴自然闭合，"
                "目光和身体重心尚未完全转向对话对象"
            ),
            motion_beats=[
                MotionBeat(
                    phase="opening",
                    trigger="参考音频人声即将开始",
                    action="眼睛先移向对话对象，头部随后小幅转动，肩膀和上身稍后跟随并吸气",
                    reaction="身体重心从后向前移动，手指先出现细微准备动作",
                    expression_transition=f"从克制转为{turn.emotion}",
                ),
                MotionBeat(
                    phase="development",
                    trigger="说到本句核心信息",
                    action=visible_action,
                    reaction=(
                        "主要手部动作带动肩膀与身体重心变化，视线短暂跟随道具或动作后重新看向对方"
                    ),
                    expression_transition=f"{turn.emotion}逐渐清晰但不过度夸张",
                ),
                MotionBeat(
                    phase="resolution",
                    trigger="台词接近结束",
                    action="动作减速，抬眼确认对方反应，说完后自然闭嘴并停住",
                    reaction="肩膀放松，头发和衣摆比身体稍晚停止",
                    expression_transition=f"停在{turn.emotion}的收束表情",
                ),
            ],
            end_state="台词完整结束，人物自然闭嘴，动作结果和最终表情清楚可见",
        )
    return PerformancePlan(
        objective=f"通过连续动作讲清“{turn.text}”，不是静态插画加数字推近",
        start_state="人物处于主要动作发生前一瞬，保留明确的移动方向和动作空间",
        motion_beats=[
            MotionBeat(
                phase="opening",
                trigger="叙事事件开始",
                action="视线先找到事件目标，头部随后转动，肩膀和身体重心依次跟随",
                reaction="手部开始为主要动作做准备",
            ),
            MotionBeat(
                phase="development",
                trigger="人物确认当前事件",
                action=visible_action,
                reaction="动作产生可见的身体位移、道具惯性或环境反馈",
            ),
            MotionBeat(
                phase="resolution",
                trigger="主要动作完成",
                action="人物完成一个清楚的收束反应并停在下一镜可衔接的位置",
                reaction="呼吸、头发和衣物惯性稍后停止",
            ),
        ],
        end_state="本镜事件结果清楚可读，人物嘴巴保持闭合",
    )


_CAMERA_MOVEMENT_CUES = (
    "进入", "走进", "冲入", "跑", "追", "后退", "上前", "离开",
    "揭开", "打开", "推开", "拉开", "发现", "出现", "现身", "登场", "闯入", "逼近",
)
_CAMERA_EMPHASIS_CUES = (
    "高潮", "climax", "反转", "揭晓", "真相", "冲突", "爆发", "决裂", "突袭", "危机",
)


def _camera_mode_for(shot, turn: ScriptTurn) -> tuple[str, str, int]:
    requested_plan = shot.camera_plan
    requested_mode = requested_plan.mode if requested_plan is not None else "locked"
    requested_motivation = (
        requested_plan.motivation if requested_plan is not None else ""
    )
    text = " ".join(
        filter(
            None,
            (
                shot.scene_job,
                shot.visual_prompt,
                shot.motion_prompt,
                turn.text,
                turn.emotion,
                requested_motivation,
            ),
        )
    ).casefold()
    has_emphasis = any(cue in text for cue in _CAMERA_EMPHASIS_CUES)
    has_displacement_or_reveal = any(cue in text for cue in _CAMERA_MOVEMENT_CUES)
    if requested_mode == "motivated_emphasis":
        return requested_mode, requested_motivation, 400
    if has_emphasis:
        return (
            "motivated_emphasis",
            "关键冲突、权力变化或信息揭示需要一次强调性重新构图",
            300,
        )
    if requested_mode == "motivated_subtle":
        return requested_mode, requested_motivation, 250
    if has_displacement_or_reveal:
        return (
            "motivated_subtle",
            "人物发生明确位移或画面需要揭示新信息",
            200,
        )
    return (
        "locked",
        "本镜由人物动作、视线和表情承担动态，无需摄影机移动",
        0,
    )


def _camera_plan_for(
    shot,
    turn: ScriptTurn,
    composition: str,
    *,
    forced_mode: str | None = None,
    forced_motivation: str | None = None,
) -> CameraPlan:
    location = shot.location or "当前场景"
    mode, motivation, _ = _camera_mode_for(shot, turn)
    if forced_mode is not None:
        mode = forced_mode
        motivation = forced_motivation or motivation
    action_axis = f"{location}首次建立的人物视线或运动轴；摄影机始终停留在同一侧"
    screen_direction = (
        f"{turn.speaker_name}始终保持“{composition}”所建立的画面侧和目光方向；"
        "除非画面完整展示走位，否则人物不得左右互换"
    )
    if mode == "locked":
        return CameraPlan(
            mode=mode,
            motivation=motivation,
            action_axis=action_axis,
            screen_direction=screen_direction,
            start_position=composition,
            camera_beats=[
                CameraBeat(
                    phase="opening",
                    trajectory="锁定机位，摄影机保持完全静止",
                    framing="依靠人物视线、手势、身体重心和画内走位保持构图活力",
                    parallax="不制造摄影机视差；前中远景和环境锚点保持固定",
                ),
                CameraBeat(
                    phase="resolution",
                    trajectory="继续锁定机位，让表情和动作结果至少停留一拍",
                    framing="不重新构图、不推拉、不环绕，清楚保留最终反应",
                    parallax="背景结构、人物屏幕位置和空间轴线保持稳定",
                ),
            ],
            end_position="与起始机位相同的稳定机位",
        )
    amplitude = "短距离" if mode == "motivated_subtle" else "明确但克制的短距离"
    return CameraPlan(
        mode=mode,
        motivation=motivation,
        action_axis=action_axis,
        screen_direction=screen_direction,
        start_position=composition,
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory=f"由人物位移或信息揭示触发，沿行动轴同侧{amplitude}横移一次",
                framing="只跟随主要动作重新构图，保留动作方向一侧的空间",
                parallax=f"{location}近处门框、桌沿或货架移动较快，远处背景移动较慢",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="唯一一次横移完成后减速停住，不再追加环绕、推进或升降",
                framing="停在能读清最终表情与动作结果的位置，并保持至少一拍",
                parallax="横向位移逐渐停止，背景运动自然收束",
            ),
        ],
        end_position="行动轴同侧的稳定机位，为下一镜保留方向一致的衔接",
    )


def compile_production_plan(
    video_id: str,
    episode: Episode,
    plan: EpisodePlan,
    bible: StoryBible,
    assets: SeriesAssetManifest,
) -> ProductionPlan:
    character_ids = {record.name: record.asset_id for record in assets.characters}
    speaker_slots = {name: index for index, name in enumerate(character_ids)}
    camera_modes = _camera_movement_budget(plan)
    character_specs = {character.name: character for character in bible.characters}
    scenes: list[RuntimeScene] = []
    shots: list[RuntimeShot] = []
    units: list[RuntimeUnit] = []
    current_scene_key: tuple[str, str] | None = None
    scene_index = 0
    for shot in plan.shots:
        location_id = _resolve_location_id(shot.location, assets)
        scene_key = (location_id, shot.scene_job)
        if scene_key != current_scene_key:
            scene_index += 1
            current_scene_key = scene_key
            scenes.append(
                RuntimeScene(
                    scene_id=f"scene_{scene_index:03d}",
                    index=scene_index,
                    location_asset_id=location_id,
                    narrative_job=shot.scene_job,
                    shot_ids=[],
                )
            )
        scene = scenes[-1]
        shot_id = f"shot_{shot.index:03d}"
        unit_ids = []
        for turn_index, turn in enumerate(_turns_for_shot(shot), start=1):
            unit_id = f"{shot_id}_turn_{turn_index:02d}"
            source_quote = turn.source_quote or shot.source_quote
            if "".join(source_quote.split()) not in "".join(episode.source_text.split()):
                source_quote = shot.source_quote
            if "".join(source_quote.split()) not in "".join(episode.source_text.split()):
                raise ValueError(f"{unit_id} source_quote is not grounded in the source episode")
            if turn.speaking and turn.speaker_name not in character_ids:
                raise ValueError(
                    f"{unit_id} visible speaker {turn.speaker_name!r} has no locked character asset"
                )
            if turn.role != "narrator" and "".join(turn.text.split()) not in "".join(source_quote.split()):
                raise ValueError(
                    f"{unit_id} character utterance is not an exact substring of its grounded source quote"
                )
            if turn.role != "narrator" and turn.speaker_name not in assets.voice_assignments:
                raise ValueError(
                    f"{unit_id} character voice {turn.speaker_name!r} has no locked voice assignment"
                )
            visible_character_ids = [
                character_ids[name] for name in shot.characters if name in character_ids
            ]
            if turn.speaking and turn.speaker_name in character_ids:
                speaker_id = character_ids[turn.speaker_name]
                # Dialogue units are deliberately single-speaker close-ups.
                # Other shot characters remain represented by their own turns,
                # but must not contaminate this unit's identity reference.
                visible_character_ids = [speaker_id]
            visible_character_ids = list(dict.fromkeys(visible_character_ids))
            voice = assets.voice_assignments.get(
                turn.speaker_name if turn.role != "narrator" else "narrator",
                assets.voice_assignments["narrator"],
            )
            composition = (
                _dialogue_composition(
                    shot.index,
                    turn_index,
                    turn.speaker_name,
                    speaker_slots.get(turn.speaker_name),
                )
                if turn.speaking
                else f"{shot.shot_scale}竖屏构图，场景具有前景、中景和远景层次"
            )
            if turn.speaking:
                visual_context = _dialogue_visual_context(shot, turn.speaker_name)
                if (
                    turn.speaker_name in visual_context
                    and "画面左侧" in visual_context
                    and "看向右" in visual_context
                ):
                    composition = _DIALOGUE_AXIS_COMPOSITIONS[1]
                elif (
                    turn.speaker_name in visual_context
                    and "画面右侧" in visual_context
                    and "看向左" in visual_context
                ):
                    composition = _DIALOGUE_AXIS_COMPOSITIONS[0]
            performance_plan = _performance_plan_for(shot, turn)
            camera_mode, camera_motivation = camera_modes[shot.index]
            camera_plan = _camera_plan_for(
                shot,
                turn,
                composition,
                forced_mode=camera_mode,
                forced_motivation=camera_motivation,
            )
            if turn.speaking:
                speaker = character_specs[turn.speaker_name]
                actor_identity = _character_identity(speaker)
                keyframe_prompt = (
                    f"系列风格指纹 {bible.style_fingerprint}。视觉风格：{bible.visual_style}。"
                    f"参考图只锁定唯一角色身份、服装和画风：{actor_identity}；"
                    "不要复制参考图中的静态姿势、画面位置或摄影机构图。"
                    f"场景明确为{shot.location or assets.locations[0].name}，背景适度虚化。"
                    f"分镜视觉约束：{visual_context}。"
                    f"人物即将表达的剧情信息是“{turn.text}”，只把语义转化为表情、视线和动作，"
                    "画面中不得出现这句文字。"
                    f"这是动作发生前一瞬的可运动起始帧：{performance_plan.start_state}。"
                    f"摄影机起始位置：{camera_plan.start_position}。{camera_plan.screen_direction}。"
                    "画面必须为人物后续动作保留空间，"
                    f"不得提前画出动作终点“{performance_plan.end_state}”。"
                    f"只画{turn.speaker_name}单人，情绪为{turn.emotion}，"
                    "脸和完整嘴部位于竖屏安全区，嘴巴自然闭合且无遮挡。"
                    "不得出现被对话者、其他前景人物、文字、字幕、气泡、Logo或水印。"
                )
                motion_prompt = build_sd_prompt(
                    turn.speaker_name,
                    turn.text,
                    shot.motion_prompt,
                    use_reference_audio=True,
                    actor_description=actor_identity,
                    composition_prompt=composition,
                    emotion=turn.emotion,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                )
            else:
                actor_identity = None
                keyframe_prompt = (
                    f"系列风格指纹 {bible.style_fingerprint}。参考板只锁定场景、角色身份、服装、色彩和光线；"
                    "不要复制参考板中的静态姿势和摄影机构图。"
                    f"分镜视觉约束：{shot.visual_prompt}。"
                    f"当前叙事信息是“{turn.text}”，只用人物行为、道具和场景状态表达，"
                    "画面中不得出现这句文字。"
                    f"这是动作发生前一瞬的起始帧：{performance_plan.start_state}。"
                    f"摄影机起始位置：{camera_plan.start_position}。{camera_plan.screen_direction}。"
                    "必须为后续人物动作保留空间，"
                    f"不要提前画出动作终点“{performance_plan.end_state}”。所有人物嘴巴自然闭合。"
                    "同一角色在现实空间只允许出现一个实例，禁止分身、重复人物、多人设定稿拼贴；"
                    "镜面场景只允许本人及一个严格对应的倒影，道具特写允许人物不出镜。"
                    f"竖屏国漫构图，视觉风格严格遵循：{bible.visual_style}。"
                    "不要文字、气泡、Logo、水印，不改变人物身份。"
                    "所有人物皮肤完整洁净、衣物完整洁净，画面健康克制；"
                    "涉及危险线索时只用人物反应与非伤害性道具表达。"
                )
                motion_prompt = build_sd_prompt(
                    "narrator",
                    turn.text,
                    shot.motion_prompt,
                    use_reference_audio=True,
                    composition_prompt=composition,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                )
            units.append(
                RuntimeUnit(
                    unit_id=unit_id,
                    episode_id=video_id,
                    scene_id=scene.scene_id,
                    shot_id=shot_id,
                    shot_index=shot.index,
                    turn_index=turn_index,
                    role=turn.role,
                    speaker_name=turn.speaker_name,
                    speaking=turn.speaking,
                    delivery_mode=turn.delivery_mode or TurnDelivery.NARRATION,
                    text=turn.text,
                    emotion=turn.emotion,
                    source_quote=source_quote,
                    character_asset_ids=visible_character_ids,
                    location_asset_id=location_id,
                    voice=voice,
                    visual_prompt=shot.visual_prompt,
                    motion_instruction=shot.motion_prompt,
                    motion_prompt=motion_prompt,
                    keyframe_prompt=keyframe_prompt,
                    actor_description=actor_identity,
                    composition_prompt=composition,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                    audio_path=f"work/turn_audio/{unit_id}.wav",
                    keyframe_path=f"work/keyframes/{unit_id}.jpeg",
                    raw_video_path=f"work/raw_video/{unit_id}.mp4",
                    segment_path=f"work/segments/{unit_id}.mp4",
                )
            )
            unit_ids.append(unit_id)
        shots.append(
            RuntimeShot(
                shot_id=shot_id,
                scene_id=scene.scene_id,
                index=shot.index,
                narrative_job=shot.scene_job,
                location_asset_id=location_id,
                source_quote=shot.source_quote,
                unit_ids=unit_ids,
            )
        )
        scene.shot_ids.append(shot_id)
    return ProductionPlan(
        video_id=video_id,
        source_title=episode.source_title,
        source_text_sha256=sha256_text(episode.source_text),
        style_fingerprint=bible.style_fingerprint,
        visual_style=bible.visual_style,
        palette=bible.palette,
        scenes=scenes,
        shots=shots,
        units=units,
    )


def _camera_movement_budget(plan: EpisodePlan) -> dict[int, tuple[str, str]]:
    """Normalize legacy/model camera plans before compiling media requests.

    The planner may still return a moving legacy CameraPlan without the new
    mode field.  Runtime policy, rather than prompt wording, is authoritative:
    roughly two thirds of shots stay locked, emphasis moves stay sparse, and
    adjacent shots never both receive a moving camera.
    """

    movement_budget = max(1, len(plan.shots) // 3) if len(plan.shots) >= 3 else 0
    emphasis_budget = max(1, len(plan.shots) // 10) if len(plan.shots) >= 10 else 0
    candidates: list[tuple[int, int, str, str]] = []
    for shot in plan.shots:
        mode, motivation, priority = _camera_mode_for(shot, _turns_for_shot(shot)[0])
        if mode != "locked":
            candidates.append((priority, shot.index, mode, motivation))

    selected: dict[int, tuple[str, str]] = {}
    emphasis = 0
    for _, shot_index, proposed_mode, motivation in sorted(
        candidates,
        key=lambda row: (-row[0], row[1]),
    ):
        if len(selected) >= movement_budget:
            break
        if shot_index - 1 in selected or shot_index + 1 in selected:
            continue
        selected_mode = proposed_mode
        if proposed_mode == "motivated_emphasis" and emphasis >= emphasis_budget:
            selected_mode = "motivated_subtle"
            motivation = f"{motivation}；受强调运镜预算约束，降为克制短移"
        selected[shot_index] = (selected_mode, motivation)
        if selected_mode == "motivated_emphasis":
            emphasis += 1

    normalized_modes: dict[int, tuple[str, str]] = {}
    for shot in plan.shots:
        if shot.index in selected:
            normalized_modes[shot.index] = selected[shot.index]
            continue
        proposed_mode, motivation, _ = _camera_mode_for(
            shot, _turns_for_shot(shot)[0]
        )
        if proposed_mode != "locked":
            motivation = "本集运镜预算、优先级或相邻运镜约束要求本镜固定机位"
        normalized_modes[shot.index] = ("locked", motivation)
    return normalized_modes
