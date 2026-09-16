from __future__ import annotations
import fcntl
import json
import re
import shutil
import threading
import time
from pathlib import Path
from PIL import Image
from ..config import Settings
from ..models import StoryBible
from ..production_models import AssetRecord, SeriesAssetManifest
from ..util import atomic_write_json
from .asset_factory import SeriesAssetFactory
from .common import sha256_text, log, reference_digests

REDRAW_ORIGIN = "privacy-stylized-redraw"

MODERATION_MARKERS = ("violate", "usage policy", "content policy", "sensitive", "moderation", "safety", "违规", "敏感", "审核")

SCRUB_WORDS = re.compile(r"妩媚|性感|曼妙|露肩|低胸|大腿|俗气|轻浮|挑逗|妖艳|夸张")

SAFE_SUFFIX = "。整体端庄得体，衣着完整，表情自然温和，普通站姿，无任何性暗示、暴力或血腥"

REDRAW_WAIT_SECONDS = 180

REPAIR_LOCK = threading.Lock()

MANIFEST_LOCK = threading.Lock()

PRIVACY_OK_FILE = "series_assets/.privacy_ok.json"

CARD_STYLE_SUFFIX_3D = (
    "。整体必须是一眼可辨的风格化三维动画角色（国漫/皮克斯式概括造型）：眼睛略大、五官简化、皮肤光滑无毛孔、"
    "干净的三维建模材质与柔和体积光；绝不是真人照片、真实人物肖像或写实渲染"
)

LOCATION_EMPTY_SUFFIX = "。画面中绝对不出现任何人物、人影、人形剪影或车内乘客，只有空无一人的场景"

STYLIZE_PROMPT = (
    "以参考图为唯一身份依据，把这张角色卡重绘成一眼可辨的中国3D国漫动画角色，不是真人：保持同一人的脸型、年龄段、发型、"
    "胡须、服装款式与配色、站姿和构图完全不变；眼睛略大、五官简化概括、皮肤光滑无毛孔无老年斑、皱纹用动画化的少量线条表现，"
    "布料和头发是干净的三维建模材质，柔和体积光；纯色简洁背景；禁止真人照片质感、真实人物肖像、写实皮肤纹理、文字、Logo或水印。"
)

class ModerationRejected(RuntimeError):
    """The image service refused a card even after the prompt was toned down."""

def moderation_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in MODERATION_MARKERS)

class FramedAssetFactory(SeriesAssetFactory):
    """Asset factory whose scene-card prompt names the frame instead of 9:16."""

    frame_text = "竖屏9:16"

    def _location_prompt(self, bible, location):  # type: ignore[override]
        prompt = SeriesAssetFactory._location_prompt(bible, location)
        return prompt.replace("9:16", self.frame_text.split("屏")[-1]).replace("竖屏", self.frame_text[:2]) if self.frame_text != "竖屏9:16" else prompt

    def ensure_card(self, prompt: str, output: Path, *, reference=None):
        """_ensure_image, and on a content-moderation refusal one retry with a
        toned-down prompt; a second refusal is final (no point in more rounds)."""
        try:
            return self._ensure_image(prompt, output, reference=reference)
        except RuntimeError as error:
            if not moderation_error(error):
                raise
            safe = SCRUB_WORDS.sub("", prompt) + SAFE_SUFFIX
            log(f"assets: {output.parent.name}/{output.name} refused by content moderation; retrying with a toned-down prompt")
            try:
                return self._ensure_image(safe, output, reference=reference)
            except RuntimeError as again:
                if moderation_error(again):
                    raise ModerationRejected(f"{output.parent.name}/{output.name}: {str(again)[:200]}") from again
                raise

    def build_selected(self, root: Path, bible: StoryBible, character_ids: set[str], location_ids: set[str], expressions: bool = True) -> SeriesAssetManifest:
        """Build (or reuse) only the listed assets; ids stay the bible positions.

        The base ``build`` renders every character and location in the bible.
        A long novel's bible grows to hundreds of entries, so an episode only
        pays for the cards it references; records are merged into the manifest.
        """
        root.mkdir(parents=True, exist_ok=True)
        style_master = self.settings.style_master_path
        guard = (
            "【系列母版继承】参考图只锁定线稿粗细、二维平涂、赛璐璐阴影、色彩亮度、"
            "光影方向和整体动画制作规格；不得照抄参考图人物身份、脸型、发型、服装、姿势、"
            "场景结构或具体构图，必须严格按当前资产描述重新设计。"
            if style_master is not None else ""
        )
        manifest_path = root / "manifest.json"
        characters: dict[str, dict] = {}  # the records this call builds, merged into the manifest at the end
        locations: dict[str, dict] = {}
        voices: dict[str, str] = {}
        for index, character in enumerate(bible.characters, start=1):
            asset_id = f"character_{index:03d}"
            if asset_id not in character_ids:
                continue
            directory = root / "characters" / asset_id
            prompt = self._character_prompt(
                bible, character.name, character.appearance, character.base_costume or character.wardrobe,
                visual_archetype=character.visual_archetype, face_anchors=character.face_anchors, silhouette=character.silhouette,
                hair=character.hair, palette=character.palette, motion_signature=character.motion_signature,
            ) + guard
            if "3D" in bible.visual_style or "三维" in bible.visual_style:
                # Modern-dress 3D cards came out near-photoreal and were then
                # redrawn by the review; ask for the animated look up front.
                prompt += CARD_STYLE_SUFFIX_3D
            invariants = [value for value in (character.appearance, *character.face_anchors, character.silhouette, character.hair) if value]
            state = {"costume": character.base_costume or character.wardrobe, "injury": "none unless changed by source events", "carried_prop": character.signature_prop or "none"}
            scope = {"inherit": ["identity", "hair", "costume", "2d_rendering"], "exclude": ["pose", "composition", "camera", "background", "lighting"]}
            atomic_write_json(directory / "spec.json", {
                "asset_id": asset_id, "name": character.name, "role": character.role, "gender": character.gender, "age": character.age,
                "appearance": character.appearance, "wardrobe": character.wardrobe, "visual_archetype": character.visual_archetype,
                "face_anchors": character.face_anchors, "silhouette": character.silhouette, "hair": character.hair, "palette": character.palette,
                "base_costume": character.base_costume, "episode_costumes": character.episode_costumes, "signature_prop": character.signature_prop,
                "expression_profile": character.expression_profile, "motion_signature": character.motion_signature, "voice_profile_id": character.voice_profile_id,
                "version": "v001", "identity_invariants": invariants, "state_variables": state, "reference_scope": scope,
                "style_fingerprint": bible.style_fingerprint, "prompt": prompt,
            })
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master)
            expression_prompt = self._expression_prompt(bible, character.name, character.expression_profile)
            # Fast production uses one main character card, including when an
            # old expression sheet happens to remain on disk.
            secondary = self.ensure_card(expression_prompt, directory / "expressions.jpeg", reference=primary.path) if expressions else None
            characters[asset_id] = AssetRecord(
                asset_id=asset_id, kind="character", name=character.name, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(primary.path.relative_to(root.parent)),
                secondary_image=str(secondary.path.relative_to(root.parent)) if secondary else None, prompt_sha256=sha256_text(prompt + expression_prompt),
            ).model_dump(mode="json")
            voices[character.name] = character.voice_profile_id or f"native:{asset_id}"
        for index, location in enumerate(dict.fromkeys(bible.locations), start=1):
            asset_id = f"location_{index:03d}"
            if asset_id not in location_ids:
                continue
            directory = root / "locations" / asset_id
            prompt = self._location_prompt(bible, location) + guard + LOCATION_EMPTY_SUFFIX
            invariants = [f"{location}固定建筑、出入口和空间层级"]
            state = {"time_of_day": "approved_reference_state", "weather": "approved_reference_state", "damage": "none unless changed by source events"}
            scope = {"inherit": ["architecture", "space", "color", "lighting", "2d_rendering"], "exclude": ["composition", "camera", "temporary_people", "text"]}
            atomic_write_json(directory / "spec.json", {
                "asset_id": asset_id, "name": location, "style_fingerprint": bible.style_fingerprint,
                "continuity": "固定空间布局、物品锚点、天气、时间、光线方向", "version": "v001",
                "identity_invariants": invariants, "state_variables": state, "reference_scope": scope, "prompt": prompt,
            })
            image = self.ensure_card(prompt, directory / "establishing.jpeg", reference=style_master)
            locations[asset_id] = AssetRecord(
                asset_id=asset_id, kind="location", name=location, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(image.path.relative_to(root.parent)),
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        # Card builds run in parallel, one process per asset.  Each used to write back the whole manifest it
        # had read at the start, so the last to finish dropped the records the others had added (two cards
        # on disk, one in the manifest).  Merge into what is on disk now, under a lock.
        with MANIFEST_LOCK, open(root / ".manifest.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            characters = {**{row["asset_id"]: row for row in existing.get("characters", [])}, **characters}
            locations = {**{row["asset_id"]: row for row in existing.get("locations", [])}, **locations}
            voices = {**(existing.get("voice_assignments") or {"narrator": "native:narrator"}), **voices}
            manifest = SeriesAssetManifest(
                style_fingerprint=bible.style_fingerprint,
                characters=[AssetRecord(**characters[key]) for key in sorted(characters)],
                locations=[AssetRecord(**locations[key]) for key in sorted(locations)],
                voice_assignments=voices,
            )
            atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest

def asset_index(asset_id: str) -> int:
    return int(asset_id.rsplit("_", 1)[1])

def load_privacy_ok(novel_dir: Path) -> set[str]:
    try:
        return set(json.loads((novel_dir / PRIVACY_OK_FILE).read_text(encoding="utf-8")).get("paths", []))
    except (OSError, ValueError):
        return set()

def record_privacy_ok(novel_dir: Path, paths) -> None:
    """Remember cards that Seedance accepted, so a later run (or a parallel one)
    never redraws a proven card just because it was the first thing rejected.
    The read-modify-write is guarded by a file lock: episodes render in
    parallel processes and finish clips at the same moment."""
    target = novel_dir / PRIVACY_OK_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with REPAIR_LOCK, open(target.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = load_privacy_ok(novel_dir) | {str(p) for p in paths}
        atomic_write_json(target, {"paths": sorted(merged)})

def cards_sheet(novel_dir: Path, output: Path, height: int = 300, *, asset_ids: set[str] | None = None) -> Path | None:
    """Preview the selected cards; a chapter build must not stack the whole book
    into a JPEG taller than the format supports."""
    rows: list[list[Path]] = []
    for card_dir in sorted((novel_dir / "series_assets" / "characters").glob("character_*")):
        if asset_ids is not None and card_dir.name not in asset_ids:
            continue
        views = [card_dir / name for name in ("turnaround.jpeg", "expressions.jpeg") if (card_dir / name).is_file()]
        if views:
            rows.append(views)
    locations = sorted((novel_dir / "series_assets" / "locations").glob("location_*/establishing.jpeg"))
    if asset_ids is not None:
        locations = [path for path in locations if path.parent.name in asset_ids]
    for index in range(0, len(locations), 4):
        rows.append(locations[index:index + 4])
    if not rows:
        return None
    thumbs: list[list[Image.Image]] = []
    for row in rows:
        thumbs.append([])
        for path in row:
            with Image.open(path) as image:
                image = image.convert("RGB")
                thumbs[-1].append(image.resize((max(1, round(image.width * height / image.height)), height)))
    width = max(sum(t.width for t in row) + 8 * (len(row) + 1) for row in thumbs)
    sheet = Image.new("RGB", (width, len(thumbs) * (height + 8) + 8), (24, 24, 24))
    y = 8
    for row in thumbs:
        x = 8
        for thumb in row:
            sheet.paste(thumb, (x, y))
            x += thumb.width + 8
        y += height + 8
    sheet.save(output, quality=85)
    return output

def stylize_card(provider, path: Path) -> Path:
    """Park a near-photoreal card (with its sidecars) and redraw it as clearly animated 3D."""
    backup = path.with_suffix(".photoreal-rejected.jpeg")
    for suffix in ("", ".task.json", ".request.json"):
        source = path.with_suffix(path.suffix + suffix)
        if source.exists():
            target = backup.with_suffix(backup.suffix + suffix)
            target.unlink(missing_ok=True)
            source.rename(target)
    provider.create_image(STYLIZE_PROMPT, path, reference=backup)
    with Image.open(path) as image:
        image.load()
    atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {
        "origin": REDRAW_ORIGIN, "source": backup.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT),
        "request_sha256": sha256_text(STYLIZE_PROMPT + backup.name), "reason": "near-photoreal card",
    })
    return path

def wait_for_inflight_redraws(paths, timeout: float = REDRAW_WAIT_SECONDS) -> list[Path]:
    """A card missing while its .photoreal-rejected.jpeg backup exists is being
    redrawn by another thread or process; wait for it instead of failing (or,
    in the asset factory's case, regenerating a photoreal card in its place)."""
    waited: list[Path] = []
    deadline = time.monotonic() + timeout
    for path in paths:
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        if (path.parent / f".regenerated.{path.name}").exists():
            continue  # deliberately deleted by the card review; the factory will rebuild it
        while not path.is_file() and backup.exists():
            if time.monotonic() > deadline:
                # The redraw is late or keeps failing (an nsfw refusal, say).
                # A photoreal card beats no card and a failed episode: put the
                # backup in place and mark the fix as tried so nobody retries it;
                # a redraw that still lands later simply replaces the file.
                shutil.copy2(backup, path)
                (path.parent / f".regenerated.{path.stem}.txt").touch()
                log(f"redraw of {path.parent.name}/{path.name} did not finish within {timeout:.0f}s; using the photoreal backup")
                break
            if path not in waited:
                waited.append(path)
                log(f"waiting for in-flight redraw of {path.parent.name}/{path.name}")
            time.sleep(5)
    return waited

def apply_genre(genre):
    global CARD_STYLE_SUFFIX_3D, LOCATION_EMPTY_SUFFIX
    if genre.get('card_style_suffix_3d'):
        CARD_STYLE_SUFFIX_3D = genre['card_style_suffix_3d']
    if genre.get('location_policy') == 'sparse':
        LOCATION_EMPTY_SUFFIX = '。主体空无一人：近景和中景不出现任何人物或人形剪影，远处允许少量模糊的背景行人'
