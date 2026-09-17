"""Read saved viewer reviews and repair checks against the clip currently selected for the final.

The card-consistency review and the two-pass viewer review ask different questions. Keep their counts separate.
Legacy viewer reviews always sampled attempt_01; a later take or split must not inherit their verdict.
This module only reads files, and never calls a judge or generates media.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from novel_manga.application.production.runs import episode_status


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def current_ids(cid: str, reviewed_at: float, plan: dict) -> list[str]:
    """Follow the split tool's explicit old-to-new numbering, only for a review made before that split."""
    record = plan.get("split_long_stages") or {}
    try:
        split_at = time.mktime(time.strptime(record["at"], "%Y-%m-%d %H:%M:%S"))
    except (KeyError, ValueError):
        return [cid]
    if reviewed_at >= split_at:
        return [cid]
    return record.get("split", {}).get(cid) or [record.get("renamed", {}).get(cid, cid)]


def viewer_progress(novel_dir: Path, h3_lane: bool) -> dict | None:
    baseline_path = novel_dir / "second_review_final.json"
    baseline = read_json(baseline_path, [])
    verification = novel_dir / "second_review" / "verify99" / "repaired"
    verified_files = list((verification / "look").glob("*.json"))
    if not baseline_path.is_file() and not verified_files:
        return None
    targets = {(r["episode"], r["clip"]): (r, mtime(baseline_path)) for r in baseline}
    repaired = set()
    for path in verified_files:
        row = read_json(path)
        if not row or not row.get("episode") or not row.get("clip"):
            continue
        key = (row["episode"], row["clip"])
        repaired.add(key)
        targets.setdefault(key, ({**row, "votes": 0}, mtime(path)))
    contexts = {}
    rows = []
    for (episode, cid), (original, original_at) in sorted(targets.items()):
        directory = novel_dir / episode
        if episode not in contexts:
            plan = read_json(directory / "clip_plan.json", {})
            media = read_json(directory / "thin_media_report.json", {})
            try:
                status = episode_status(directory, h3_lane)
            except (OSError, ValueError, KeyError):
                status = "pending"
            contexts[episode] = (plan, {c["clip_id"]: c.get("selected") or {} for c in media.get("clips", [])}, status)
        plan, selected, episode_state = contexts[episode]
        ids = current_ids(cid, original_at, plan)
        row = {"episode": episode, "chapter": int(episode.rsplit("_", 1)[-1]), "clip": ",".join(ids),
               "original_clip": cid, "baseline_votes": original.get("votes", 0),
               "repair_check": (episode, cid) in repaired, "status": "needs_review", "reason": "缺少当前版本的双轮复审",
               "kind": original.get("kind", ""), "observation": original.get("saw", "")}
        candidates = []
        pairs = [(novel_dir / "second_review" / "look" / judge, novel_dir / "second_review" / "read" / judge)
                 for judge in ("local", "flashnext")]
        pairs += [(verification / "look", verification / "read")]
        for look_dir, read_dir in pairs:
            for lookup_id in set([cid, *ids]):
                path = look_dir / f"{episode}__{lookup_id}.json"
                look = read_json(path)
                if look and not look.get("error") and current_ids(lookup_id, mtime(path), plan) == ids:
                    candidates.append((mtime(path), look, read_json(read_dir / path.name), str(path)))
        if len(ids) != 1:
            row["reason"] = "原镜头已拆分，旧判定不能代表新片段"
        elif episode_state not in {"done", "done_with_warnings"}:
            row["reason"] = "成片尚未按当前计划完成，待完成后复审"
        elif candidates:
            stamp, look, read, source = max(candidates, key=lambda c: c[0])
            video = directory / "work" / "clips" / ids[0] / "attempt_01" / "clip.mp4"
            chosen = Path(selected.get(ids[0], {}).get("video") or "")
            if not video.is_file() or chosen.resolve() != video.resolve():
                row["reason"] = "当前成片选择了其他片段，旧验收不适用"
            elif mtime(video) > stamp:
                row["reason"] = "验收后视频又有更新，待复审"
            elif not read or read.get("saw") != look.get("saw") or not isinstance(read.get("read"), bool):
                row["reason"] = "看图或描述复核尚未完成"
            elif not isinstance(look.get("wrong_out_of_100"), int):
                row["reason"] = "复审记录缺少有效判定"
            else:
                votes = int(look["wrong_out_of_100"] >= 60) + int(read["read"])
                row.update(status={0: "clear", 1: "one_vote", 2: "confirmed"}[votes],
                           reason={0: "双轮复审未发现明显错误", 1: "只有一方存疑，需人工看一下", 2: "双轮复审仍确认有问题"}[votes],
                           kind=look.get("kind", ""), observation=look.get("saw", ""), source=source,
                           reviewed_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)))
        rows.append(row)
    counts = dict(Counter(r["status"] for r in rows))
    repair_counts = dict(Counter(r["status"] for r in rows if r["repair_check"]))
    return {"baseline_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime(baseline_path))) if baseline_path.is_file() else None,
            "baseline_confirmed": sum(r.get("votes", 0) >= 2 for r in baseline),
            "baseline_one_vote": sum(r.get("votes") == 1 for r in baseline),
            "tracked": len(rows), "counts": counts, "repair_checks": len(repaired), "repair_counts": repair_counts,
            "rows": rows}
