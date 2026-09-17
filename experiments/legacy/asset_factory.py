from __future__ import annotations
import json
import shutil
from pathlib import Path
from PIL import Image, ImageDraw
from novel_manga.config import Settings
from novel_manga.models.bible import Character, StoryBible
from novel_manga.models.assets import AssetRecord, SeriesAssetManifest
from novel_manga.models.runtime import RuntimeUnit
from novel_manga.providers.base import ImageResult, MediaProvider
from novel_manga.util import atomic_write_json
from novel_manga.media.common import sha256_text, sha256_file
from novel_manga.media.asset_images import ensure_image
from novel_manga.media.asset_prompts import character_prompt, expression_prompt as make_expression_prompt, location_prompt
from novel_manga.media.asset_specs import character_spec, location_spec




class SeriesAssetFactory:
    def _ensure_image(self, prompt, output, *, reference=None, additional_references=()):
        return ensure_image(self.settings, self.provider, prompt, output, reference=reference, additional_references=additional_references)

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
            prompt = character_prompt(
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
            spec = character_spec(asset_id, character, bible, prompt)
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
            prompt = location_prompt(bible, location)
            prompt += style_reference_guard
            atomic_write_json(
                directory / "spec.json",
                location_spec(asset_id, location, bible, prompt),
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
            expression_prompt = make_expression_prompt(
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
