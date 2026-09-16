from __future__ import annotations
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from PIL import Image, ImageDraw
from ..config import Settings
from ..models import (
    CameraBeat,
    CameraPlan,
    Character,
    Episode,
    EpisodePlan,
    MotionActionType,
    MotionBeat,
    PerformancePlan,
    SpeechStrategy,
    ScriptTurn,
    StoryBible,
    TurnDelivery,
    TurnDerivation,
    VisualStrategy,
)
from ..production_models import (
    ActionPhysicsPlan,
    AssetRecord,
    ProductionPlan,
    RuntimeScene,
    RuntimeShot,
    RuntimeUnit,
    SceneSpatialContract,
    SeriesAssetManifest,
)
from ..providers.base import ImageResult, MediaProvider
from ..sd_dialogue import build_sd_prompt
from ..util import atomic_write_json

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
        additional_references: tuple[Path, ...] = (),
    ) -> ImageResult:
        identity = {
            "prompt_sha256": sha256_text(prompt),
            "reference_sha256": sha256_file(reference) if reference and reference.is_file() else None,
            "additional_reference_sha256s": [
                sha256_file(path) for path in additional_references
            ],
            "provider": self.settings.provider,
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
            "image_command_sha256": (
                sha256_text(self.settings.image_command) if self.settings.image_command else None
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
        if additional_references:
            result = self.provider.create_image(
                prompt,
                output,
                reference=reference,
                additional_references=additional_references,
            )
        else:
            # Keep simple provider test doubles and hosted backends compatible
            # when a task genuinely has only one reference.
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
        # Positive art-direction terms must win over exclusions such as
        # "禁止2.5D厚涂". Checking the bare ``2.5D`` token first incorrectly
        # routed an explicitly 2D cartoon bible into the semi-realistic branch.
        if any(token in style for token in ("二维", "卡通", "赛璐璐", "2d")):
            return (
                "二维国风卡通动画人物资产，清晰且有粗细变化的手绘线稿，"
                "明快平涂和两级赛璐璐阴影，概括但稳定的五官、发型和服装形状；"
                "表情动作清楚易读，保持自然人体比例但不做幼儿Q版；"
                "不要真人照片、半写实皮肤、2.5D厚涂、三维游戏CG或PBR塑料高光"
            )
        if any(token in style for token in ("2.5d", "2．5d", "二点五维")):
            return (
                "国风2.5D半写实动态漫人物资产，保留精致手绘轮廓与可控线条，"
                "同时用真实体块、柔和材质、电影体积光和分层景深塑造空间；"
                "东方审美面孔、自然人体比例、细腻发丝、克制皮肤质感，"
                "服装与器物具有稳定结构但不做塑料游戏建模感；"
                "不要纯二维平涂、全写实真人照片、全3D游戏CG、Q版或欧美卡通"
            )
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
    def _character_prompt(
        bible: StoryBible,
        name: str,
        appearance: str,
        wardrobe: str,
        *,
        visual_archetype: str = "",
        face_anchors: list[str] | None = None,
        silhouette: str = "",
        hair: str = "",
        palette: str = "",
        motion_signature: str = "",
    ) -> str:
        identity = "；".join(
            item
            for item in (
                f"戏剧类型：{visual_archetype}" if visual_archetype else "",
                f"五官锚点：{'、'.join(face_anchors or [])}" if face_anchors else "",
                f"轮廓：{silhouette}" if silhouette else "",
                f"发型结构：{hair}" if hair else "",
                f"角色专属配色：{palette}" if palette else "",
                f"惯用姿态：{motion_signature}" if motion_signature else "",
            )
            if item
        )
        prefix = (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"角色资产：{name}；固定外貌：{appearance}；固定服装：{wardrobe}。"
        )
        return prefix + (f"{identity}。" if identity else "") + (
            "只画一个人物且只出现一次，单人四分之三正面、从头到脚的选角定妆照；"
            "脸部占比足够识别，头脚完整，轮廓和服装主色一眼可区分，身体比例自然。"
            "双手自然放松，不拿食物、纸袋、武器或任何剧情道具。"
            "纯色简洁背景，不要多视角设定表、分身、镜像人物、局部小头像或拼贴。"
            f"{SeriesAssetFactory._rendering_direction(bible)}；"
            "不要文字、Logo或水印。"
        )

    @staticmethod
    def _expression_prompt(
        bible: StoryBible,
        name: str,
        expression_profile: str = "",
    ) -> str:
        return (
            f"保持参考图中{ name }的脸型、年龄、发型、服装和{bible.style_fingerprint}风格完全一致。"
            "只画这个人物且只出现一次，生成四分之三正面单人胸像身份与表情锚点；"
            f"角色表情幅度：{expression_profile or '克制自然、以眼神和眉形为主'}。"
            "选择该角色最有辨识度、但尚未到剧情高潮的基础表情，"
            "眼睛、眉形和嘴部清晰无遮挡，肩颈与服装领口完整。"
            "不要表情九宫格、多头像、分身、拼贴；双手不持任何物品，简单背景，"
            f"{SeriesAssetFactory._rendering_direction(bible)}；不要文字、Logo或水印。"
        )

    @staticmethod
    def _location_prompt(bible: StoryBible, location: str) -> str:
        return (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"场景资产：{location}。固定建筑结构、空间布局、关键物品、天气、时间和光线方向。"
            "严格空场，竖屏建立镜头与对话主角度可复用背景板；前景、中景、背景层次明确，"
            "预留一至两名人物站立、走动和视线交流的表演空间，避免把核心道具放在字幕安全区。"
            "不得出现人物、人体剪影、海报人物、照片人物或镜中人。"
            f"{SeriesAssetFactory._rendering_direction(bible)}；不要文字、Logo或水印。"
        )

    def build(self, root: Path, bible: StoryBible) -> SeriesAssetManifest:
        root.mkdir(parents=True, exist_ok=True)
        characters: list[AssetRecord] = []
        locations: list[AssetRecord] = []
        voice_assignments = {"narrator": "native:narrator"}
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
        style_master = self.settings.style_master_path
        style_reference_guard = (
            "【系列母版继承】参考图只锁定线稿粗细、二维平涂、赛璐璐阴影、色彩亮度、"
            "光影方向和整体动画制作规格；不得照抄参考图人物身份、脸型、发型、服装、姿势、"
            "场景结构或具体构图，必须严格按当前资产描述重新设计。"
            if style_master is not None
            else ""
        )
        for index, character in enumerate(source_characters, start=1):
            asset_id = f"character_{index:03d}"
            directory = root / "characters" / asset_id
            prompt = self._character_prompt(
                bible,
                character.name,
                character.appearance,
                character.base_costume or character.wardrobe,
                visual_archetype=character.visual_archetype,
                face_anchors=character.face_anchors,
                silhouette=character.silhouette,
                hair=character.hair,
                palette=character.palette,
                motion_signature=character.motion_signature,
            ) + style_reference_guard
            spec = {
                "asset_id": asset_id,
                "name": character.name,
                "role": character.role,
                "gender": character.gender,
                "age": character.age,
                "appearance": character.appearance,
                "wardrobe": character.wardrobe,
                "visual_archetype": character.visual_archetype,
                "face_anchors": character.face_anchors,
                "silhouette": character.silhouette,
                "hair": character.hair,
                "palette": character.palette,
                "base_costume": character.base_costume,
                "episode_costumes": character.episode_costumes,
                "signature_prop": character.signature_prop,
                "expression_profile": character.expression_profile,
                "motion_signature": character.motion_signature,
                "voice_profile_id": character.voice_profile_id,
                "version": "v001",
                "identity_invariants": [
                    value
                    for value in (
                        character.appearance,
                        *character.face_anchors,
                        character.silhouette,
                        character.hair,
                    )
                    if value
                ],
                "state_variables": {
                    "costume": character.base_costume or character.wardrobe,
                    "injury": "none unless changed by source events",
                    "carried_prop": character.signature_prop or "none",
                },
                "reference_scope": {
                    "inherit": ["identity", "hair", "costume", "2d_rendering"],
                    "exclude": ["pose", "composition", "camera", "background", "lighting"],
                },
                "style_fingerprint": bible.style_fingerprint,
                "prompt": prompt,
            }
            atomic_write_json(directory / "spec.json", spec)
            primary = self._ensure_image(
                prompt,
                directory / "turnaround.jpeg",
                reference=style_master,
            )
            character_rows.append((character, asset_id, directory, prompt, primary))
            voice_assignments[character.name] = (
                character.voice_profile_id or f"native:{asset_id}"
            )
        for index, location in enumerate(dict.fromkeys(bible.locations or ["原文主要场景"]), start=1):
            asset_id = f"location_{index:03d}"
            directory = root / "locations" / asset_id
            prompt = self._location_prompt(bible, location)
            prompt += style_reference_guard
            atomic_write_json(
                directory / "spec.json",
                {
                    "asset_id": asset_id,
                    "name": location,
                    "style_fingerprint": bible.style_fingerprint,
                    "continuity": "固定空间布局、物品锚点、天气、时间、光线方向",
                    "version": "v001",
                    "identity_invariants": [f"{location}固定建筑、出入口和空间层级"],
                    "state_variables": {
                        "time_of_day": "approved_reference_state",
                        "weather": "approved_reference_state",
                        "damage": "none unless changed by source events",
                    },
                    "reference_scope": {
                        "inherit": ["architecture", "space", "color", "lighting", "2d_rendering"],
                        "exclude": ["composition", "camera", "temporary_people", "text"],
                    },
                    "prompt": prompt,
                },
            )
            image = self._ensure_image(
                prompt,
                directory / "establishing.jpeg",
                reference=style_master,
            )
            location_rows.append(
                (asset_id, location, directory, prompt, image)
            )

        for character, asset_id, directory, prompt, primary in character_rows:
            expression_prompt = self._expression_prompt(
                bible, character.name, character.expression_profile
            )
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
                    identity_invariants=[
                        value
                        for value in (
                            character.appearance,
                            *character.face_anchors,
                            character.silhouette,
                            character.hair,
                        )
                        if value
                    ],
                    state_variables={
                        "costume": character.base_costume or character.wardrobe,
                        "injury": "none unless changed by source events",
                        "carried_prop": character.signature_prop or "none",
                    },
                    reference_scope={
                        "inherit": ["identity", "hair", "costume", "2d_rendering"],
                        "exclude": ["pose", "composition", "camera", "background", "lighting"],
                    },
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
                    identity_invariants=[f"{location}固定建筑、出入口和空间层级"],
                    state_variables={
                        "time_of_day": "approved_reference_state",
                        "weather": "approved_reference_state",
                        "damage": "none unless changed by source events",
                    },
                    reference_scope={
                        "inherit": ["architecture", "space", "color", "lighting", "2d_rendering"],
                        "exclude": ["composition", "camera", "temporary_people", "text"],
                    },
                    spec_path=str((directory / "spec.json").relative_to(root.parent)),
                    primary_image=str(image.path.relative_to(root.parent)),
                    prompt_sha256=sha256_text(prompt),
                )
            )
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
