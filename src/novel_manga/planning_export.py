from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import Settings
from .ingest import read_novel
from .models import (
    ChapterDiagnosis,
    EpisodePlan,
    ScriptQualityReport,
    SeriesState,
    StoryBible,
)
from .planner import OpenAICompatiblePlanner
from .production import compile_production_plan
from .production_models import AssetRecord, SeriesAssetManifest
from .providers import build_planner
from .script_planning import evaluate_script_quality, validate_series_state
from .util import atomic_write_json


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_planning_bundle(
    settings: Settings,
    source: str | Path,
    *,
    novel_id: str,
    title: str | None = None,
) -> dict:
    """Run only the model-neutral planning stage and persist validated JSON artifacts."""

    novel = read_novel(source, novel_id=novel_id, title=title)
    planner = build_planner(settings)
    root = settings.output_root.resolve() / novel.novel_id
    root.mkdir(parents=True, exist_ok=True)

    bible = planner.build_bible(novel)
    bible_path = root / "story_bible.json"
    atomic_write_json(bible_path, bible.model_dump(mode="json"))

    episode_rows = []
    previous_state: SeriesState | None = None
    for episode in novel.episodes:
        bundle = planner.plan_episode_bundle(novel, episode, bible, previous_state)
        plan = bundle.plan
        previous_state = bundle.updated_series_state
        plan_path = root / f"episode_{episode.index:03d}_plan.json"
        diagnosis_path = root / f"episode_{episode.index:03d}_diagnosis.json"
        quality_path = root / f"episode_{episode.index:03d}_script_quality.json"
        state_path = root / f"episode_{episode.index:03d}_series_state.json"
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        atomic_write_json(diagnosis_path, bundle.diagnosis.model_dump(mode="json"))
        atomic_write_json(quality_path, bundle.quality_report.model_dump(mode="json"))
        atomic_write_json(state_path, previous_state.model_dump(mode="json"))
        episode_rows.append(
            {
                "index": episode.index,
                "source_title": episode.source_title,
                "text_count": episode.text_count,
                "source_text_sha256": _sha256_text(episode.source_text),
                "plan": plan_path.name,
                "plan_sha256": _sha256_file(plan_path),
                "diagnosis": diagnosis_path.name,
                "diagnosis_sha256": _sha256_file(diagnosis_path),
                "script_quality": quality_path.name,
                "script_quality_sha256": _sha256_file(quality_path),
                "updated_series_state": state_path.name,
                "updated_series_state_sha256": _sha256_file(state_path),
                "shot_count": len(plan.shots),
                "turn_count": sum(len(shot.turns) for shot in plan.shots),
            }
        )

    manifest = {
        "contract": "novel-manga-planning/v3",
        "novel_id": novel.novel_id,
        "novel_title": novel.title,
        "chaptered": novel.chaptered,
        "episode_count": len(novel.episodes),
        "source_sha256": _sha256_text(novel.text),
        "planner": {
            "backend": settings.planner_backend,
            "model": settings.llm_model if settings.planner_backend == "openai-compatible" else None,
            "max_revisions": settings.planner_max_revisions,
            "base_url_sha256": (
                _sha256_text(settings.llm_base_url)
                if settings.planner_backend == "openai-compatible" and settings.llm_base_url
                else None
            ),
            "credentials_persisted": False,
        },
        "story_bible": bible_path.name,
        "story_bible_sha256": _sha256_file(bible_path),
        "episodes": episode_rows,
    }
    if previous_state is not None:
        latest_state = root / "series_state.json"
        atomic_write_json(latest_state, previous_state.model_dump(mode="json"))
        manifest["series_state"] = latest_state.name
        manifest["series_state_sha256"] = _sha256_file(latest_state)
    manifest_path = root / "planning_manifest.json"
    atomic_write_json(manifest_path, manifest)
    return {**manifest, "output_directory": str(root), "manifest": str(manifest_path)}


def repair_planning_bundle(bundle_dir: str | Path) -> dict:
    """Normalize model-emitted character aliases to reusable StoryBible asset names."""

    root = Path(bundle_dir).resolve()
    manifest_path = root / "planning_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bible = StoryBible.model_validate_json((root / "story_bible.json").read_text(encoding="utf-8"))
    normalizer = object.__new__(OpenAICompatiblePlanner)
    repaired = 0
    for row in manifest["episodes"]:
        plan_path = root / row["plan"]
        plan = EpisodePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        normalized = normalizer._canonicalize_characters(plan, bible)
        if normalized != plan:
            repaired += 1
            atomic_write_json(plan_path, normalized.model_dump(mode="json"))
            row["plan_sha256"] = _sha256_file(plan_path)
    atomic_write_json(manifest_path, manifest)
    return {
        "bundle": str(root),
        "episode_count": len(manifest["episodes"]),
        "repaired_episode_count": repaired,
        "manifest": str(manifest_path),
    }


def _planned_assets(bible: StoryBible) -> SeriesAssetManifest:
    """Build path-only asset bindings for compile-time contract validation.

    These records describe the stable paths the media stage will materialize. No
    image, audio, or video file is created by planning compilation.
    """

    characters = []
    voice_assignments = {"narrator": "narrator"}
    for index, character in enumerate(bible.characters, start=1):
        asset_id = f"character_{index:03d}"
        characters.append(
            AssetRecord(
                asset_id=asset_id,
                kind="character",
                name=character.name,
                spec_path=f"series_assets/characters/{asset_id}/spec.json",
                primary_image=f"series_assets/characters/{asset_id}/turnaround.jpeg",
                prompt_sha256=_sha256_text(
                    "|".join(
                        (
                            bible.style_fingerprint,
                            character.name,
                            character.appearance,
                            character.wardrobe,
                        )
                    )
                ),
            )
        )
        voice_assignments[character.name] = f"voice_{index:03d}"

    locations = []
    for index, location in enumerate(
        dict.fromkeys(bible.locations or ["原文主要场景"]), start=1
    ):
        asset_id = f"location_{index:03d}"
        locations.append(
            AssetRecord(
                asset_id=asset_id,
                kind="location",
                name=location,
                spec_path=f"series_assets/locations/{asset_id}/spec.json",
                primary_image=f"series_assets/locations/{asset_id}/establishing.jpeg",
                prompt_sha256=_sha256_text(f"{bible.style_fingerprint}|{location}"),
            )
        )

    return SeriesAssetManifest(
        style_fingerprint=bible.style_fingerprint,
        characters=characters,
        locations=locations,
        voice_assignments=voice_assignments,
    )


def compile_planning_bundle(
    source: str | Path,
    bundle_dir: str | Path,
    *,
    novel_id: str,
    title: str | None = None,
) -> dict:
    """Compile model-neutral planning JSON into downstream production contracts."""

    novel = read_novel(source, novel_id=novel_id, title=title)
    root = Path(bundle_dir).resolve()
    manifest = json.loads((root / "planning_manifest.json").read_text(encoding="utf-8"))
    bible = StoryBible.model_validate_json(
        (root / manifest["story_bible"]).read_text(encoding="utf-8")
    )
    if len(manifest["episodes"]) != len(novel.episodes):
        raise ValueError("planning bundle episode count does not match parsed source")

    assets = _planned_assets(bible)
    results = []
    previous_state: SeriesState | None = None
    for episode, row in zip(novel.episodes, manifest["episodes"], strict=True):
        if row["source_text_sha256"] != _sha256_text(episode.source_text):
            raise ValueError(f"episode {episode.index} source hash does not match planning bundle")
        plan = EpisodePlan.model_validate_json(
            (root / row["plan"]).read_text(encoding="utf-8")
        )
        diagnosis = ChapterDiagnosis.model_validate_json(
            (root / row["diagnosis"]).read_text(encoding="utf-8")
        )
        stored_quality = ScriptQualityReport.model_validate_json(
            (root / row["script_quality"]).read_text(encoding="utf-8")
        )
        quality = evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            qualitative=stored_quality,
            previous_state=previous_state,
        )
        if not quality.passed:
            raise ValueError(
                f"episode {episode.index} failed script quality gate before compilation"
            )
        state = validate_series_state(
            SeriesState.model_validate_json(
                (root / row["updated_series_state"]).read_text(encoding="utf-8")
            ),
            episode,
            previous_state,
        )
        previous_state = state
        production = compile_production_plan(
            f"{novel.novel_id}_{episode.index}", episode, plan, bible, assets
        )
        output = root / f"episode_{episode.index:03d}_production_plan.json"
        atomic_write_json(output, production.model_dump(mode="json"))
        results.append(
            {
                "index": episode.index,
                "production_plan": output.name,
                "production_plan_sha256": _sha256_file(output),
                "scene_count": len(production.scenes),
                "shot_count": len(production.shots),
                "unit_count": len(production.units),
                "visible_speaking_unit_count": sum(unit.speaking for unit in production.units),
                "reference_audio_prompt_count": sum(
                    "参考音频" in unit.motion_prompt for unit in production.units
                ),
            }
        )
    return {
        "contract": "novel-manga-production/v1",
        "bundle": str(root),
        "episode_count": len(results),
        "episodes": results,
        "media_generated": False,
    }
