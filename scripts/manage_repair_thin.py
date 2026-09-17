"""manage_repair_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
from novel_manga.util import atomic_write_json
from pathlib import Path
import argparse
import json
import time
import novel_manga.repair.scheduling as schedule_rules
import repair_manager_flow_thin as repair_manager_flow
import repair_manager_state_thin as repair_manager_state
import repair_manager_workers_thin as repair_manager_workers

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "status", "progress", "pause", "resume", "preview"])
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, default=Path("/mnt/disk1/zengzhitao/tmp/fix"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--adopt-legacy", action="store_true")
    parser.add_argument('--prepared-only', action='store_true', help='admit new episodes only after current H3 book preparation passes')
    parser.add_argument('--model-workers', type=int, choices=range(1, 13), default=12,
                        help='parallel episode preparations; reduce while another book uses Qwen')
    args = parser.parse_args()
    schedule_rules.STAGE_CAPACITY['repair_model'] = args.model_workers
    manager = repair_manager_flow.Manager(args.novel_dir, args.legacy_dir, args.state_dir)
    if args.action == "run":
        manager.state["model_workers"] = args.model_workers
    if args.prepared_only:
        manager.state['preparation_gate'] = True
    if args.action == "preview":
        snapshot = repair_manager_workers.adoption_preview(manager)
        print(json.dumps({"controllers_to_retire": [{"pid": p["pid"], "entry": p["args"][-1]} for p in snapshot["controllers"]],
                          "workers_to_keep": snapshot["workers"], "current_batches": {"A": snapshot["a_batch"], "B": snapshot["b_batch"]}}, ensure_ascii=False, indent=2))
    elif args.action == "progress":
        # Read-only with respect to production files and the coordinator's state.
        snapshot = repair_manager_state.read_snapshot(manager)
        report = {"generated_at": time.strftime("%F %T"), "total_episodes": len(snapshot.inspection_rows),
                  **snapshot.state["summary"]["inspection"], "episodes": snapshot.inspection_rows}
        manager.directory.mkdir(parents=True, exist_ok=True)
        path = manager.directory / "inspection_progress.json"
        atomic_write_json(path, report)
        print(json.dumps({k: v for k, v in report.items() if k != "episodes"}, ensure_ascii=False, indent=2))
        print(f"Details: {path}")
    elif args.action == "status":
        state = manager.state
        print(json.dumps({"status": state.get("status", "not_started"), "alive": repair_manager_workers.alive(state.get("pid")), "phase": state["phase"],
                          "updated_at": state.get("updated_at"), "audit_policy": state.get("audit_policy"),
                          "dispatch_policy": state.get("dispatch_policy"), "summary": state.get("summary"),
                          "jobs": [{"id": j["id"], "kind": j["kind"], "stage": schedule_rules.FLOWS[j["kind"]][j["step"]] if j["kind"] in schedule_rules.FLOWS else j.get("source", j["kind"]),
                                    "status": j["status"], "episode_count": len(j["episodes"]), "pid": j.get("pid"),
                                    "failures": j.get("failures", 0), "log": j.get("log")}
                                   for j in state["jobs"] if j["status"] not in {"done", "split", "superseded"}]}, ensure_ascii=False, indent=2))
    elif args.action in {"pause", "resume"}:
        manager.directory.mkdir(parents=True, exist_ok=True)
        pause = manager.directory / "pause"
        pause.write_text(time.strftime("%F %T")) if args.action == "pause" else pause.unlink(missing_ok=True)
        print("pause requested; current steps may finish" if args.action == "pause" else "resume requested")
    else:
        manager.run(args.adopt_legacy)


if __name__ == "__main__":
    raise SystemExit(main())
