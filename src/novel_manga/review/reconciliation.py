"""Same-take review reconciliation and selection. No file access, models or dispatch."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from . import contracts as review_contracts, policy as review_policy

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


def reconcile_review(directory: Path, plan: dict, previous: dict, takes: dict, local: dict, flash: dict) -> dict:
    """Apply the existing evidence priority to an explicit current-take snapshot."""
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
    return result
