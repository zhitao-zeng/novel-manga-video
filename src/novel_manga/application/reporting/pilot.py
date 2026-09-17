#!/usr/bin/env python
"""How many corrected clips came back clean: the repair rate of a retake pilot.

    pilot_report.py --novel-dir outputs/X --targets tmp/fix/pilot_targets_0913.txt

For every episode in the list: the clips its review_feedback.json instructs, whether the episode has been
re-rendered (a final newer than the correction file) and re-reviewed (a review newer than that final),
and each instructed clip's severity and tier in the latest review.  A clip counts as repaired when it is
no longer must_fix; the two other columns keep "still must_fix" and "not yet re-reviewed" apart, so an
early reading is never mistaken for a low rate.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from novel_manga.application.production.runs import REVIEW_POLICY, episode_status


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True, help="comma-separated episode numbers")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    novel_id = novel_dir.name
    numbers = sorted({int(x) for x in args.targets.read_text(encoding="utf-8").split(",") if x.strip()})

    totals: Counter = Counter()
    rows = []
    for n in numbers:
        d = novel_dir / f"{novel_id}_{n}"
        try:
            corrections = json.loads((d / "review_feedback.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            corrections = {}
        final = d / f"{d.name}.mp4"
        rerendered = mtime(final) > mtime(d / "review_feedback.json") > 0
        rereviewed = rerendered and mtime(d / "episode_review.json") >= mtime(final)
        try:
            review = json.loads((d / "episode_review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            review = {}
        rereviewed = rereviewed and review.get("policy") == REVIEW_POLICY
        status = episode_status(d, True)
        verdicts = review.get("clips") or {}
        feedback = review.get("feedback") or {}
        per_clip = []
        for cid in sorted(corrections):
            if not rereviewed or verdicts.get(cid, {}).get("severity") not in {"pass", "minor", "fail"}:
                outcome = "待复审"
            elif cid in feedback:
                outcome = "仍 must_fix"
            else:
                outcome = "修好"
            totals[outcome] += 1
            per_clip.append(f"{cid}:{outcome}({verdicts.get(cid, {}).get('severity', '-')})")
        rows.append((d.name, status, "已重渲" if rerendered else "未重渲", "已复审" if rereviewed else "未复审", " ".join(per_clip)))

    print(f"{novel_id} 试点 {len(numbers)} 集 / {sum(totals.values())} 段：{dict(totals)}")
    judged = totals["修好"] + totals["仍 must_fix"]
    if judged:
        print(f"  已复审的段修好率：{totals['修好']}/{judged} = {100 * totals['修好'] / judged:.0f}%")
    for name, status, rr, rv, clips in rows:
        print(f"  {name:<12} {status:<18} {rr} {rv}  {clips}")
    return 0
