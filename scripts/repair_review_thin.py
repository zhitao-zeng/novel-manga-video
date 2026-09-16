"""One precise verdict per current take, shared by repair, fill, audit and Flash confirmation."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import novel_manga.review.contracts as review_contracts
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
from novel_manga.util import atomic_write_json
from verify_clips_thin import ROOT, Verifier, parse_episodes


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def evidence_key(ep: int, cid: str, video: str, take: list) -> tuple:
    return ep, cid, video, json.dumps(take, sort_keys=True)


def merge_evidence(result: dict, row: dict):
    if "error" in row or not row.get("video") or not row.get("take"):
        return
    key = evidence_key(int(row["ep"]), row["clip"], row["video"], row["take"])
    old = result.get(key, {})
    if old.get('mode') == 'source_confirm' and row.get('mode') != 'source_confirm':
        return
    if old.get("mode") == "confirm" and row.get("mode") not in {"confirm", 'source_confirm'}:
        return  # explicit adjudication retains priority over a blind recheck
    if old.get("mode") == "joint" and row.get("mode") not in {"joint", "confirm", 'source_confirm'}:
        return
    result[key] = row


def load_evidence(paths: list[Path]) -> dict:
    result = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # the running scanner may still be appending its last line
                merge_evidence(result, row)
    return result


def current_evidence(legacy: Path, state_dir: Path) -> tuple[dict, dict]:
    local = load_evidence([legacy / "wy_verify.jsonl", state_dir / "scan_local.jsonl", state_dir / "verified.jsonl"])
    flash = load_evidence([legacy / "wy_verify_flash.jsonl", state_dir / "scan_flash.jsonl"])
    queue = state_dir / "shared_audit.sqlite3"
    if queue.is_file():
        from shared_audit_thin import export_results
        for row in export_results(queue, 'qwen'):
            merge_evidence(local, row)
        for row in export_results(queue, 'flash'):
            merge_evidence(flash, row)
    return local, flash


def current_takes(directory: Path, plan: dict, review: dict) -> dict:
    media = read(directory / "thin_media_report.json", {})
    selected = {c["clip_id"]: c.get("selected") or {} for c in media.get("clips", [])}
    result = {}
    for clip in plan.get("clips", []):
        if clip.get("kind") != "video":
            continue
        cid = clip["clip_id"]
        video = selected.get(cid, {}).get("video") or (review.get("clips", {}).get(cid) or {}).get("video")
        if not video:
            continue
        path = Path(video)
        if not path.is_absolute():
            path = ROOT / path
        take = review_storage.take_identity(path)
        if take:
            result[cid] = {"video": str(path), "take": take}
    return result


def verdict_from_record(record: dict) -> dict:
    answer = dict(record)
    people = answer.get("people") or []
    answer["people"] = [p if isinstance(p, dict) else {"who": p} for p in people]
    verdict = review_policy.verify_to_verdict(answer)
    verdict["verify"]["people"] = people
    verdict["tier"] = "must_fix" if verdict["story_ok"] is False else "optional"
    if record.get("mode") in {"confirm", 'source_confirm'}:
        verdict["flash_checked"] = True
    if record.get('mode') == 'source_confirm':
        verdict['source_confirmed_at'] = record.get('source_confirmed_at')
    if record.get("mode") == "joint":
        verdict["joint_checked"] = True
    return {"video": record["video"], "take": record["take"], **verdict}


def assemble_review(directory: Path, previous: dict, clips: dict) -> dict:
    feedback = {cid: v.get("feedback") or v.get("story_issue") or "按原文修正画面"
                for cid, v in clips.items() if (v.get("tier") or v.get("fix_tier")) == "must_fix"}
    return {**previous, "policy": review_contracts.POLICY, "episode": directory.name, "video_name": "clip.mp4", "clips": clips,
            "feedback": feedback, "flags": [f"{cid}: {note}" for cid, note in feedback.items()]}


def reconcile(directory: Path, local: dict, flash: dict, *, write: bool = True) -> tuple[dict, dict]:
    plan = read(directory / "clip_plan.json", {})
    previous = read(directory / "episode_review.json", {})
    takes = current_takes(directory, plan, previous)
    ep = int(directory.name.rsplit("_", 1)[1])
    clips = copy.deepcopy(previous.get("clips") or {})
    for cid, current in takes.items():
        old = clips.get(cid) or {}
        matches = old.get("video") == current["video"] and old.get("take") == current["take"]
        key = evidence_key(ep, cid, current["video"], current["take"])
        record = local.get(key)
        # Preserve a current explicit technical failure (e.g. a black clip).
        if matches and old.get("technical"):
            continue
        # Before the unified controller, the final gate changed the tier and
        # stored `verified` but left the first judge's conflicting `verify` in
        # place. Import the gate's actual same-take evidence once, then retain
        # the single canonical verdict as usual.
        final_gate = old.get("verified") or {}
        legacy_final = matches and final_gate and record and record.get("verdict") == final_gate.get("verdict")
        if record and (not matches or not old.get("verify") or legacy_final or (record.get("mode") == "confirm" and not old.get("flash_checked"))
                       or (record.get("mode") == "joint" and not old.get("joint_checked"))
                       or (record.get('mode') == 'source_confirm' and old.get('source_confirmed_at') != record.get('source_confirmed_at'))):
            clips[cid] = verdict_from_record(record)
        elif not matches:
            clips[cid] = {**current, "severity": "review_error", "error": "current take awaiting precise review"}
        row = clips.get(cid) or {}
        second = flash.get(key)
        pending = bool(second and second.get("verdict") == "obvious" and not row.get("flash_checked"))
        if pending:
            row["flash_pending"] = str(second.get("evidence") or "另一位审片员发现明显画面错误，请独立核实")
        else:
            row.pop("flash_pending", None)
    # Superseded clip IDs must not keep obsolete retake instructions alive.
    expected = {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"}
    result = assemble_review(directory, previous, {cid: v for cid, v in clips.items() if cid in expected})
    if write and result != previous:
        atomic_write_json(directory / "episode_review.json", result)
    return result, takes


def missing_reviews(review: dict, takes: dict, scope: str) -> list[str]:
    result = []
    for cid in takes:
        row = review.get("clips", {}).get(cid) or {}
        if row.get("technical"):
            continue
        if scope == "flash":
            wanted = bool(row.get("flash_pending"))
        elif row.get("verify"):
            wanted = False
        elif scope == "all":
            wanted = True
        elif scope == "candidates":
            wanted = (row.get("tier") or row.get("fix_tier")) == "must_fix"
        else:
            wanted = row.get("severity") == "review_error" or (row.get("tier") or row.get("fix_tier")) == "must_fix"
        if wanted:
            result.append(cid)
    return result


def inspection_counts(review: dict, takes: dict, expected: list[str]) -> dict:
    """Disjoint current-take outcomes; an old judge candidate is still unchecked."""
    counts = {"total": len(expected), "passed": 0, "failed": 0, "unchecked": 0,
              "unconfirmed_candidates": 0, "flash_pending": 0}
    for cid in expected:
        row = review.get("clips", {}).get(cid) or {}
        current = takes.get(cid)
        matches = bool(current and row.get("video") == current["video"] and row.get("take") == current["take"])
        precise = row.get("verify") or {}
        checked = matches and bool(precise or row.get("technical"))
        if not checked:
            counts["unchecked"] += 1
            if (row.get("tier") or row.get("fix_tier")) == "must_fix":
                counts["unconfirmed_candidates"] += 1
        elif row.get("technical") or row.get("story_ok") is False or precise.get("verdict") == "obvious":
            counts["failed"] += 1
        else:
            counts["passed"] += 1
        if matches and row.get("flash_pending"):
            counts["flash_pending"] += 1
    return counts


class CurrentVerifier(Verifier):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("repair_advice", True)
        super().__init__(*args, **kwargs)

    def video_of(self, ep_dir, cid, review_clip):
        plan = self.load(ep_dir / "clip_plan.json") or {}
        review = self.load(ep_dir / "episode_review.json") or {}
        current = current_takes(ep_dir, plan, review).get(cid)
        return Path(current["video"]) if current else None


def review_batch(novel: Path, episodes: list[int], scope: str, state_dir: Path, legacy: Path, workers: int = 6, max_tokens: int | None = None) -> dict:
    cache = state_dir / "verified.jsonl"
    local, flash = current_evidence(legacy, state_dir)
    jobs = []
    for n in episodes:
        directory = novel / f"{novel.name}_{n}"
        review, takes = reconcile(directory, local, flash)
        for cid in missing_reviews(review, takes, scope):
            row = review["clips"][cid]
            claim = row.get("flash_pending") if scope == "flash" else row.get("story_issue", "")
            jobs.append((n, cid, str(claim or ""), "confirm" if scope == "flash" else "managed"))
    if jobs:
        verifier = CurrentVerifier(novel, cache, "local", workers, max_tokens=max_tokens)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for record in pool.map(verifier.verify, jobs):
                with cache.open("a", encoding="utf-8") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                if "error" not in record:
                    local[evidence_key(record["ep"], record["clip"], record["video"], record["take"])] = record
    remaining = 0
    for n in episodes:
        directory = novel / f"{novel.name}_{n}"
        review, takes = reconcile(directory, local, flash)
        remaining += len(missing_reviews(review, takes, scope))
        from repair_history import observe, publish_if_ready
        observe(directory, review, takes)
        publish_if_ready(directory, review, takes)
    return {"episodes": episodes, "scope": scope, "judged": len(jobs), "remaining": remaining}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--scope", choices=["candidates", "changed", "all", "flash"], required=True)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    from thin_batch import load_dotenv
    load_dotenv(ROOT / ".env")
    result = review_batch(args.novel_dir.resolve(), sorted(parse_episodes(args.episodes)), args.scope,
                          args.state_dir.resolve(), args.legacy_dir.resolve(), args.workers, args.max_tokens)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["remaining"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
