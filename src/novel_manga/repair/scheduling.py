"""repair.scheduling responsibilities; existing job state and scheduling policy."""
from __future__ import annotations


REPAIR = ["check", "repair", "render", "review", "note1", "render1", "review1", "note2", "render2", "review2"]


FLOWS = {"repair": REPAIR, "fill": ["render", "review"], "audit": ["audit"], "confirm": ["confirm"],
         "recovery": ["recover", "render", "review"]}


REPAIR_BATCH_SIZE = 12  # one wave of the renderer's 12 episode workers; fewer slow-tail barriers


FILL_REVIEW_BACKLOG = 2  # keep rendering while a prior fill batch is reviewed, with bounded judge load


REPAIR_EPISODES = 2 * REPAIR_BATCH_SIZE


FILL_EPISODES = FILL_REVIEW_BACKLOG * REPAIR_BATCH_SIZE


STAGE_CAPACITY = {"repair_render": 24, "fill_render": 12, "repair_model": 12,
                  "fill_model": 12, "confirm": 6, "audit": 6}


def active_episodes(state: dict) -> set[int]:
    return {n for job in state["jobs"] if job["status"] in {"pending", "running", "waiting_plan"} for n in job.get("episodes", [])}


def stage_slots(job: dict) -> tuple[str, int] | None:
    if job["kind"] not in FLOWS:
        return None
    step = FLOWS[job["kind"]][job["step"]]
    if job["kind"] in {"audit", "confirm"}:
        return job["kind"], min(6, len(job["episodes"]))
    render = step.startswith("render")
    family = "repair" if job["kind"] == "recovery" else job["kind"]
    return f"{family}_{'render' if render else 'model'}", min(12 if render else 6, len(job["episodes"]))


def supplementary(job: dict) -> bool:
    return job["kind"] == "confirm" or (job["kind"] == "scan" and (job.get("source") == "flash" or str(job.get("source", "")).startswith("shared_")))


def second_pass_ready(state: dict, info: dict) -> bool:
    # Qwen is the full-book primary check. Flash and its confirmations keep
    # contributing findings while the residual pass runs; they are not a barrier.
    if any(j["status"] in {"pending", "running", "held"} and j["kind"] in {"scan", "drain"}
           and not supplementary(j) for j in state["jobs"]):
        return False
    if any(j["status"] in {"pending", "running"} and not supplementary(j) for j in state["jobs"]):
        return False
    return not any(r["unverified"] or
                   (r["bad"] and state["passes"].get(str(n), 0) == 0 and r.get('managed_clips',True)) or r["can_fill"]
                   for n, r in info.items() if not r.get("held") and not r.get("plan_blocked"))


def inspection_bucket(status: str, counts: dict) -> str:
    if counts["unchecked"]:
        return "not_fully_checked"
    if counts["failed"]:
        return "checked_with_errors"
    if status != "done":
        return "technical_pending"
    if counts["flash_pending"]:
        return "flash_confirmation"
    return "passed"
