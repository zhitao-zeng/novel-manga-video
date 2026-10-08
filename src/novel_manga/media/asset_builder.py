"""Selected asset generation using explicit style, shared specs and image caching."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from novel_manga.models.bible import StoryBible
from novel_manga.models.assets import AssetRecord, SeriesAssetManifest
from ..util import atomic_write_json
from .common import sha256_file, sha256_text, log
from .asset_style import AssetStyle, card_suffix as _card_suffix
from .asset_specs import character_spec, location_spec, prop_spec
from .asset_prompts import character_prompt, expression_prompt as make_expression_prompt, location_prompt, prop_prompt
from .asset_images import ensure_image, _archive_stale
from .asset_policy import (ModerationRejected, moderation_error, refusal_text, seedream_prompt,
                           SCRUB_WORDS, SAFE_SUFFIX, SEEDREAM_BACKGROUND)
from .asset_records import merge_manifest, replace_card_verdict
from .card_check import people_in_card, record_people_check

def load_location_time(novel_dir) -> dict:
    """The book's curated time and main light per location, or nothing if it was never filled."""
    import json
    from pathlib import Path
    path = Path(novel_dir) / "visual_grammar.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("location_time") or {}
    except ValueError:
        return {}
    return {name: text for name, text in rows.items() if str(text).strip()}


class FramedAssetFactory:
    """Asset factory whose scene-card prompt names the frame instead of 9:16."""

    def __init__(self, settings, provider, *, style: AssetStyle | None = None, location_time: dict | None = None):
        self.settings, self.provider = settings, provider
        self.style = style or AssetStyle()
        # visual_grammar.json's location_time, which review/verify.py checks a rendered clip against.
        # Without it here the card is drawn at whatever hour the model picks, and the review then
        # faults the clip for an hour nobody ever asked the card for.
        self.location_time = dict(location_time or {})

    def _location_prompt(self, bible, location):
        prompt = location_prompt(bible, location, family=self.style.render_family,
                                 direction=self.style.scene_direction or self.style.render_direction,
                                 fingerprint=self.style.prompt_fingerprint,
                                 scene_style=self.style.scene_style, tidy=self.style.tidy_prompts)
        when = self.location_time.get(str(location).split('：', 1)[0].strip(), '')
        if when:
            prompt += f'时段与主光源：{when}。'
        frame = self.style.frame_text
        return prompt.replace('9:16', frame.split('屏')[-1]).replace('竖屏', frame[:2]) if frame != '竖屏9:16' else prompt

    def ensure_card(self, prompt: str, output: Path, *, reference=None, additional_references=(), aspect_ratio=None, seed=None,
                    negative_prompt: str = "", route_local: bool | None = None):
        """_ensure_image, and on a content-moderation refusal one retry with a
        toned-down prompt, then one attempt at the fallback model.

        route_local: True/False 由方言的 local_kinds 决定(道具本地、人物 API);
        None 是旧调用方的"开了本地就全本地"语义。"""
        try:
            return ensure_image(self.settings, self.provider, prompt, output, reference=reference,
                                additional_references=tuple(additional_references), aspect_ratio=aspect_ratio, seed=seed,
                                negative_prompt=negative_prompt, route_local=route_local)
        except RuntimeError as error:
            if not moderation_error(error):
                raise
            safe = SCRUB_WORDS.sub("", prompt) + SAFE_SUFFIX
            log(f"assets: {output.parent.name}/{output.name} refused by content moderation; retrying with a toned-down prompt")
            try:
                return ensure_image(self.settings, self.provider, safe, output, reference=reference,
                                    additional_references=tuple(additional_references), aspect_ratio=aspect_ratio, seed=seed,
                                    negative_prompt=negative_prompt, route_local=route_local)
            except RuntimeError as again:
                if moderation_error(again):
                    return self._fallback_card(prompt, output, aspect_ratio, again, reference=reference)
                raise

    # Seeds for a location card drawn again because the judge found people in it.
    EMPTY_RETRY_SEEDS = (1009, 2027)

    def _keep_empty(self, prompt: str, output: Path, image, **card):
        """A location card is a room nobody stands in: H3 casts whoever it finds in a set.  A card the
        local model drew in this run is counted by the judge; with people in it, it is set aside and
        drawn again at another seed, twice at most.  The count stays beside the card, where the
        pre-render check reads it, so a card still holding people stops the clips that use it."""
        for attempt, seed in enumerate((None, *self.EMPTY_RETRY_SEEDS)):
            if seed is not None:
                rejected = output.with_name(f"{output.stem}.people-rejected-{attempt}{output.suffix}")
                meta = output.with_suffix(output.suffix + ".request.json")
                output.replace(rejected)
                if meta.is_file():
                    meta.replace(rejected.with_suffix(rejected.suffix + ".request.json"))
                image = self.ensure_card(prompt, output, seed=seed, **card)
            try:
                verdict = people_in_card(output)
            except Exception as error:  # a check, not a dependency: say it was not made and go on
                log(f"assets: {output.parent.name}/{output.name} not checked for people: {error}")
                record_people_check(output, {"error": str(error)[:300]})
                return image
            record_people_check(output, verdict)
            if not verdict.get("people_in_scene"):
                return image
            log(f"assets: {output.parent.name}/{output.name} has {verdict['people_in_scene']} people in it: "
                f"{verdict.get('where', '')}")
        return image

    def _fallback_card(self, prompt: str, output: Path, aspect_ratio, refusal: Exception,
                       *, reference: Path | None = None):
        """A second refusal on the same card means the prompt is not the problem.

        Five of this book's characters are trademarked - 尼克·弗瑞, 托尔, 毒液 and two more -
        and gpt-image-2 will not draw them however the wording is softened, because what it
        objects to is who is in the picture.  Sixteen chapters were failing on those five,
        each one paying for both attempts on the way down.  Seedream draws them from the
        same prompt, so the card gets one attempt there before the chapter is given up.

        Full-body cards keep the measured no-reference framing strategy. Expression
        cards already request a bust: retain their primary identity reference and
        original crop instead of turning them into a new full-body character.
        """
        model = self.settings.card_fallback_image_model
        if not model or model == self.settings.image_model:
            raise ModerationRejected(f"{output.parent.name}/{output.name}: {refusal_text(refusal)}") from refusal
        from ..providers.phanrouter import PhanRouterMediaProvider
        settings = replace(self.settings, image_model=model)
        log(f"assets: {output.parent.name}/{output.name} refused twice; falling back to {model}")
        try:
            expression = output.name == "expressions.jpeg"
            return ensure_image(settings, PhanRouterMediaProvider(settings),
                                prompt + SEEDREAM_BACKGROUND if expression else seedream_prompt(prompt), output,
                                reference=reference if expression else None, aspect_ratio=aspect_ratio)
        except RuntimeError as fallen:
            raise ModerationRejected(
                f"{output.parent.name}/{output.name}: {refusal_text(refusal)}; "
                f"{model} also failed: {refusal_text(fallen)}"
            ) from fallen

    def _redraw_selected(self, root, bible, character_ids, location_ids, expressions, prop_ids,
                         review_candidate):
        """Generate each selected card separately, then publish its complete views."""
        requested = character_ids | location_ids | (prop_ids or set())
        if not requested:
            raise ValueError("redraw requires an explicit nonempty asset scope")
        available = ({f"character_{i:03d}" for i in range(1, len(bible.characters) + 1)}
                     | {f"location_{i:03d}" for i in range(1, len(dict.fromkeys(bible.locations)) + 1)}
                     | {f"prop_{i:03d}" for i in range(1, len(bible.props or []) + 1)})
        if requested - available:
            raise ValueError("unknown selected assets: " + ", ".join(sorted(requested - available)))
        worker = FramedAssetFactory(replace(self.settings, reuse_existing_assets=False), self.provider,
                                    style=self.style, location_time=self.location_time)
        manifest = None
        for asset_id in sorted(requested):
            kind = asset_id.split("_", 1)[0]
            section = {"character": "characters", "location": "locations", "prop": "props"}[kind]
            staging_root = root / ".redraw" / asset_id / root.name
            generated = worker.build_selected(staging_root, bible,
                {asset_id} if kind == "character" else set(),
                {asset_id} if kind == "location" else set(), expressions=expressions,
                prop_ids={asset_id} if kind == "prop" else set())
            source = staging_root / section / asset_id
            views = {"character": ("turnaround.jpeg", "expressions.jpeg"),
                     "location": ("establishing.jpeg",),
                     "prop": ("turnaround.jpeg", "detail.jpeg")}[kind]
            rows = {record.asset_id: record.model_dump(mode="json")
                    for record in getattr(generated, section) if record.asset_id == asset_id}
            current_views = {Path(row[field]).name for row in rows.values()
                             for field in ("primary_image", "secondary_image") if row.get(field)}
            # A prior rejected quality candidate may leave an expression sheet
            # behind when the operator resumes with main-card-only production.
            for view in set(views) - current_views:
                meta = source / (view + ".request.json")
                previous = (json.loads(meta.read_text()).get("request_sha256", "unknown")
                            if meta.is_file() else "unknown")
                for suffix in ("", ".request.json", ".task.json"):
                    _archive_stale(source / (view + suffix), str(previous))
            review = review_candidate(staging_root.parent, asset_id) if review_candidate is not None else None
            if review is not None and (review.get("flags") or review.get("missing_cards")):
                raise ValueError("candidate card review failed: " + "; ".join(
                    review.get("flags") or review.get("missing_cards")))
            directory = root / section / asset_id
            old_meta = directory / (views[0] + ".request.json")
            old_request = (json.loads(old_meta.read_text()).get("request_sha256", "unknown")
                           if old_meta.is_file() else "unknown")
            files = ["spec.json", *(view + suffix for view in views
                                    for suffix in ("", ".request.json", ".task.json"))]
            directory.mkdir(parents=True, exist_ok=True)
            for name in files:
                _archive_stale(directory / name, str(old_request))
                if (source / name).is_file():
                    (source / name).replace(directory / name)
            replace_card_verdict(root, asset_id, str(old_request), review)
            voices = {row["name"]: generated.voice_assignments[row["name"]] for row in rows.values()
                      if kind == "character"}
            manifest = merge_manifest(root, bible.style_fingerprint,
                rows if kind == "character" else {}, rows if kind == "location" else {}, voices,
                rows if kind == "prop" else {})
        return manifest

    def build_selected(self, root: Path, bible: StoryBible, character_ids: set[str], location_ids: set[str],
                       expressions: bool = True, prop_ids: set[str] | None = None,
                       redraw: bool = False, review_candidate=None) -> SeriesAssetManifest:
        """Build (or reuse) only the listed assets; ids stay the bible positions.

        The base ``build`` renders every character and location in the bible.
        A long novel's bible grows to hundreds of entries, so an episode only
        pays for the cards it references; records are merged into the manifest.
        A bible without props builds none, and the prop_ids default keeps every
        older caller working.
        """
        if redraw:
            return self._redraw_selected(root, bible, character_ids, location_ids, expressions, prop_ids,
                                         review_candidate)
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
        # 按类型路由(方言写了 local_kinds 时):人物/场景显式走 API,道具才落本地;
        # 方言没写 local_kinds 时维持旧语义——开了本地服务就全部走本地(None)。
        # 不带 route_local 调用,保持旧测试替身和旁路调用方的签名兼容。
        remote_route = False if self.style.dialect_routes_subset else None
        route_kw = {} if remote_route is None else {"route_local": remote_route}
        location_route = ({} if not self.style.dialect_routes_subset
                          else {"route_local": 'location' in self.style.local_kinds})
        if location_route.get('route_local') and self.style.location_negative_prompt:
            location_route['negative_prompt']=self.style.location_negative_prompt
        prop_route = ({} if not self.style.dialect_routes_subset
                      else {"route_local": 'prop' in self.style.local_kinds})
        for index, character in enumerate(bible.characters, start=1):
            asset_id = f"character_{index:03d}"
            if asset_id not in character_ids:
                continue
            directory = root / "characters" / asset_id
            prompt = character_prompt(
                bible, character.name, character.appearance, character.base_costume or character.wardrobe,
                age=character.age,
                gender=character.gender,
                visual_archetype=character.visual_archetype, face_anchors=character.face_anchors, silhouette=character.silhouette,
                hair=character.hair, palette=character.palette, motion_signature=character.motion_signature,
                family=self.style.render_family, direction=self.style.render_direction,
                fingerprint=self.style.prompt_fingerprint, tidy=self.style.tidy_prompts,
                brief=self.style.card_brief,
            ) + guard
            # Modern-dress 3D cards came out near-photoreal and were then
            # redrawn by the review; ask for the drawn look up front.
            prompt += _card_suffix(self.style, bible)
            spec = character_spec(asset_id, character, bible, prompt)
            invariants, state, scope = spec['identity_invariants'], spec['state_variables'], spec['reference_scope']
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master,
                                       **route_kw)
            if not primary.request_current:
                continue
            expression_prompt = make_expression_prompt(bible, character.name, character.expression_profile,
                                                   character=character,
                                                   family=self.style.render_family, direction=self.style.render_direction,
                                                   fingerprint=self.style.prompt_fingerprint,
                                                   tidy=self.style.tidy_prompts)
            # Fast production uses one main character card, including when an
            # old expression sheet happens to remain on disk.
            secondary = self.ensure_card(expression_prompt, directory / "expressions.jpeg", reference=primary.path,
                                         **route_kw) if expressions else None
            if secondary is not None and not secondary.request_current:
                continue
            spec_path = directory / "spec.json"
            saved_spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
            # A matched cached request is not a new generation. Preserve its
            # original metadata and timestamp when the rendered inputs agree.
            if (saved_spec.get("prompt") != prompt
                    or (expressions and saved_spec.get("expression_profile", "") != character.expression_profile)):
                atomic_write_json(spec_path, spec)
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
            if location_route.get('route_local'):
                prompt += '。建筑上的牌匾、标识和招牌均为纯色空白面板，以边框和材质呈现。'
            spec = location_spec(asset_id, location, bible, prompt)
            invariants, state, scope = spec['identity_invariants'], spec['state_variables'], spec['reference_scope']
            card = directory / "establishing.jpeg"
            ratio = "16:9" if "16:9" in self.style.frame_text else "9:16"
            before = sha256_file(card) if card.is_file() else None
            image = self.ensure_card(prompt, card, reference=style_master, aspect_ratio=ratio,
                                     **location_route)
            if not image.request_current:
                continue
            # Counted only when the local model drew it just now: a card reused from an earlier run was
            # already looked at, and the hosted model's empty rooms have come back empty.
            if (getattr(self.settings, "local_image_base_url", None)
                    and location_route.get('route_local') is not False and sha256_file(card) != before):
                image = self._keep_empty(prompt, card, image, reference=style_master, aspect_ratio=ratio,
                                         **location_route)
            atomic_write_json(directory / "spec.json", spec)
            locations[asset_id] = AssetRecord(
                asset_id=asset_id, kind="location", name=location, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(image.path.relative_to(root.parent)),
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        props: dict[str, dict] = {}
        for index, prop in enumerate(getattr(bible, "props", None) or [], start=1):
            asset_id = f"prop_{index:03d}"
            if prop_ids is not None and asset_id not in prop_ids:
                continue
            directory = root / "props" / asset_id
            prompt = prop_prompt(bible, prop, family=self.style.render_family,
                                 direction=self.style.render_direction,
                                 fingerprint=self.style.prompt_fingerprint,
                                 tidy=self.style.tidy_prompts or bool(self.style.prop_brief),
                                 brief=self.style.prop_brief) + guard
            spec = prop_spec(asset_id, prop, bible, prompt)
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master,
                                       negative_prompt=self.style.prop_negative_prompt,
                                       **prop_route)
            if not primary.request_current:
                continue
            detail = self.ensure_card(prompt + "局部材质特写，纹理与工艺细节占满画面。",
                                      directory / "detail.jpeg", reference=primary.path,
                                      negative_prompt=self.style.prop_negative_prompt,
                                      **prop_route) if prop.closeup else None
            if detail is not None and not detail.request_current:
                continue
            atomic_write_json(directory / "spec.json", spec)
            props[asset_id] = AssetRecord(
                asset_id=asset_id, kind="prop", name=prop.name,
                identity_invariants=spec["identity_invariants"], state_variables=spec["state_variables"],
                reference_scope=spec["reference_scope"],
                spec_path=str((directory / "spec.json").relative_to(root.parent)),
                primary_image=str(primary.path.relative_to(root.parent)),
                secondary_image=str(detail.path.relative_to(root.parent)) if detail else None,
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        if not (characters or locations or props) and manifest_path.is_file():
            return SeriesAssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        return merge_manifest(root, bible.style_fingerprint, characters, locations, voices, props)
