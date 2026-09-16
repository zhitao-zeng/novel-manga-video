"""repair_manager_state_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
from collections import Counter
from novel_manga.util import read_json as read
import time
import clip_readiness as clip_readiness
import novel_manga.repair.scheduling as schedule_rules
import novel_manga.review.reconciliation as reconciliation
import review_store_thin as review_store
import thin_runs as thin_runs

def refresh(manager, *, write: bool = True):
    local, flash = review_store.current_evidence(manager.legacy, manager.directory)
    busy = schedule_rules.active_episodes(manager.state)
    held = {n for j in manager.state["jobs"] if j["status"] in {"held", "needs_attention"} for n in j["episodes"]}
    held.update(int(n) for n, count in manager.state["fill_failures"].items() if count >= 3)
    info = {}
    inspection_rows = []
    plan_queue = {}
    admitted = set(manager.state.get('admitted_episodes', []))
    for directory in manager.novel.glob(f"{manager.novel.name}_*"):
        suffix = directory.name.rsplit("_", 1)[-1]
        if not directory.is_dir() or not suffix.isdigit():
            continue
        n = int(suffix)
        if manager.episode_scope is not None and n not in manager.episode_scope:
            continue
        if manager.state.get('preparation_gate') and n not in admitted:
            from preparation_store_thin import inputs as preparation_inputs
            from novel_manga.planning.preparation import POLICY as PREPARATION_POLICY
            preparation = read(manager.novel / 'h3_preparation/episodes' / f'{n}.json', {})
            if (preparation.get('policy') == PREPARATION_POLICY and preparation.get('status') == 'ready'
                    and preparation.get('inputs') == preparation_inputs(directory)):
                admitted.add(n)
            else:
                # Preparation owns these files. Do not write empty video
                # reviews or start structural repairs while it is editing.
                info[n] = {'status': 'pending', 'bad': 0, 'unverified': 0, 'flash_pending': 0,
                           'can_fill': False, 'deliverable': False, 'held': False, 'ready': False,
                           'plan_blocked': False, 'exposure_due': False, 'managed_clips': [],
                           'managed_blocked': {}, 'awaiting_preparation': True}
                inspection_rows.append({'episode': n, 'status': 'pending', 'bucket': 'awaiting_preparation',
                                        'total': 0, 'passed': 0, 'failed': 0, 'unchecked': 0,
                                        'unconfirmed_candidates': 0, 'flash_pending': 0, 'processed_cycles': 0})
                continue
        try:
            status = thin_runs.episode_status(directory, True)
            blocked = clip_readiness.current_blocks(directory) if status == "plan_blocked" else {}
            if blocked:
                plan_queue[str(n)] = blocked
            review, takes = review_store.reconcile(directory, local, flash, write=write and n not in busy)
            from repair_history import observe
            from repair_delivery_thin import publication_pending, publish_if_ready
            if write and n not in busy:
                observe(directory, review, takes)
                publish_if_ready(directory, review, takes)
            pending_publication = publication_pending(directory)
            media = read(directory / "thin_media_report.json", {}) if status == "done_with_warnings" else {}
            black_targets = read(directory / "technical_repair.json", {}).get("black_clips", []) if media else []
            exposure_due = any(c.get("clip_id") in black_targets and (c.get("selected") or {}).get("black_check_policy") not in {2, 3}
                               for c in media.get("clips", []))
            plan = read(directory / "clip_plan.json", {})
            expected = [c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"]
            clips = review.get("clips") or {}
            inspected = reconciliation.inspection_counts(review, takes, expected)
            unverified = inspected["unchecked"]
            inspection_rows.append({"episode": n, "status": status, "bucket": schedule_rules.inspection_bucket(status, inspected),
                                    "processed_cycles": manager.state["passes"].get(str(n), 0), **inspected})
            bad = len(review.get("feedback") or {})
            ready = status in {"done", "done_with_warnings"}
            from managed_repair_thin import candidates as repair_candidates
            managed_clips,managed_blocked = repair_candidates(directory,review) if (ready and bad) or blocked else ([],{})
            flash_pending = sum(bool(v.get("flash_pending")) for v in clips.values())
            info[n] = {"status": status, "bad": bad, "unverified": unverified if ready else 0,
                       "flash_pending": flash_pending,
                       "can_fill": status in {"stale", "pending", "clips_failed"} and thin_runs.render_runs(directory) < thin_runs.RENDER_RUNS_PER_PLAN and n not in held,
                       "deliverable": status == "done" and bad == 0 and unverified == 0 and not flash_pending and not pending_publication,
                       "held": n in held, "ready": ready, "plan_blocked": bool(blocked), "exposure_due": exposure_due,
                       'managed_clips':managed_clips,'managed_blocked':managed_blocked}
        except (OSError, ValueError, KeyError):
            info[n] = {"status": "unreadable", "bad": 0, "unverified": 0, "flash_pending": 0,
                       "can_fill": False, "deliverable": False, "held": True, "ready": False,
                       'managed_clips':[],'managed_blocked':{}}
            inspection_rows.append({"episode": n, "status": "unreadable", "bucket": "unreadable",
                                    "total": 0, "passed": 0, "failed": 0, "unchecked": 0,
                                    "unconfirmed_candidates": 0, "flash_pending": 0, "processed_cycles": manager.state["passes"].get(str(n), 0)})
    manager.info = info
    if manager.state.get('preparation_gate'):
        manager.state['admitted_episodes'] = sorted(admitted)
    for job in manager.state["jobs"]:
        if (job["kind"] == "recovery" and job["status"] == "done"
                and job.get("outcome") in {None, "awaiting_current_delivery_check", "awaiting_review_or_publication"}):
            row = info.get(job["episodes"][0], {})
            job["outcome"] = ("passed" if row.get("deliverable") else "plan_blocked" if row.get("plan_blocked")
                              else "visual_error" if row.get("bad") else "technical_error" if row.get("status") != "done"
                              else "awaiting_review_or_publication")
    manager.state["plan_queue"] = plan_queue
    manager.inspection_rows = sorted(inspection_rows, key=lambda r: r["episode"])
    manager.state["summary"] = {"total": len(info), "deliverable_precise": sum(r["deliverable"] for r in info.values()),
                             "must_fix_clips": sum(r["bad"] for r in info.values()),
                             "unverified_clips": sum(r["unverified"] for r in info.values()),
                             "technical": dict(Counter(r["status"] for r in info.values())),
                             "held_episodes": sorted(n for n, r in info.items() if r["held"]),
                             "plan_blocked_episodes": sorted(map(int, plan_queue)),
                             "plan_blocked_clips": sum(len(clips) for clips in plan_queue.values()),
                             'repair_ready_clips':sum(len(r['managed_clips']) for r in info.values()),
                             'repair_blocked_clips':sum(len(r['managed_blocked']) for r in info.values()),
                             "residual_episodes": sorted(n for n, r in info.items() if r["bad"] and manager.state["passes"].get(str(n), 0) >= 2)}
    if manager.state.get('preparation_gate'):
        preparation = read(manager.novel / 'h3_preparation/status.json', {})
        manager.state['summary']['preparation'] = {
            'admitted': len(admitted), 'waiting': sum(bool(r.get('awaiting_preparation')) for r in info.values()),
            'status': preparation.get('status'), 'counts': preparation.get('counts', {}),
            'updated_at': preparation.get('at')}
    manager.state["summary"]["inspection"] = {
        "episode_buckets": dict(Counter(r["bucket"] for r in inspection_rows)),
        "clips": {k: sum(r[k] for r in inspection_rows) for k in
                  ["total", "passed", "failed", "unchecked", "unconfirmed_candidates", "flash_pending"]},
        "episodes_with_confirmed_errors": sum(r["failed"] > 0 for r in inspection_rows),
        "error_episodes_still_partly_unchecked": sum(r["failed"] > 0 and r["unchecked"] > 0 for r in inspection_rows),
        "episodes_with_no_precise_check": sum(r["total"] > 0 and r["unchecked"] == r["total"] for r in inspection_rows),
        "processed_episodes_now_passed": sum(r["bucket"] == "passed" and r["processed_cycles"] > 0 for r in inspection_rows),
    }
    manager.state["summary"]["recovery"] = {
        kind: dict(Counter(j["status"] for j in manager.state["jobs"]
                           if j["kind"] == "recovery" and j.get("recovery_kind") == kind))
        for kind in ("plan", "references", "technical", "residual", "identity", "binding", "speech", "review", 'source','managed','entities')}
    manager.state["summary"]["recovery_outcomes"] = dict(Counter(
        j.get("outcome", "unknown") for j in manager.state["jobs"] if j["kind"] == "recovery" and j["status"] == "done"))
    from novel_manga.review.audit_queue import summary as audit_summary
    manager.state["summary"]["shared_audit"] = audit_summary(manager.directory / 'shared_audit.sqlite3')
    manager.last_refresh = time.monotonic()
