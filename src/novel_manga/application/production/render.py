"""production_render_thin responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
import copy
import json
import os
import sys
import time
import novel_manga.application.production.common as production_common
import novel_manga.application.production.runs as thin_runs

def render(batch, chapter: int) -> None:
    row = batch.rows[chapter]
    directory = batch.episode_dir(chapter)
    status = batch.render_status(chapter)
    row["render_before"] = status
    if status == "no_plan":
        row["render"] = "skipped (no plan)"
        return
    lane_cap = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "0") or 0)
    if lane_cap:
        # This lane's model only takes clips up to lane_cap seconds: a plan
        # packed for longer clips belongs to the other lane (or is waiting to
        # be re-planned) and must not be submitted here.
        try:
            packed_cap = float(json.loads((directory / "clip_plan.json").read_text(encoding="utf-8")).get("limits", {}).get("max_clip_seconds", 0) or 0)
        except (OSError, ValueError):
            packed_cap = 0.0
        if packed_cap > lane_cap:
            row["render"] = f"skipped (clip plan packed for {packed_cap:g} s, lane takes {lane_cap:g} s)"
            return
    free = bool(os.environ.get("NOVEL_LOCAL_H3_URL"))
    # A final with clips that failed the speech gate is a preview, not a finished episode.  A paid lane leaves
    # it to a person to decide whether to pay for more (--retake-failed, for one approved batch); a free one
    # (local H3) takes it back for fresh takes of the failed clips - the runner does not count its cached
    # failures against them - until the runs for this plan are used up.  Only the speech gate's failures are
    # taken back: a final that fails a media check on clips that all passed would only be put together again
    # from the same clips, so it waits for a person.
    retake = (status == "done_with_warnings" and (free or batch.args.retake_failed) and bool(thin_runs.gate_failures(directory))
              and thin_runs.render_runs(directory) < thin_runs.RENDER_RUNS_PER_PLAN and not batch.args.no_render)
    if status in {"done", "done_with_warnings"} and not retake and not batch.args.rerender:
        row["render"] = status
        batch.fill_result(chapter)
        if batch.reviewing:
            try:
                batch.review_episode(chapter)
            except Exception as error:  # noqa: BLE001
                row["note"] = f"review failed: {type(error).__name__}: {str(error)[:120]}"
                production_common.log(f"ch{chapter}: episode review failed ({type(error).__name__}: {str(error)[:120]})")
        return
    if batch.args.no_render:
        # A review job: the conductor spawns it without the novel's render key, so a render here would run
        # on whatever model its own environment names - sd2.5 for 雾月 on 2026-09-11, when one re-stitched
        # 60 invalidated episodes from their cached clips.
        row["render"] = f"skipped (review job, {status})"
        return
    if batch.args.dry_run:
        row["render"] = f"would render ({status})"
        return
    lock = directory / ".render.lock"
    if lock.is_file():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = 0
        if pid and production_common.pid_alive(pid):
            row["render"] = f"locked by pid {pid}"
            production_common.log(f"ch{chapter}: another render (pid {pid}) is running; skipped")
            return
        lock.unlink(missing_ok=True)
    if not batch.args.cache_only:
        from novel_manga.application.packing.ranges import repair_episode
        recovered = repair_episode(directory, apply=True)
        if recovered["changed"]:
            production_common.log(f"ch{chapter}: restored split dialogue for {', '.join(recovered['changed'])}; refreshing prompts")
        if recovered["skipped"]:
            production_common.log(f"ch{chapter}: split ranges left unchanged: {recovered['skipped']}")
    if batch.fast and not batch.args.cache_only:
        from novel_manga.application.packing.single_card import single_card_plan, repair_missing_expressions
        from novel_manga.util import atomic_write_json
        plan_path = directory / "clip_plan.json"
        restored=repair_missing_expressions(directory)
        if restored['changed']:
            production_common.log(f"ch{chapter}: removed missing expression references in {len(restored['changed'])} clips; retained {len(restored['retained'])} already approved videos")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        targets = None
        if (directory/'episode_review.json').is_file():
            # Existing reviewed footage keeps its exact cached references.
            # Repair preparation normalizes changed clips before sealing
            # their trials; here only missing material needs normalization.
            from novel_manga.util import read_json as read
            from novel_manga.application.review.store import current_takes
            takes=current_takes(directory,plan,read(directory/'episode_review.json',{}))
            targets={clip['clip_id'] for clip in plan['clips'] if clip['clip_id'] not in takes}
        before_plan=copy.deepcopy(plan)
        changed = single_card_plan(plan,targets)
        if changed:
            from novel_manga.application.repair.history import refresh_prepared_plan
            refresh_prepared_plan(directory,before_plan,plan,thin_runs.corrections(directory))
            atomic_write_json(plan_path, plan)
            production_common.log(f"ch{chapter}: main character card only for {', '.join(changed)}; refreshing reference bindings")
    if not batch.args.cache_only:
        from novel_manga.application.preparation.readiness import inspect_episode, may_reuse_duration_cache, save_check
        plan, blocked = inspect_episode(directory)
        video_ids = {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"}
        if video_ids and video_ids <= blocked.keys() and not any(may_reuse_duration_cache(directory, cid, reasons) for cid, reasons in blocked.items()):
            save_check(directory, plan, blocked)
            row.update(render="plan_blocked", note="all clips await corrected plans; no generation run spent")
            production_common.log(f"ch{chapter}: all {len(blocked)} clips wait for corrected plans; no video requested")
            return
    if free and not batch.args.cache_only and not batch.h3_ready(chapter):
        row["render"] = "skipped (H3 prompt not ready)"
        return
    # Three runs per clip plan and set of director corrections, then stop: a clip that fails the same
    # gate every time (a silent generation, say) would otherwise be regenerated and paid for on every
    # round of the lane loop.  A re-plan or a new correction starts the count again (thin_runs.py).
    # Counted only once the episode really renders: a round turned away by another render's lock used
    # to spend a run too, and three of those gave an episode up without a single generation.
    runs = thin_runs.render_runs(directory)
    # Held submissions get past the limit when the person rerunning with --resubmit-unconfirmed has checked the
    # bill: the rounds before were spent on the held clip, and the limit turned away the one rerun that could
    # release it.
    held = getattr(batch.args, "resubmit_unconfirmed", False) and production_common.held_submissions(directory)
    if runs >= thin_runs.RENDER_RUNS_PER_PLAN and not batch.args.cache_only and not held:
        row["render"] = f"gave up ({runs} render runs on this plan)"
        row["note"] = "needs a look: same failure on every run; see thin_media_report.json"
        production_common.log(f"ch{chapter}: gave up after {runs} render runs on this plan; needs a look")
        return
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        if not batch.args.cache_only:  # cards are only built, judged and redrawn for clips about to be generated
            batch.prepare_cards(chapter)
            plan, blocked = inspect_episode(directory, assets=True)
            save_check(directory, plan, blocked)
            video_ids = {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"}
            if video_ids and video_ids <= blocked.keys() and not any(may_reuse_duration_cache(directory, cid, reasons) for cid, reasons in blocked.items()):
                row.update(render="plan_blocked", note="all clips await plans/assets; no generation run spent")
                production_common.log(f"ch{chapter}: no executable clips after asset preparation; no video requested")
                return
            if not video_ids or video_ids - blocked.keys():
                thin_runs.count_run(directory)
        command = [sys.executable, str(production_common.SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(batch.novel_dir), "--episode", directory.name, "--workers", str(batch.args.workers), "--inflight", str(batch.args.inflight)] + (["--tier", batch.args.tier] if batch.args.tier else []) + (["--prescreen"] if batch.args.prescreen else []) + ([] if batch.args.moderation_repair else ["--no-moderation-repair"]) + (["--cache-only"] if batch.args.cache_only else [])
        # Fresh paid takes of gate-failed clips only for the final a person approved them for, and only on the
        # first call: a second call counted the takes the first had just bought as cached failures and bought two
        # more of each, and stale or clips_failed episodes got paid retakes nobody had asked for.
        first = command + (["--retake-failed"] if retake and not free and batch.args.retake_failed else [])
        for attempt in ((1,) if batch.args.cache_only else (1, 2)):
            production_common.log(f"ch{chapter}: rendering (attempt {attempt})")
            code, problem = batch.run(first if attempt == 1 else command, directory / "render.log")
            status = batch.render_status(chapter)
            if status in {"done", "done_with_warnings"}:
                break
            production_common.log(f"ch{chapter}: render attempt {attempt} ended with {status} ({problem[:120]})")
            if attempt == 1 and status in {"clips_failed", "pending", "stale"}:
                continue
            break
        replans = sum((directory / marker).exists() for marker in production_common.MODERATION_MARKERS)
        if status == "clips_failed" and not batch.args.cache_only and batch.moderation_blocked(chapter) and replans < len(production_common.MODERATION_MARKERS):
            # Seedance refused the text or the output twice: re-plan the chapter
            # with a director note that keeps the sensitive beats indirect - the
            # second time naming exactly what was refused.
            (directory / production_common.MODERATION_MARKERS[replans]).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
            targets = batch.moderation_targets(chapter)
            production_common.log(f"ch{chapter}: content moderation blocked a clip twice; re-planning ({replans + 1}/{len(production_common.MODERATION_MARKERS)}) with a toned-down note"
                + (" naming the refused lines" if targets else ""))
            from novel_manga.application.profiles import load_genre
            notes = production_common.MODERATION_NOTE + load_genre(batch.profile).get("moderation_note_extra", "") + targets
            batch.plan(chapter, replan=True, notes=notes)
            if batch.rows[chapter].get("plan") == "planned":
                batch.prepare_cards(chapter)
                code, problem = batch.run(command, directory / "render.log")
                status = batch.render_status(chapter)
                row["moderation_replanned"] = True
        row["render"] = status
        if status not in {"done", "done_with_warnings"}:
            row["note"] = problem
        batch.fill_result(chapter)
        if batch.reviewing and status in {"done", "done_with_warnings"} and not batch.fast:
            try:
                batch.review_episode(chapter)
            except Exception as error:  # noqa: BLE001 - the episode is done; a review failure is a note
                row["note"] = f"review failed: {type(error).__name__}: {str(error)[:120]}"
                production_common.log(f"ch{chapter}: episode review failed ({type(error).__name__}: {str(error)[:120]})")
        if batch.args.prune and batch.render_status(chapter) in {"done", "done_with_warnings"}:
            production_common.prune_episode(directory)
    finally:
        lock.unlink(missing_ok=True)
