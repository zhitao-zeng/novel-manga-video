"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext




from novel_manga.util import atomic_write_json, read_json
import fcntl
import json
import time
import novel_manga.planning.outputs as pc_outputs
import novel_manga.planning.metrics as pc_metrics
import novel_manga.planning.text as pc_text
import novel_manga.planning.validation as pc_validation
import novel_manga.planning.gate_stats as pc_gate_stats
import novel_manga.application.planning.context as planner_context


from . import source_menu
from .run_types import PlanningInputs, PlanningOutcome


def publish_result(args, ctx: PlannerContext, inputs: PlanningInputs, outcome: PlanningOutcome) -> int:
    episode = inputs.source.episode
    novel_dir = inputs.source.novel_dir
    episode_dir = inputs.source.episode_dir
    profile = inputs.source.profile
    method = inputs.source.method
    planning_budget = inputs.source.planning_budget
    segments = inputs.source.segments
    bible = inputs.cast.bible
    location_map = inputs.cast.location_map
    source_places = inputs.cast.source_places
    recap_path = inputs.cast.recap_path
    bible = inputs.bible
    location_map = inputs.location_map
    prop_all = inputs.prop_all
    prop_names = inputs.prop_names
    result = outcome.result
    attempts = outcome.attempts
    last_issues = outcome.last_issues
    final_errors = outcome.final_errors
    posture_states = outcome.posture_states
    started = outcome.started
    gate_run = outcome.gate_run
    if result is None:
        failure = {
            "status": "planning_failed",
            "policy": ctx.policy,
            "episode_index": episode.index,
            "attempts": attempts,
            "errors": final_errors,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
        atomic_write_json(episode_dir / "planning_failed.json", failure)
        print(json.dumps({"status": "planning_failed", "errors": final_errors}, ensure_ascii=False, indent=2))
        pc_gate_stats.finish_gate_run(novel_dir, episode.index, gate_run, passed=False,
                                     fatal_codes=[issue.code.code for issue in last_issues])
        return 2

    raw, shots, warnings = result
    # A new script invalidates everything downstream: the packed plan must be
    # rebuilt and a finished-episode report from the old plan would make batch
    # drivers skip the episode as done.
    for stale in ("clip_plan.json", "clip_plan.md", "thin_media_report.json", "media_qc_report.json"):
        (episode_dir / stale).unlink(missing_ok=True)
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    report_metrics = pc_metrics.metrics(shots, episode.source_text, segments, skipped)
    report = {
        "status": "passed",
        "policy": ctx.policy,
        "model": args.model,
        "planning_budget": planning_budget,
        "outline_mode": f"method:{method.key}" if method else args.outline_mode,
        **({'story_method': method.describe()} if method else {}),
        "seed": args.seed,
        "episode_index": episode.index,
        "source_text_sha256": pc_text.sha256_text(episode.source_text),
        "style_fingerprint": bible.style_fingerprint,
        "hard_gates": {"source_provenance": "passed", "cast_and_speakers": "passed"},
        "adaptation_review": ((read_json(episode_dir/"planning_attempt.json", {}).get("chapter_review") or {}).get("verdict", "not_run")),
        **({'story_method_checks': {'source_ownership': 'passed', 'beat_source_links': 'passed',
                                    'semantic_quality_review': 'passed' if ctx.story_blueprint.get('review', {}).get('completed') else 'not_run'}} if method else {}),
        "metrics": report_metrics,
        "warnings": [*warnings, *pc_validation.soft_warnings(report_metrics, ctx=ctx)],
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    # Prop appearances were read with presence in the same
    # current-candidate review; publication must not buy a fourth interpretation.
    source_menu.adopt_selected(novel_dir, source_places, shots)
    plan = pc_outputs.to_episode_plan(raw, shots, location_map, episode.source_text, episode.source_title, ctx=ctx)
    atomic_write_json(episode_dir / "chapter_script.json", {"video_title": raw.get("video_title"), "source_title": episode.source_title, "episode_index": episode.index, "profile": profile, "hook": raw.get("hook"), "summary": raw.get("summary"), "clip_count": len(raw.get("clips") or []), "shots": shots, "skipped_segments": skipped,
        **({'story_method': method.describe(), 'story_blueprint': ctx.story_blueprint} if method else {})})
    if posture_states is not None:
        atomic_write_json(episode_dir / 'posture_states.json', posture_states)
    atomic_write_json(episode_dir / "chapter_script_report.json", report)
    atomic_write_json(episode_dir / "chapter_script_report.json", report)
    with open(recap_path.with_suffix(".lock"), "w") as lock:  # planners may run in parallel
        fcntl.flock(lock, fcntl.LOCK_EX)
        recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
        recap = [row for row in recap if int(row.get("chapter", 0)) != episode.index]
        recap.append({"chapter": episode.index, "title": episode.source_title, "summary": raw.get("summary"), "hook": raw.get("hook")})
        atomic_write_json(recap_path, sorted(recap, key=lambda row: int(row.get("chapter", 0))))
    planner_context.record_cast(novel_dir, episode.index,
                sorted({name for shot in shots for name in shot.get("characters", []) or []}),
                sorted({shot["location"] for shot in shots if shot.get("location")}))
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    (episode_dir / "chapter_script.md").write_text(pc_outputs.render_markdown(raw, shots, report, episode.source_title,
        blueprint=ctx.story_blueprint if method else None), encoding="utf-8")
    if ctx.story_blueprint.get('version') == 'scene-screenplay-v2':
        from novel_manga.planning.methods.scenes import render_screenplay
        (episode_dir / 'scene_script.md').write_text(render_screenplay(ctx.story_blueprint), encoding='utf-8')
    (episode_dir / 'planning_failed.json').unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "episode_dir": str(episode_dir), "metrics": report_metrics, "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    pc_gate_stats.finish_gate_run(novel_dir, episode.index, gate_run, passed=True)
    return 0
