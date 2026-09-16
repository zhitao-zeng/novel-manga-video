#!/usr/bin/env python3
"""Bounded paired probe of history + optional reframing on repeated visual failures.

Freeze existing takes first; no production writes or new cards. Each arm gets one
fresh, fixed-seed H3 sample, including a no-op rewrite. This isolates the candidate
request's success probability, not the full scheduler's cache/no-op behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

from benchmark_repair_cause import ROOT, ROOT_FILES, EPISODE_FILES, copy_if_present, clone_case, render_arm, evaluate_arm, evaluation_passed
from clip_readiness import plan_issues
from manage_repair_thin import active_episodes
from novel_manga.config import Settings
from novel_manga.models import StoryBible
from novel_manga.util import atomic_write_json
from repair_flow_thin import repair_episode
from repair_history import read, ERROR_FIELDS
from render_clips_thin import ThinMediaRunner
from thin_batch import load_dotenv
from thin_profile import load_profile, h3_prompt_outdated, plan_fingerprint
from thin_runs import episode_status
from verify_clips_thin import Verifier
import thin_review as tr


def make_runner(novel, episode, *, cache_only=False):
    settings = replace(Settings.from_env(provider="phanrouter", output_root=novel.parent, admission_mode="preview"), local_h3_base_url="pool")
    return ThinMediaRunner(novel_dir=novel, episode_dir=episode, settings=settings,
                          bible=StoryBible.model_validate_json((novel / "story_bible.json").read_text()), workers=1,
                          max_attempts=1, profile=load_profile(novel, tier="fast"), cache_only=cache_only)


def freeze(novel: Path, output: Path, count=8):
    if (output / "manifest.json").exists():
        return read(output / "manifest.json")
    frozen = output / "frozen" / novel.name
    busy = active_episodes(read(novel / "repair_manager/state.json", {"jobs": []}))
    pool = []
    for path in novel.glob(f"{novel.name}_*/episode_review.json"):
        ep = path.parent
        number = int(ep.name.rsplit("_", 1)[1])
        if number in busy or episode_status(ep, True) not in {"done", "done_with_warnings"}:
            continue
        plan, script, review = read(ep / "clip_plan.json", {}), read(ep / "chapter_script.json", {}), read(path)
        blocked = plan_issues(plan, script)
        for clip in plan.get("clips", []):
            cid = clip["clip_id"]
            row = review.get("clips", {}).get(cid) or {}
            if cid in blocked or row.get("story_ok") is not False or not row.get("verify") or row.get("technical"):
                continue
            if not 4 <= clip.get("request_seconds", 0) <= 15 or h3_prompt_outdated(clip, str(read(ep / "review_feedback.json", {}).get(cid, ""))):
                continue
            if any(ref.get("path", "").endswith("expressions.jpeg") for ref in clip.get("references", [])):
                continue
            attempts = list((ep / "work/clips" / cid).glob("attempt_*/clip.mp4"))
            if len(attempts) >= 2:
                pool.append((number, cid, plan, row, attempts))
    random.Random(20260915).shuffle(pool)
    for name in ROOT_FILES:
        copy_if_present(novel / name, frozen / name)
    for path in (novel / "entity").glob("*.json"):
        copy_if_present(path, frozen / "entity" / path.name)
    for name in ["manifest.json", "phases.json"]:
        copy_if_present(novel / "series_assets" / name, frozen / "series_assets" / name)
    cases, seen = [], set()
    for number, cid, plan, row, attempts in pool:
        if number in seen:
            continue
        ep = novel / f"{novel.name}_{number}"
        current = Path(row.get("video") or "")
        if not current.is_file() or tr.take_identity(current) != row.get("take"):
            continue
        runner = make_runner(novel, ep, cache_only=True)
        clip = next(c for c in runner.clip_plan["clips"] if c["clip_id"] == cid)
        # Both previous and current takes must be of the same current request
        # (apart from its existing retry suffix) and the same current reference cards.
        matching = [p for p in attempts if runner.cached_take(clip, int(p.parent.name.split("_")[-1]))]
        prior = next((p for p in sorted(matching) if p != current), None)
        if current not in matching or prior is None:
            continue
        dest = frozen / ep.name
        for name in [*EPISODE_FILES, "clip_plan.json"]:
            copy_if_present(ep / name, dest / name)
        for folder in ["mentions", "relations"]:
            copy_if_present(novel / "entity" / folder / f"ch_{number:04d}.json", frozen / "entity" / folder / f"ch_{number:04d}.json")
        for label, video in (("failed", current), ("previous_failed", prior)):
            copy_if_present(video, dest / f"{label}.mp4")
            copy_if_present(video.parent / "request.json", dest / f"{label}.request.json")
        original_review = read(dest / "episode_review.json")
        original_review["clips"] = {cid: {**row, "video": str(dest / "failed.mp4"), "take": tr.take_identity(dest / "failed.mp4")}}
        original_review["feedback"] = {cid: row.get("feedback") or row.get("story_issue") or "按原文修正画面"}
        atomic_write_json(dest / "episode_review.json", original_review)
        for c in plan["clips"]:
            for ref in c.get("references", []):
                copy_if_present(novel / ref["path"], frozen / ref["path"])
        cases.append({"id": f"{ep.name}_{cid}", "episode": number, "clip_id": cid, "seed": 20260915 + len(cases),
                      "original_seconds": clip["request_seconds"], "source_video": str(current), "previous_video": str(prior)})
        seen.add(number)
        if len(cases) >= count:
            break
    # Repairs can choose other already-drawn actors. Freeze a single shared set
    # of primary cards for all arms, never per-arm copies or new image generation.
    for path in (novel / "series_assets/characters").glob("*/turnaround.jpeg"):
        target = frozen / path.relative_to(novel)
        if not target.exists(): copy_if_present(path, target)
    for path in (novel / "series_assets/locations").glob("*/establishing.jpeg"):
        target = frozen / path.relative_to(novel)
        if not target.exists(): copy_if_present(path, target)
    for path in (novel / "series_assets/voices").glob("*"):
        if path.is_file(): copy_if_present(path, frozen / path.relative_to(novel))
    manifest = {"created_at": time.strftime("%F %T"), "frozen_novel": str(frozen), "cases": cases,
                "contract": "two existing matching-request takes, reverify both; at most four confirmed repeated failures and eight fresh video submissions"}
    atomic_write_json(output / "manifest.json", manifest)
    return manifest


def confirm(output: Path):
    if (output / "selection.json").exists(): return read(output / "selection.json")
    manifest = read(output / "manifest.json")
    frozen = Path(manifest["frozen_novel"])
    selected, excluded = [], []
    for case in manifest["cases"]:
        pair = {}
        for label in ["previous_failed", "failed"]:
            dest = output / "confirm" / case["id"] / label
            novel, ep = clone_case(frozen, dest, case)
            review = read(ep / "episode_review.json")
            video = frozen / ep.name / f"{label}.mp4"
            review["clips"][case["clip_id"]]["video"] = str(video)
            atomic_write_json(ep / "episode_review.json", review)
            verifier = Verifier(novel, dest / "raw.jsonl", "blind_local", 1)
            verdict = verifier.verify((case["episode"], case["clip_id"], "", "pilot"))
            atomic_write_json(dest / "verdict.json", verdict)
            pair[label] = verdict
        common = [k for k in ERROR_FIELDS if all(row.get(k) for row in pair.values())]
        if not common or any("error" in row or evaluation_passed(row) for row in pair.values()):
            excluded.append({"case": case["id"], "why": "two matching-request takes did not confirm the same obvious error"})
            continue
        observations = [{"video": row["video"], "take": row["take"], "policy": tr.POLICY,
                         "verdict": "obvious", "errors": [k for k in ERROR_FIELDS if row.get(k)],
                         "evidence": row.get("evidence", ""), "instruction": row.get("instruction", "")} for row in pair.values()]
        clip = next(c for c in read(frozen / f"{frozen.name}_{case['episode']}" / "clip_plan.json")["clips"] if c["clip_id"] == case["clip_id"])
        history = {"observations": {case["clip_id"]: observations}, "trials": []}
        atomic_write_json(output / "histories" / f"{case['id']}.json", history)
        selected.append({**case, "repeated_errors": common})
        print("confirmed repeated error", case["id"], common, flush=True)
        if len(selected) == 4: break
    result = {"selected": selected, "excluded": excluded}
    atomic_write_json(output / "selection.json", result)
    return result


def prepare(output: Path, case: dict, arm: str):
    import build_h3_prompts as h3
    frozen = Path(read(output / "manifest.json")["frozen_novel"])
    dest = output / "runs" / case["id"] / arm
    if (dest / "prepared.json").exists(): return read(dest / "prepared.json")
    novel, ep = clone_case(frozen, dest, case)
    if arm == "history":
        copy_if_present(output / "histories" / f"{case['id']}.json", ep / "repair_history/history.json")
    before = next(c for c in read(ep / "clip_plan.json")["clips"] if c["clip_id"] == case["clip_id"])
    started = time.monotonic()
    repair = repair_episode(novel, case["episode"], True, use_history=arm == "history", reframe=arm == "history")
    plan = read(ep / "clip_plan.json")
    clip = next(c for c in plan["clips"] if c["clip_id"] == case["clip_id"])
    if clip.get("lines") != before.get("lines") or clip.get("segment_ids") != before.get("segment_ids"):
        raise ValueError("candidate changed the frozen dialogue or source coverage")
    if float(clip.get("seconds_estimate") or 0) > case["original_seconds"]:
        raise ValueError("candidate no longer fits the shared duration")
    clip["request_seconds"] = case["original_seconds"]
    clip.pop("prompt_h3_of", None)
    h3.convert(clip, note=str(read(ep / "review_feedback.json", {}).get(case["clip_id"], "")))
    if h3_prompt_outdated(clip, str(read(ep / "review_feedback.json", {}).get(case["clip_id"], ""))):
        raise ValueError("incomplete English request")
    for ref in clip.get("references", []):
        if ref["path"].endswith("expressions.jpeg") or not (novel / ref["path"]).is_file():
            raise ValueError("candidate needs an unavailable frozen primary asset")
    atomic_write_json(ep / "full_clip_plan.json", plan)
    atomic_write_json(ep / "clip_plan.json", {**plan, "clips": [clip]})
    result = {"case": case, "arm": arm, "novel": str(novel), "episode": str(ep),
              "prepare_seconds": round(time.monotonic() - started, 3), "request_seconds": case["original_seconds"], "repair": repair}
    atomic_write_json(dest / "prepared.json", result)
    return result


def run(output: Path):
    selection = confirm(output)
    if not selection["selected"]: raise RuntimeError("no confirmed repeated-error cases; do not generate")
    results = []
    started = time.monotonic()
    for case in selection["selected"]:
        if time.monotonic() - started > 3600: raise TimeoutError("pilot reached its one-hour budget")
        with ThreadPoolExecutor(max_workers=2) as pool:
            prepared = list(pool.map(lambda arm: prepare(output, case, arm), ["baseline", "history"]))
            videos = list(pool.map(lambda item: render_arm(output, item), prepared))
        for video in videos:
            verdict = evaluate_arm(output, video)
            runner = make_runner(Path(video["novel"]), Path(video["episode"]), cache_only=True)
            speech = runner.analyse_clip(runner.clip_plan["clips"][0], Path(video["video"]))
            row = {**video, "verdict": verdict, "speech": speech, "passed": evaluation_passed(verdict) and speech["passed"]}
            results.append(row)
            atomic_write_json(output / "results.json", results)
            print(case["id"], video["arm"], "PASS" if row["passed"] else "FAIL", verdict.get("evidence", ""), flush=True)
    summary = {arm: {"cases": sum(r["arm"] == arm for r in results), "passed": sum(r["passed"] for r in results if r["arm"] == arm),
                     "requested_video_seconds": sum(r["request_seconds"] for r in results if r["arm"] == arm),
                     "prepare_seconds": round(sum(r["prepare_seconds"] for r in results if r["arm"] == arm), 3)} for arm in ["baseline", "history"]}
    atomic_write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run"])
    parser.add_argument("--novel-dir", type=Path, default=ROOT / "outputs/wuyue")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    state = read(args.novel_dir / "repair_manager/state.json", {})
    if state.get("legacy_dir"):
        os.environ["NOVEL_INFLIGHT_DIR"] = str(Path(state["legacy_dir"]).parent / "inflight/h3pool")
        os.environ["NOVEL_INFLIGHT_POOL"] = "h3pool"
    if args.action == "freeze": print(json.dumps(freeze(args.novel_dir.resolve(), args.output.resolve()), ensure_ascii=False))
    else: run(args.output.resolve())


if __name__ == "__main__": main()
