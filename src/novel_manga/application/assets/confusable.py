#!/usr/bin/env python
"""Which characters does the generator confuse with each other, from the review record.

A pair earns its place by two things at once: the clips holding both fail identity far more
often than the novel's own baseline, and it happens enough times to be a pattern rather than a
run of bad luck.  Writes outputs/<novel>/confusable_pairs.json, which the planner reads to
keep those two out of the same shot.
"""
from novel_manga.application.configuration import project_root
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = project_root() / "outputs"
MIN_TOGETHER = 30      # fewer than this and the rate means little
MIN_FAILURES = 20      # a pattern, not a handful
LIFT = 1.5             # at least half again the novel's own failure rate


def analyse(novel: str) -> dict:
    manifest = ROOT / novel / "series_assets" / "manifest.json"
    names = {}
    if manifest.is_file():
        names = {c["asset_id"]: c.get("name", "") for c in json.loads(manifest.read_text(encoding="utf-8")).get("characters", [])}
    together: Counter = Counter()
    failed: Counter = Counter()
    clips = bad = 0
    for d in sorted((ROOT / novel).glob(f"{novel}_*")):
        review, plan_path = d / "episode_review.json", d / "clip_plan.json"
        if not (review.is_file() and plan_path.is_file()):
            continue
        try:
            verdicts = json.loads(review.read_text(encoding="utf-8")).get("clips") or {}
            plan = {c["clip_id"]: c for c in json.loads(plan_path.read_text(encoding="utf-8"))["clips"]}
        except (OSError, ValueError):
            continue
        for clip_id, verdict in verdicts.items():
            clip = plan.get(clip_id)
            if not clip or "identity_ok" not in verdict:
                continue
            cast = sorted({names.get(r.get("asset_id"), "") for r in clip.get("references") or []
                           if r.get("role") == "character"} - {""})
            clips += 1
            failure = verdict.get("identity_ok") is False
            bad += failure
            for i, a in enumerate(cast):
                for b in cast[i + 1:]:
                    together[(a, b)] += 1
                    if failure:
                        failed[(a, b)] += 1
    base = bad / clips if clips else 0
    pairs = []
    for pair, n in together.items():
        k = failed[pair]
        if n >= MIN_TOGETHER and k >= MIN_FAILURES and base and (k / n) >= LIFT * base:
            pairs.append({"pair": list(pair), "together": n, "failed": k, "rate": round(k / n, 3)})
    pairs.sort(key=lambda p: -p["failed"])
    return {"novel": novel, "baseline": round(base, 3), "clips_reviewed": clips, "pairs": pairs}


def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return 0
    from novel_manga.application.configuration import pipeline_config
    for novel in sys.argv[1:] or [n['id'] for n in pipeline_config(ROOT.parent)['novels']]:
        result = analyse(novel)
        out = ROOT / novel / "confusable_pairs.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{novel}: 基线出错率 {100 * result['baseline']:.0f}%，命中 {len(result['pairs'])} 对 → {out}")
        for p in result["pairs"][:6]:
            print(f"   {p['pair'][0]} + {p['pair'][1]}: 同框 {p['together']}，出错 {p['failed']}（{100 * p['rate']:.0f}%）")
    return 0
