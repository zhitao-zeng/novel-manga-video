#!/usr/bin/env python
"""Paired, text-only planning experiment using frozen novel context and source code.

No image/video generation. Each worker runs the normal planner in its own
output directory; model requests and usage are retained without HTTP headers.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "outputs/experiments/planner-outline-ab-20260914"
CONTEXT_FILES = (
    "story_bible.json", "profile.json", "bible_growth.json", "bible_aliases.json",
    "entity_index.json", "cast_index.json", "recap.json", "volumes.json",
    "visual_grammar.json", "chat_screen.json", "confusable_pairs.json",
)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def audit_outline_trace(run_dir: Path, mode: str, validate) -> dict:
    """Verify actual responses and forwarding, independently of a success label."""
    ready = None
    violations, outlines = [], []
    finals = 0
    for request_path in sorted((run_dir / "http").glob("*-request.json")):
        request = read(request_path)
        kind = request.get("response_format", {}).get("json_schema", {}).get("name")
        response_path = request_path.with_name(request_path.name.replace("-request", "-response"))
        response = read(response_path) if response_path.is_file() else {}
        choice = (response.get("choices") or [{}])[0]
        if kind == "chapter_outline":
            content = choice.get("message", {}).get("content") or ""
            payload = json.loads(request["messages"][1]["content"])
            errors = validate(content, mode, [s["segment_id"] for s in payload["segments"]])
            valid = choice.get("finish_reason") == "stop" and not errors
            ready = content if valid else None
            outlines.append({"request": request_path.name, "finish_reason": choice.get("finish_reason"),
                             "content_chars": len(content), "complete": valid, "errors": errors})
        elif kind == "thin_chapter_clips":
            finals += 1
            message = request["messages"][-1]["content"]
            forwarded = message.split("完整提纲：", 1)[1] if "完整提纲：" in message else None
            if ready is None or forwarded != ready:
                violations.append(f"{request_path.name}: incomplete or altered first-pass artifact")
            if choice.get("finish_reason") != "stop":
                violations.append(f"{request_path.name}: final generation did not finish")
            ready = None
    return {"valid": bool(finals) and not violations, "final_passes": finals, "outlines": outlines, "violations": violations}


def prepare(root: Path, cases: str, endpoints: list[str], repeats: int, repeat_cases: list[str] | None = None) -> None:
    if not root.is_relative_to(REPO / "outputs/experiments"):
        raise ValueError("experiment outputs must be under outputs/experiments")
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError("manifest already frozen; use run to resume")
    selected = []
    for item in cases.split(","):
        novel, chapter = item.rsplit(":", 1)
        chapter = int(chapter)
        directory = REPO / "outputs" / novel / f"{novel}_{chapter}"
        review = read(directory / "episode_review.json")
        verdicts = list(review.get("clips", {}).values())
        if not verdicts or any(v.get("severity") != "pass" for v in verdicts):
            raise ValueError(f"{item}: select a chapter with all existing clip verdicts pass")
        request = read(directory / "request_attempt_01.json")
        selected.append({"novel": novel, "chapter": chapter, "id": f"{novel}_{chapter}",
                         "title": request["chapter_title"], "selection_review_policy": review.get("policy"),
                         "source_chars": request["chapter_chars"],
                         "repeats": repeats if repeat_cases is None or f"{novel}_{chapter}" in repeat_cases else 1})
        write(root / "selection" / f"{novel}_{chapter}-review.json", review)

    for novel in dict.fromkeys(c["novel"] for c in selected):
        source_dir = REPO / "outputs" / novel
        frozen = root / "inputs" / novel
        frozen.mkdir(parents=True, exist_ok=True)
        for name in CONTEXT_FILES:
            if (source_dir / name).is_file():
                shutil.copyfile(source_dir / name, frozen / name)
        meta = read(source_dir / "novel.json")
        source = Path(meta["source"]).resolve()
        shutil.copyfile(source, frozen / "source.txt")
        for name in ("entities.json", "claims.json"):
            path = source_dir / "entity" / name
            if path.is_file():
                (frozen / "entity").mkdir(exist_ok=True)
                shutil.copyfile(path, frozen / "entity" / name)
        for case in (c for c in selected if c["novel"] == novel):
            chapter = case["chapter"]
            previous = source_dir / f"{novel}_{chapter - 1}" / "chapter_script.json"
            if previous.is_file():
                dest = frozen / previous.parent.name / previous.name
                dest.parent.mkdir(exist_ok=True)
                shutil.copyfile(previous, dest)
            mentions = source_dir / "entity/mentions" / f"ch_{chapter:04d}.json"
            if mentions.is_file():
                dest = frozen / "entity/mentions" / mentions.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mentions, dest)

    code = root / "code"
    for folder in ("scripts", "src", "configs/genres", "experiments"):
        shutil.copytree(REPO / folder, code / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write(manifest_path, {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "cases": selected, "repeats": repeats,
        "model": "Qwen3.8-27B-Project", "endpoints": endpoints, "clip_cap": 15,
        "outline_tokens": 4096, "require_complete_outline": True,
        "arms": {"coverage": "budget-fixed production first pass", "story": "causal/scene/expression first pass"},
        "first_seed": 20260914, "parallel": 2, "max_redo": 1, "max_tokens": 12000,
        "request_timeout_seconds": 360, "worker_timeout_seconds": 900, "run_timeout_seconds": 3600,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "comparison": "same frozen inputs, model, seeds, budgets; endpoint assignment swapped across repeats",
        "scope": "planning only; existing pass labels are selection criteria, not ground truth",
    })
    print(json.dumps({"frozen": str(root), "cases": selected, "planning_runs": sum(c["repeats"] for c in selected) * 2}, ensure_ascii=False), flush=True)


def worker(job_path: Path) -> int:
    job = read(job_path)
    root = Path(job["root"])
    run_dir = Path(job["run_dir"])
    manifest = read(root / "manifest.json")
    endpoint = job["endpoint"]
    os.environ["QWEN38_LOCAL_BASE_URL"] = endpoint
    os.environ["NOVEL_CLIP_SECONDS_MAX"] = str(manifest["clip_cap"])
    # Use exactly the captured implementation, even if production is edited during the probe.
    sys.path[:0] = [str(root / "code/scripts"), str(root / "code/src")]
    import httpx
    import novel_manga.planning.contracts as pc_contracts
    import novel_manga.application.planning.cli as plan_chapter

    requests = []
    original_send = httpx.Client.send

    def traced_send(client, request, **kwargs):
        number = len(requests) + 1
        entry = {"request": number, "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z")}
        requests.append(entry)
        body = json.loads(request.content) if request.content else {}
        write(run_dir / "http" / f"{number:03d}-request.json", body)  # no headers or credentials
        started = time.monotonic()
        try:
            response = original_send(client, request, **kwargs)
            entry["http_status"] = response.status_code
            data = response.json()
            entry["usage"] = data.get("usage", {})
            write(run_dir / "http" / f"{number:03d}-response.json", data)
            return response
        except Exception as error:
            entry["error"] = type(error).__name__
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)
            write(run_dir / "requests.json", requests)

    httpx.Client.send = traced_send
    novel = job["novel"]
    frozen = root / "inputs" / novel
    output = run_dir / "output" / novel
    shutil.copytree(frozen, output, ignore=shutil.ignore_patterns("source.txt"))
    sys.argv = ["plan_chapter_thin.py", str(frozen / "source.txt"), "--novel-id", novel,
                "--episode-index", str(job["chapter"]), "--bible", str(output / "story_bible.json"),
                "--output-root", str(output.parent), "--tier", "fast", "--base-url", endpoint,
                "--model", manifest["model"], "--outline-mode", job["arm"], "--seed", str(job["seed"]),
                "--outline-tokens", str(manifest.get("outline_tokens", 4096)),
                "--timeout", str(manifest["request_timeout_seconds"]), "--max-redo", str(manifest["max_redo"]),
                "--max-tokens", str(manifest["max_tokens"])]
    started = time.monotonic()
    result = {**job, "pid": os.getpid(), "model": manifest["model"], "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    try:
        result["exit_code"] = plan_chapter.main()
    except Exception as error:
        result.update(status="planning_exception", exit_code=1, error=f"{type(error).__name__}: {error}")
    result["wall_seconds"] = round(time.monotonic() - started, 3)
    episode = output / f"{novel}_{job['chapter']}"
    result["episode_dir"] = str(episode)
    report_path = episode / "chapter_script_report.json"
    if not report_path.is_file():
        report_path = episode / "planning_failed.json"
    if report_path.is_file():
        report = read(report_path)
        attempts = report.get("attempts", [])
        result.update(status=report["status"], full_drafts=len(attempts),
                      patches=sum(len(a.get("patches", [])) for a in attempts),
                      planner_seconds=report.get("elapsed_seconds"), metrics=report.get("metrics"),
                      warnings=report.get("warnings", []))
    result["http_requests"] = len(requests)
    result["usage"] = {k: sum((r.get("usage") or {}).get(k, 0) or 0 for r in requests)
                       for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    result["requests_with_usage"] = sum(bool(r.get("usage")) for r in requests)
    result["outline_trace"] = audit_outline_trace(run_dir, job["arm"], pc_contracts.validate_outline)
    if result.get("status") == "passed" and not result["outline_trace"]["valid"]:
        result.update(status="invalid_outline_trace", exit_code=3)
    result["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    write(run_dir / "result.json", result)
    return result["exit_code"]


def run(root: Path) -> None:
    manifest = read(root / "manifest.json")
    jobs = []
    for repeat in range(manifest["repeats"]):
        for index, case in enumerate(manifest["cases"]):
            if repeat >= case.get("repeats", manifest["repeats"]):
                continue
            for arm_index, arm in enumerate(("coverage", "story")):
                run_dir = root / "runs" / case["id"] / f"repeat_{repeat + 1:02d}" / arm
                jobs.append({**case, "root": str(root), "run_dir": str(run_dir), "arm": arm,
                             "repeat": repeat + 1, "seed": manifest["first_seed"] + repeat,
                             "endpoint": manifest["endpoints"][(arm_index + repeat + index) % len(manifest["endpoints"])]})
    deadline = time.monotonic() + manifest["run_timeout_seconds"]

    def launch(job):
        run_dir = Path(job["run_dir"])
        if (run_dir / "result.json").is_file():
            return read(run_dir / "result.json")
        if time.monotonic() >= deadline:
            return {**job, "status": "not_started_time_budget"}
        if (run_dir / "output").exists():
            return {**job, "status": "interrupted_run_requires_inspection"}
        write(run_dir / "job.json", job)
        command = [sys.executable, str(root / "code/experiments/benchmark_planner_outline.py"), "worker", str(run_dir / "job.json")]
        with (run_dir / "planner.log").open("w") as log:
            worker_started = time.monotonic()
            proc = subprocess.Popen(command, cwd=root / "code", stdout=log, stderr=subprocess.STDOUT)
            write(run_dir / "process.json", {"pid": proc.pid, "command": command, "log": str(run_dir / "planner.log")})
            try:
                proc.wait(timeout=min(manifest["worker_timeout_seconds"], max(1, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                write(run_dir / "result.json", {**job, "status": "worker_timeout", "exit_code": proc.returncode,
                                                "timeout_scope": "run_budget" if time.monotonic() >= deadline else "worker",
                                                "wall_seconds": round(time.monotonic() - worker_started, 3),
                                                "usage_complete": False})
        if not (run_dir / "result.json").is_file():
            write(run_dir / "result.json", {**job, "status": "worker_error", "exit_code": proc.returncode})
        result = read(run_dir / "result.json")
        print(json.dumps({"case": job["id"], "repeat": job["repeat"], "arm": job["arm"], "status": result.get("status"),
                          "seconds": result.get("wall_seconds"), "tokens": result.get("usage", {}).get("total_tokens")}, ensure_ascii=False), flush=True)
        return result

    results = []
    # Keep each pair contemporaneous; swap physical endpoints in the repeat.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index in range(0, len(jobs), 2):
            pair = list(pool.map(launch, jobs[index:index + 2]))
            results.extend(pair)
            if manifest.get("require_complete_outline") and any(not r.get("outline_trace", {}).get("valid") for r in pair):
                for job in jobs[index + 2:]:
                    skipped = {**job, "status": "not_started_invalid_outline", "reason": "stop after a failed protocol-completeness gate"}
                    write(Path(job["run_dir"]) / "result.json", skipped)
                    results.append(skipped)
                write(root / "planning-results.json", results)
                print("stopped: incomplete output or forwarding; no further comparison samples", flush=True)
                return
            write(root / "planning-results.json", results)
    print(json.dumps({"finished": len(results), "status": "planning_complete", "root": str(root)}), flush=True)


def pack_completed(root: Path) -> list[dict]:
    """Measure requested video workload with the frozen deterministic packer."""
    manifest = read(root / "manifest.json")
    rows = []
    for path in sorted((root / "runs").glob("*/repeat_*/*/result.json")):
        result = read(path)
        if result.get("status") != "passed":
            continue
        episode = Path(result["episode_dir"])
        if not episode.is_relative_to(root / "runs"):
            raise ValueError("packing is restricted to this experiment's outputs")
        plan_path = episode / "clip_plan.json"
        row = {"case": result["id"], "arm": result["arm"], "repeat": result["repeat"]}
        if not plan_path.is_file():
            env = {**os.environ, "NOVEL_CLIP_SECONDS_MAX": str(manifest["clip_cap"]),
                   "PYTHONPATH": str(root / "code/src") + os.pathsep + str(root / "code/scripts")}
            command = [sys.executable, str(root / "code/scripts/build_clip_plan_thin.py"), "--episode-dir", str(episode),
                       "--bible", str(episode.parent / "story_bible.json"), "--tier", "fast"]
            with (path.parent / "pack.log").open("w") as log:
                packed = subprocess.run(command, cwd=root / "code", env=env, stdout=log, stderr=subprocess.STDOUT, timeout=30)
            if packed.returncode:
                rows.append({**row, "pack_error": packed.returncode})
                continue
        plan = read(plan_path)
        videos = [clip for clip in plan["clips"] if clip["kind"] == "video"]
        rows.append({**row, "video_clips": len(videos), "requested_video_seconds": sum(c["request_seconds"] for c in videos),
                     "estimated_seconds": plan["totals"]["estimated_seconds"]})
    write(root / "pack-results.json", rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    prepare_parser.add_argument("--cases", required=True)
    prepare_parser.add_argument("--endpoints", default="http://127.0.0.1:18122/v1,http://127.0.0.1:18124/v1")
    prepare_parser.add_argument("--repeats", type=int, default=2)
    prepare_parser.add_argument("--repeat-cases", help="only repeat these case IDs; all others run once")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    pack_parser = commands.add_parser("pack")
    pack_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    worker_parser = commands.add_parser("worker")
    worker_parser.add_argument("job", type=Path)
    args = parser.parse_args()
    if args.command == "worker":
        return worker(args.job.resolve())
    if args.command == "prepare":
        prepare(args.root.resolve(), args.cases, args.endpoints.split(","), args.repeats,
                args.repeat_cases.split(",") if args.repeat_cases else None)
    elif args.command == "pack":
        print(json.dumps(pack_completed(args.root.resolve()), ensure_ascii=False, indent=2))
    else:
        run(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
