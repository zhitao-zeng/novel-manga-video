#!/usr/bin/env python3
"""Freeze actual failed clips and compare cause-aware repair in isolated outputs.

First diagnose at most 16 clips. Generation is a separately invoked bounded pilot;
the production queue, episode files and default repair policy are never modified.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import shutil
import sys
import threading
import time
import subprocess
from dataclasses import replace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
from novel_manga.util import atomic_write_json
from diagnose_clip_repair import clip_context, diagnose, diagnose_numbered
from review_store_thin import current_takes
from novel_manga.util import read_json as read
from novel_manga.util import load_dotenv
from thin_runs import episode_status

PILOT_CASES = ["wuyue_667_clip_07", "wuyue_1312_clip_01", "wuyue_1104_clip_02", "wuyue_710_clip_03", "wuyue_1563_clip_01"]
PILOT_ENDPOINT = "http://172.28.4.52:30014"

ROOT_FILES = ["story_bible.json", "bible_aliases.json", "entity_index.json", "cast_index.json", "profile.json",
              "visual_grammar.json", "chat_screen.json", "novel.json", "confusable_pairs.json"]
EPISODE_FILES = ["chapter_script.json", "segments.json", "episode_review.json", "review_feedback.json", "clip_overrides.json"]


def copy_if_present(source: Path, target: Path):
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def freeze(novel: Path, output: Path, count: int = 16, seed: int = 20260914):
    if (output / "manifest.json").exists():
        return read(output / "manifest.json", {})
    state = read(novel / "repair_manager/state.json", {})
    busy = {n for j in state.get("jobs", []) if j.get("status") in {"pending", "running"} for n in j["episodes"]}
    candidates = []
    for directory in sorted(novel.glob(f"{novel.name}_*")):
        suffix = directory.name.rsplit("_", 1)[-1]
        if not suffix.isdigit() or int(suffix) in busy or state.get("passes", {}).get(suffix, 0):
            continue
        plan = read(directory / "clip_plan.json", {})
        review = read(directory / "episode_review.json", {})
        if episode_status(directory, True) not in {"done", "done_with_warnings"}:
            continue
        takes = current_takes(directory, plan, review)
        for clip in plan.get("clips", []):
            cid = clip.get("clip_id"); row = review.get("clips", {}).get(cid) or {}
            take = takes.get(cid)
            if (clip.get("kind") == "video" and 4 <= clip.get("request_seconds", 0) <= 15 and take
                    and row.get("video") == take["video"] and row.get("take") == take["take"]
                    and row.get("verify", {}).get("verdict") == "obvious" and row.get("tier") == "must_fix"
                    and not row.get("technical") and clip.get("shot_indexes")
                    and not any(r.get("path", "").endswith("/expressions.jpeg") for r in clip.get("references", []))):
                candidates.append((int(suffix), cid))
    random.Random(seed).shuffle(candidates)
    chosen = []; seen = set()
    for ep, cid in candidates:
        if ep not in seen:
            seen.add(ep);chosen.append((ep, cid))
        if len(chosen) == count:break
    frozen = output / "frozen" / novel.name
    for name in ROOT_FILES:copy_if_present(novel / name, frozen / name)
    for path in (novel / "entity").glob("*.json"):copy_if_present(path, frozen / "entity" / path.name)
    for name in ["manifest.json", "phases.json"]:copy_if_present(novel / "series_assets" / name, frozen / "series_assets" / name)
    for path in (novel / "series_assets" / "voices").glob("*"):
        if path.is_file():copy_if_present(path, frozen / "series_assets/voices" / path.name)
    cases = []
    for index, (ep, cid) in enumerate(chosen):
        source = novel / f"{novel.name}_{ep}"; dest = frozen / source.name
        plan = read(source / "clip_plan.json", {}); clip = next(c for c in plan["clips"] if c["clip_id"] == cid)
        for name in EPISODE_FILES:copy_if_present(source / name, dest / name)
        one_plan = {**plan, "clips": [clip]}
        atomic_write_json(dest / "clip_plan.json", one_plan)
        for folder in ["mentions", "relations"]:
            copy_if_present(novel / "entity" / folder / f"ch_{ep:04d}.json", frozen / "entity" / folder / f"ch_{ep:04d}.json")
        review = read(dest / "episode_review.json", {})
        video = Path(review["clips"][cid]["video"])
        copy_if_present(video, dest / "failed.mp4")
        for ref in clip.get("references", []):
            copy_if_present(novel / ref["path"], frozen / ref["path"])
        case = {"id": f"{novel.name}_{ep}_{cid}", "episode": ep, "clip_id": cid, "seed": seed + index,
                "source_video": str(video), "take": review["clips"][cid]["take"], "original_seconds": clip["request_seconds"]}
        cases.append(case)
    # All actors that the baseline repair is allowed to add must use frozen cards too.
    # Copy primary cards once into the shared snapshot; no expression cards and no image generation.
    for path in (novel / "series_assets/characters").glob("*/turnaround.jpeg"):
        copy_if_present(path, frozen / path.relative_to(novel))
    for path in (novel / "series_assets/locations").glob("*/establishing.jpeg"):
        copy_if_present(path, frozen / path.relative_to(novel))
    manifest = {"created_at": time.strftime("%F %T"), "source_novel": str(novel), "frozen_novel": str(frozen),
                "selection_seed": seed, "eligible_failed_clips": len(candidates), "cases": cases,
                "contract": "current precise failures; unclaimed episodes without a completed repair cycle; one clip per chapter; single main cards; max 15 seconds"}
    atomic_write_json(output / "manifest.json", manifest)
    return manifest


def classify(output: Path, numbered: bool = False):
    manifest = read(output / "manifest.json", {}); novel = Path(manifest["frozen_novel"])
    def one(case):
        folder = "diagnoses_v2" if numbered else "diagnoses"
        path = output / folder / f"{case['id']}.json"
        if path.exists():return read(path, {})
        context = clip_context(novel, case["episode"], case["clip_id"])
        if numbered:
            original = Path(manifest["source_novel"]) / f"{novel.name}_{case['episode']}"
            live_plan = read(original / "clip_plan.json", {})
            live_clip = next((c for c in live_plan.get("clips", []) if c.get("clip_id") == case["clip_id"]), None)
            frozen_clip = read(novel / original.name / "clip_plan.json", {})["clips"][0]
            valid_input = live_clip == frozen_clip and episode_status(original, True) in {"done", "done_with_warnings"}
            answer = diagnose_numbered(context) if valid_input else {"cause": "uncertain", "reason": "snapshot is stale or its live input changed before eligibility check", "elapsed_seconds": 0}
        else:
            answer = diagnose(context)
        result = {"case": case, "context": context, "diagnosis": answer}
        atomic_write_json(path, result)
        print(case["id"],answer["cause"],answer.get("reason", "")[:100],flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, manifest["cases"]))
    atomic_write_json(output / ("diagnoses_v2.json" if numbered else "diagnoses.json"), results)
    return results


def clone_case(frozen: Path, destination: Path, case: dict) -> tuple[Path, Path]:
    novel = destination / frozen.name
    novel.mkdir(parents=True, exist_ok=True)
    for name in ROOT_FILES:
        copy_if_present(frozen / name, novel / name)
    # Immutable snapshot only, never a link into the live production novel.
    for name in ["series_assets", "entity"]:
        link = novel / name
        if not link.exists():link.symlink_to(frozen / name, target_is_directory=True)
    episode = novel / f"{frozen.name}_{case['episode']}"
    source = frozen / episode.name
    for name in [*EPISODE_FILES, "clip_plan.json"]:copy_if_present(source / name, episode / name)
    return novel, episode


def prepare_arm(output: Path, case: dict, arm: str) -> dict:
    from repair_flow_thin import repair_episode
    import build_h3_prompts as h3
    manifest = read(output / "manifest.json", {})
    frozen = Path(manifest["frozen_novel"])
    destination = output / "runs" / case["id"] / arm
    saved = read(destination / "prepared.json")
    if saved:return saved
    novel, episode = clone_case(frozen, destination, case)
    start = time.monotonic()
    repair = None
    if arm == "rewrite":
        repair = repair_episode(novel, case["episode"], True)
        if case["clip_id"] not in repair.get("changed", []):
            raise RuntimeError(f"baseline could not rebuild {case['id']}: {repair}")
    else:
        review = read(episode / "episode_review.json", {})
        note = review.get("feedback", {}).get(case["clip_id"]) or review["clips"][case["clip_id"]].get("feedback")
        if not note:raise RuntimeError(f"no concrete correction for {case['id']}")
        notes = read(episode / "review_feedback.json", {})
        notes[case["clip_id"]] = note
        atomic_write_json(episode / "review_feedback.json", notes)
    plan = read(episode / "clip_plan.json", {});clip = plan["clips"][0]
    notes = read(episode / "review_feedback.json", {})
    h3.convert(clip, note=notes.get(case["clip_id"], ""))
    from thin_profile import h3_prompt_outdated
    if not clip.get("prompt_h3") or h3_prompt_outdated(clip, notes.get(case["clip_id"], "")):
        raise RuntimeError(f"incomplete H3 prompt for {case['id']}/{arm}")
    if not 4 <= clip["request_seconds"] <= 15:raise RuntimeError('clip exceeds the shared 15-second cap')
    for ref in clip.get("references", []):
        if not (novel / ref["path"]).is_file():raise RuntimeError(f"required frozen asset missing: {ref['path']}")
        if ref["path"].endswith("/expressions.jpeg"):raise RuntimeError('expression sheets are not part of this experiment')
    atomic_write_json(episode / "clip_plan.json", plan)
    result = {"case": case, "arm": arm, "novel": str(novel), "episode": str(episode),
              "prepare_seconds": round(time.monotonic() - start, 3), "request_seconds": clip["request_seconds"],
              "repair": repair, "reference_paths": [r["path"] for r in clip.get("references", [])]}
    atomic_write_json(destination / "prepared.json", result)
    return result


def controlled_submit(original, seed: int, receipt: Path):
    def submit(payload, base):
        if receipt.exists():
            raise RuntimeError('pilot permits one submission per arm; do not silently rerun a lost task')
        payload["seed"] = seed
        atomic_write_json(receipt, {"endpoint": base, "seed": seed, "seconds": payload["seconds"],
                                    "started_at": time.strftime('%F %T')})
        return original(payload, base)
    return submit


def render_arm(output: Path, prepared: dict) -> dict:
    from novel_manga.config import Settings
    from novel_manga.models import StoryBible
    from novel_manga.providers.h3_pool import H3Pool
    from render_flow_thin import ThinMediaRunner
    from thin_profile import load_profile
    case = prepared["case"]; novel = Path(prepared["novel"]);episode = Path(prepared["episode"])
    result_path = episode.parent.parent / "rendered.json"
    if result_path.exists():return read(result_path)
    settings = Settings.from_env(provider="phanrouter", output_root=novel.parent, admission_mode="preview")
    settings = replace(settings, local_h3_base_url="pool", video_model="minimax-h3-ref2va-turbo", poll_timeout=600)
    bible = StoryBible.model_validate_json((novel / "story_bible.json").read_text())
    runner = ThinMediaRunner(novel_dir=novel, episode_dir=episode, settings=settings, bible=bible, workers=1,
                             max_attempts=1, profile=load_profile(novel, tier="fast"), inflight=24, moderation_repair=False)
    class PinnedPool(H3Pool):
        def acquire(self, timeout=None):
            member = self.lookup(PILOT_ENDPOINT)
            return member, self.hold(PILOT_ENDPOINT, timeout=timeout)
    runner.context.provider.local.pool = PinnedPool()
    runner.context.provider.local._submit = controlled_submit(runner.context.provider.local._submit, case['seed'], episode.parent.parent/'submission.json')
    # Calling only generate_clip avoids new asset creation, whole-episode assembly,
    # speech-gate retries and any write to the production review/cache.
    started = time.monotonic()
    video = runner.generate_clip(runner.context.clip_plan["clips"][0], 1)
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True,capture_output=True)
    task = read(video.with_suffix(video.suffix + '.task.json'), {})
    if task.get('seed') != case['seed'] or task.get('endpoint') != PILOT_ENDPOINT + '/v1/videos':
        raise RuntimeError('actual seed/backend differs from the controlled request')
    result = {**prepared, "video": str(video), "render_seconds": round(time.monotonic() - started, 3),
              "seed": task["seed"], "endpoint": task["endpoint"], "task_id": task.get('task_id'), "submissions": 1}
    atomic_write_json(result_path, result)
    return result


def evaluate_arm(output: Path, rendered: dict) -> dict:
    from verify_clips_thin import Verifier
    manifest = read(output / 'manifest.json', {}); frozen = Path(manifest['frozen_novel'])
    case = rendered['case'];arm = rendered['arm']
    dest = output / 'evaluation' / case['id'] / arm
    path = dest / 'verdict.json'
    if path.exists():return read(path)
    novel, episode = clone_case(frozen, dest, case)
    # Both arms are judged against the SAME frozen source, ledger and original
    # intended scene, never against the rewrite's self-modified expectations.
    review = read(episode/'episode_review.json', {})
    review['clips'] = {case['clip_id']: {'video': rendered['video']}}
    atomic_write_json(episode/'episode_review.json', review)
    verifier = Verifier(novel, dest/'raw.jsonl', 'blind_local', 1)
    verdict = verifier.verify((case['episode'], case['clip_id'], '', 'pilot'))
    if 'error' in verdict:raise RuntimeError(verdict['error'])
    atomic_write_json(path, verdict)
    return verdict


def evaluation_passed(verdict: dict) -> bool:
    return 'error' not in verdict and verdict.get('verdict') in {'fine','subtle'} and not any(verdict.get(k) for k in
        ['same_person_twice','species_or_gender_wrong','action_by_wrong_person','actor_missing','lead_face_swapped'])


def verify_originals(output: Path):
    manifest=read(output/'manifest.json',{});cases={c['id']:c for c in manifest['cases']};frozen=Path(manifest['frozen_novel'])
    results=[]
    for cid in PILOT_CASES:
        case=cases[cid]
        result=evaluate_arm(output,{'case':case,'arm':'original','video':str(frozen/f"wuyue_{case['episode']}"/'failed.mp4')})
        results.append({'case':case,'verdict':result,'passed':evaluation_passed(result)})
        atomic_write_json(output/'original_verdicts.json',results)
        print(cid,'original','PASS' if results[-1]['passed'] else 'FAIL',result.get('evidence',''),flush=True)


def run_pilot(output: Path):
    manifest = read(output/'manifest.json', {});diagnoses=read(output/'diagnoses_v2.json', [])
    by_id = {r['case']['id']:r for r in diagnoses}
    selection = {'cases': PILOT_CASES, 'endpoint':PILOT_ENDPOINT, 'max_video_submissions':2*len(PILOT_CASES),
                 'max_parallel_video_requests':2, 'seed_policy':'same new seed per pair, actual payload recorded',
                 'evaluation':'same frozen original scene/source/ledger for both arms; one first take each',
                 'scope':'manually checked generation-origin subset; not an accuracy estimate for the automatic router',
                 'reclassified_before_generation':{
                    'wuyue_1035_clip_09':'prompt mismatch: source/script doctor opens the lock, H3 Subject 1 is Lorne and performs it',
                    'wuyue_1572_clip_01':'prompt/reference mismatch: required Elaya and cat omitted from the actual subject bindings'}}
    atomic_write_json(output/'pilot_selection.json', selection)
    results=[]
    for index,cid in enumerate(PILOT_CASES):
        case=by_id[cid]['case']
        prepared={arm:prepare_arm(output,case,arm) for arm in ['rewrite','retake']}
        # Alternate order; same hardware and seed, with the shared production pool's
        # two slots still respected. Queue time is retained, not called inference time.
        arms=['rewrite','retake'] if index%2==0 else ['retake','rewrite']
        with ThreadPoolExecutor(max_workers=2) as pool:
            videos=list(pool.map(lambda a:render_arm(output,prepared[a]),arms))
        for video in videos:
            verdict=evaluate_arm(output,video)
            result={**video,'verdict':verdict,'passed':evaluation_passed(verdict),
                    'diagnosis_seconds':by_id[cid]['diagnosis'].get('elapsed_seconds',0) if video['arm']=='retake' else 0}
            results.append(result)
            atomic_write_json(output/'pilot_results.json',results)
            print(cid,video['arm'],'PASS' if result['passed'] else 'FAIL',verdict.get('evidence',''),flush=True)
    summary={arm:{'cases':len([r for r in results if r['arm']==arm]),'passed':sum(r['passed'] for r in results if r['arm']==arm),
                 'requested_video_seconds':sum(r['request_seconds'] for r in results if r['arm']==arm),
                 'prepare_seconds':sum(r['prepare_seconds']+r['diagnosis_seconds'] for r in results if r['arm']==arm),
                 'render_seconds_including_queue':sum(r['render_seconds'] for r in results if r['arm']==arm)} for arm in ['rewrite','retake']}
    atomic_write_json(output/'pilot_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "diagnose", "diagnose-numbered", "run-pilot", "verify-originals"])
    parser.add_argument("--novel-dir", type=Path, default=ROOT / "outputs/wuyue")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    os.environ['NOVEL_PLANNER_BACKEND']='deterministic'
    if args.action == "freeze":
        result=freeze(args.novel_dir.resolve(),args.output.resolve());print('Frozen',len(result['cases']),'cases')
    elif args.action=='run-pilot':run_pilot(args.output.resolve())
    elif args.action=='verify-originals':verify_originals(args.output.resolve())
    else:classify(args.output.resolve(), numbered=args.action == "diagnose-numbered")


if __name__ == "__main__":main()
