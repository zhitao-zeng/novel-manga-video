from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from .config import Settings
from .models import Character, Episode, EpisodePlan, ScriptTurn, StoryBible
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
            _archive_stale(output, str(saved.get("request_sha256", "unknown")))
            _archive_stale(meta, str(saved.get("request_sha256", "unknown")))
            output.with_suffix(output.suffix + ".task.json").unlink(missing_ok=True)
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
    def _character_prompt(bible: StoryBible, name: str, appearance: str, wardrobe: str) -> str:
        return (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"角色资产：{name}；固定外貌：{appearance}；固定服装：{wardrobe}。"
            "纯色简洁背景，正面、四分之三、侧面、背面与全身/胸像比例设计，身份完全一致。"
            "二维国漫角色设定稿，不要场景、文字、Logo、水印、真人、3D或其他画风。"
        )

    @staticmethod
    def _expression_prompt(bible: StoryBible, name: str) -> str:
        return (
            f"保持参考图中{ name }的脸型、年龄、发型、服装和{bible.style_fingerprint}风格完全一致。"
            "生成中近景表情资产：中性、喜悦、愤怒、害怕、悲伤、惊讶、坚定。"
            "嘴部清晰无遮挡，简单背景，不要文字、Logo、水印、真人或3D。"
        )

    @staticmethod
    def _location_prompt(bible: StoryBible, location: str) -> str:
        return (
            f"{bible.visual_style}。系列风格指纹 {bible.style_fingerprint}。{bible.palette}。"
            f"场景资产：{location}。固定建筑结构、空间布局、关键物品、天气、时间和光线方向。"
            "竖屏建立镜头与对话主角度可复用背景板，前景不出现人物，不要文字、Logo、水印、真人或3D。"
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


def _dialogue_composition(shot_index: int, turn_index: int) -> str:
    return _DIALOGUE_COMPOSITIONS[(shot_index + turn_index - 2) % len(_DIALOGUE_COMPOSITIONS)]


def _character_identity(character: Character) -> str:
    appearance = character.appearance.removeprefix("固定")
    wardrobe = character.wardrobe.removeprefix("固定")
    return (
        f"{character.name}，{character.gender}，{character.age}，"
        f"外貌锚点：{appearance}；服装锚点：{wardrobe}"
    )


def compile_production_plan(
    video_id: str,
    episode: Episode,
    plan: EpisodePlan,
    bible: StoryBible,
    assets: SeriesAssetManifest,
) -> ProductionPlan:
    character_ids = {record.name: record.asset_id for record in assets.characters}
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
            if turn.speaking and "".join(turn.text.split()) not in "".join(source_quote.split()):
                raise ValueError(
                    f"{unit_id} visible dialogue is not an exact substring of its grounded source quote"
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
                turn.speaker_name if turn.speaking else "narrator",
                assets.voice_assignments["narrator"],
            )
            if turn.speaking:
                speaker = character_specs[turn.speaker_name]
                actor_identity = _character_identity(speaker)
                composition = _dialogue_composition(shot.index, turn_index)
                keyframe_prompt = (
                    f"系列风格指纹 {bible.style_fingerprint}。二维国漫竖屏漫剧。"
                    f"严格保持参考图中的唯一角色身份：{actor_identity}。"
                    f"场景明确为{shot.location or assets.locations[0].name}，背景适度虚化。"
                    f"独立构图要求：{composition}。只画{turn.speaker_name}单人，情绪为{turn.emotion}，"
                    "脸和完整嘴部位于竖屏安全区，嘴巴自然闭合且无遮挡。"
                    "不得出现被对话者、其他前景人物、文字、字幕、气泡、Logo、水印、真人或3D。"
                )
                motion_prompt = build_sd_prompt(
                    turn.speaker_name,
                    turn.text,
                    shot.motion_prompt,
                    use_reference_audio=True,
                    actor_description=actor_identity,
                    composition_prompt=composition,
                    emotion=turn.emotion,
                )
            else:
                keyframe_prompt = (
                    f"系列风格指纹 {bible.style_fingerprint}。保持参考板的场景、角色、服装、色彩和光线。"
                    f"分镜视觉约束：{shot.visual_prompt}。"
                    "表现一个明确动作或情绪信息，所有人物嘴巴自然闭合。"
                    "同一角色在现实空间只允许出现一个实例，禁止分身、重复人物、多人设定稿拼贴；"
                    "镜面场景只允许本人及一个严格对应的倒影，道具特写允许人物不出镜。"
                    "竖屏国漫构图，不要文字、气泡、Logo、水印，不改变人物身份。"
                    "所有人物皮肤完整洁净、衣物完整洁净，画面健康克制；"
                    "涉及危险线索时只用人物反应与非伤害性道具表达。"
                )
                motion_prompt = (
                    "严格使用参考音频作为唯一画外旁白，声音内容与锁定字幕一致。"
                    "画中人物不得开口或随旁白做口型。"
                    f"{shot.motion_prompt}。动作自然克制，不切镜、不循环、不倒放。"
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
                    text=turn.text,
                    emotion=turn.emotion,
                    source_quote=source_quote,
                    character_asset_ids=visible_character_ids,
                    location_asset_id=location_id,
                    voice=voice,
                    visual_prompt=shot.visual_prompt,
                    motion_prompt=motion_prompt,
                    keyframe_prompt=keyframe_prompt,
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
        scenes=scenes,
        shots=shots,
        units=units,
    )
