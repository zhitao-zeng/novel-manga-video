"""Compact repair facts; a successful finish is not itself a repair recipe."""
from __future__ import annotations


def summarize_case(record):
    issues = [{"kind": row.get("kind"), "clips": row.get("clips") or [],
               "detail": str(row.get("detail") or "")[:350]}
              for row in (record.get("issues") or [])[:3]]
    operations, failures, general_failures = [], [], []
    seen_operations, seen_failures = set(), set()
    for attempt in reversed(record.get("attempts") or []):
        blocked = attempt.get("blocked") or {}
        changed = set(attempt.get("changed") or [])
        accepted = set(attempt.get("accepted_clips") or [])
        remedies = attempt.get("remedies") or []
        for remedy in remedies:
            cid, action = remedy.get("clip"), remedy.get("action")
            key = (action, cid)
            if cid in (changed | accepted) and (
                    not isinstance(blocked, dict) or cid not in blocked) and key not in seen_operations:
                seen_operations.add(key)
                operations.append({"action": action, "clip": cid,
                    "reason": str(remedy.get("reason") or "")[:700],
                    "receipt": "accepted_clip" if cid in accepted else "changed"})
        if isinstance(blocked, dict):
            for cid, error in blocked.items():
                remedy = next((r for r in remedies if r.get("clip") == cid), {})
                key = (remedy.get("action", attempt.get("action")), cid, str(error))
                if key in seen_failures:
                    continue
                seen_failures.add(key)
                failures.append({"action": key[0], "clip": cid,
                    "reason": str(remedy.get("reason") or "")[:350], "error": str(error)[:350]})
        elif blocked:
            general_failures.append({"action": attempt.get("action"), "error": str(blocked)[:350]})
    # Keep a concrete input/request/take change ahead of a large batch of keeps.
    operations.sort(key=lambda row: row["action"] == "keep")
    summary = {"episode": record.get("episode"),
        "kinds": record.get("kinds") or sorted({r["kind"] for r in issues if r["kind"]}),
        "outcome": record.get("outcome", "unresolved"), "issues": issues,
        "effective_operations": operations[:3],
        "failed_operations": (failures or general_failures)[:2],
        "repair_details": "observed_receipts" if operations else "not_recorded"}
    if record.get("retrospective"):
        summary["retrospective"] = record["retrospective"]
    return summary
