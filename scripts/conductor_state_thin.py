"""conductor_state_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import json
import time
import urllib.request
import conductor_common_thin as conductor_common
import thin_runs as thin_runs

def chapter(conductor, n: int) -> dict:
    d = conductor.episode_dir(n)
    state = {"n": n, "done": False, "blocked": False, "planned": (d / "clip_plan.json").is_file(), "runs": 0, "mode": None,
             "unreviewed": False}
    if not state["planned"]:
        return state
    state["mode"], status, retakeable = reading(conductor, d)
    # The count thin_batch keeps, per plan and set of corrections.  Comparing the plan's mtime alone with the
    # pair it writes read 0 runs everywhere and kept given-up episodes renderable.
    state["runs"] = thin_runs.render_runs(d)
    # One reading with thin_batch (thin_runs.episode_status).  An episode with a video file is not done when its
    # plan, a correction or an English prompt changed since: counted as done, a range already rendered was never
    # scheduled again.  A preview (done_with_warnings) is not done either: a free lane takes it back while the
    # speech gate's failures can be retaken and runs remain; otherwise it waits for a person - "blocked", like an
    # episode that used up its runs - and is neither done nor work for a lane.
    if status == "done":
        state["done"] = True
    elif status == "done_with_warnings":
        state["blocked"] = not (conductor.free and retakeable and state["runs"] < thin_runs.RENDER_RUNS_PER_PLAN)
    elif state["runs"] >= thin_runs.RENDER_RUNS_PER_PLAN:
        state["blocked"] = True
    mp4 = d / f"{conductor.novel_id}_{n}.mp4"
    if status in {"done", "done_with_warnings"} and (state["done"] or state["blocked"]):
        final = mp4.stat().st_mtime
        review = d / "episode_review.json"
        seen, batches = conductor.review_tries.get(n, (final, 0))
        state["unreviewed"] = (not (seen == final and batches >= conductor_common.REVIEW_BATCH_TRIES)
                               and (not review.is_file() or review.stat().st_mtime < final or review_incomplete(conductor, review)))
    return state


def reading(conductor, d: Path) -> tuple[int | None, str, bool]:
    """(clip length of the plan, thin_runs.episode_status, whether the final has gate failures to retake), kept
    until one of the files it is read from changes: every tick looks at every episode."""
    key = tuple(_mtime(d / name) for name in ("clip_plan.json", "thin_media_report.json", "review_feedback.json", f"{d.name}.mp4"))
    hit = conductor._readings.get(str(d))
    if hit and hit[0] == key:
        return hit[1]
    try:
        policy = json.loads((d / "clip_plan.json").read_text(encoding="utf-8")).get("policy", "")
        mode = 15 if "-15s" in policy else 30
    except (OSError, ValueError):
        mode = 30
    try:
        status = thin_runs.episode_status(d, conductor.free)
    except (OSError, ValueError, KeyError):
        status = "pending"
    value = (mode, status, bool(thin_runs.gate_failures(d)))
    conductor._readings[str(d)] = (key, value)
    return value


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def settled(conductor, n: int) -> bool:
    """Planned, or left out on purpose (planning_skipped.json: too short to be an episode)."""
    d = conductor.episode_dir(n)
    return (d / "clip_plan.json").is_file() or (d / "planning_skipped.json").is_file()


def summary(conductor, path: Path, kind: str, summarize):
    """summarize(the file's JSON), kept until the file changes: every tick looks at every episode."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = conductor._summaries.get((str(path), kind))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        value = summarize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, AttributeError, TypeError):
        value = None
    conductor._summaries[(str(path), kind)] = (mtime, value)
    return value


def review_incomplete(conductor, review: Path) -> bool:
    """A review in which the judge failed on some clips (severity review_error) has not reviewed them: it
    goes back in the queue, for a few rounds.  Only the review file's age used to count, and 28 of 雾月's
    and 3 of 诸天's episodes sat as reviewed with those clips unjudged and unflagged (2026-09-11)."""
    errors = summary(conductor, review, "review_errors", lambda data: (
        sum(1 for c in (data.get("clips") or {}).values() if c.get("severity") == "review_error"),
        int(data.get("error_rounds", 0)), data.get("policy")))
    return errors is None or errors[2] != thin_runs.REVIEW_POLICY or (errors[0] > 0 and errors[1] < conductor_common.REVIEW_ERROR_ROUNDS)


def range_stats(conductor, r: dict) -> dict:
    chapters = [chapter(conductor, n) for n in range(r["a"], r["b"] + 1)]
    renderable = [c["n"] for c in chapters if c["planned"] and not c["done"] and not c["blocked"]
                  and c["runs"] < thin_runs.RENDER_RUNS_PER_PLAN and c["mode"] == int(r.get("plan_mode", 30))]
    return {"total": len(chapters), "planned": sum(c["planned"] for c in chapters), "done": sum(c["done"] for c in chapters),
            "blocked": [c["n"] for c in chapters if c["blocked"]], "renderable": renderable,
            "pending": [c["n"] for c in chapters if not c["done"]],
            "unreviewed": [c["n"] for c in chapters if c["unreviewed"]]}


def recent_lines(conductor, chapters, pattern: str, seconds: int) -> int:
    """Lines matching `pattern` in the chapters' render logs within the last `seconds` (same day)."""
    cutoff = (datetime.now() - timedelta(seconds=seconds)).strftime("%H:%M:%S")
    count = 0
    for n in chapters:
        path = conductor.episode_dir(n) / "render.log"
        if not path.is_file() or time.time() - path.stat().st_mtime > seconds + 60:
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
                if len(line) > 9 and line[0] == "[" and line[1:9] >= cutoff and pattern in line:
                    count += 1
        except OSError:
            pass
    return count


def qwen_waiting(conductor) -> int:
    total = 0
    for url in conductor.cfg["qwen"]["urls"]:
        base = url.rsplit("/v1", 1)[0]
        try:
            with urllib.request.urlopen(base + "/metrics", timeout=3) as response:
                for line in response.read().decode("utf-8", "replace").splitlines():
                    if line.startswith("vllm:num_requests_waiting"):
                        total += int(float(line.rsplit(" ", 1)[-1]))
        except Exception:  # noqa: BLE001 - a dead endpoint counts as idle
            pass
    return total


def read_upto(conductor) -> int:
    try:
        return max(int(k) for k in json.loads((conductor.novel_dir / "bible_growth.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return 0
