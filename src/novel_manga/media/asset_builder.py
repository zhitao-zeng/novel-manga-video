"""Selected asset generation using explicit style, shared specs and image caching."""
from __future__ import annotations

from pathlib import Path
from ..models import StoryBible
from ..production_models import AssetRecord, SeriesAssetManifest
from ..util import atomic_write_json
from .common import sha256_text, log
from .asset_style import AssetStyle
from .asset_specs import character_spec, location_spec
from .asset_prompts import character_prompt, expression_prompt as make_expression_prompt, location_prompt
from .asset_images import ensure_image
from .asset_policy import ModerationRejected, moderation_error, SCRUB_WORDS, SAFE_SUFFIX
from .asset_records import merge_manifest

class FramedAssetFactory:
    """Asset factory whose scene-card prompt names the frame instead of 9:16."""

    def __init__(self, settings, provider, *, style: AssetStyle | None = None):
        self.settings, self.provider = settings, provider
        self.style = style or AssetStyle()

    def _location_prompt(self, bible, location):
        prompt = location_prompt(bible, location)
        frame = self.style.frame_text
        return prompt.replace('9:16', frame.split('屏')[-1]).replace('竖屏', frame[:2]) if frame != '竖屏9:16' else prompt

    def ensure_card(self, prompt: str, output: Path, *, reference=None, aspect_ratio=None):
        """_ensure_image, and on a content-moderation refusal one retry with a
        toned-down prompt; a second refusal is final (no point in more rounds)."""
        try:
            return ensure_image(self.settings, self.provider, prompt, output, reference=reference, aspect_ratio=aspect_ratio)
        except RuntimeError as error:
            if not moderation_error(error):
                raise
            safe = SCRUB_WORDS.sub("", prompt) + SAFE_SUFFIX
            log(f"assets: {output.parent.name}/{output.name} refused by content moderation; retrying with a toned-down prompt")
            try:
                return ensure_image(self.settings, self.provider, safe, output, reference=reference, aspect_ratio=aspect_ratio)
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
            prompt = character_prompt(
                bible, character.name, character.appearance, character.base_costume or character.wardrobe,
                visual_archetype=character.visual_archetype, face_anchors=character.face_anchors, silhouette=character.silhouette,
                hair=character.hair, palette=character.palette, motion_signature=character.motion_signature,
            ) + guard
            if "3D" in bible.visual_style or "三维" in bible.visual_style:
                # Modern-dress 3D cards came out near-photoreal and were then
                # redrawn by the review; ask for the animated look up front.
                prompt += self.style.card_style_suffix_3d
            spec = character_spec(asset_id, character, bible, prompt)
            invariants, state, scope = spec['identity_invariants'], spec['state_variables'], spec['reference_scope']
            atomic_write_json(directory / "spec.json", spec)
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master)
            expression_prompt = make_expression_prompt(bible, character.name, character.expression_profile)
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
            prompt = self._location_prompt(bible, location) + guard + self.style.location_empty_suffix
            spec = location_spec(asset_id, location, bible, prompt)
            invariants, state, scope = spec['identity_invariants'], spec['state_variables'], spec['reference_scope']
            atomic_write_json(directory / "spec.json", spec)
            image = self.ensure_card(prompt, directory / "establishing.jpeg", reference=style_master,
                                     aspect_ratio="16:9" if "16:9" in self.style.frame_text else "9:16")
            locations[asset_id] = AssetRecord(
                asset_id=asset_id, kind="location", name=location, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(image.path.relative_to(root.parent)),
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        return merge_manifest(root, bible.style_fingerprint, characters, locations, voices)
