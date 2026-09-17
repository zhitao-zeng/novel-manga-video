#!/usr/bin/env python
"""Mark clips for re-rendering after a character card is replaced.

A clip is reused when its request matches, and requests written before 2026-09-10 record only
the reference paths, so replacing a card leaves every existing clip untouched.  Deciding which
of them to redo is a judgement about money, not something to do automatically: this moves the
cached attempt of the chosen clips aside, and the next render regenerates exactly those.

    invalidate_cards.py <novel> <asset_id>[,<asset_id>...] [--flagged-only] [--apply]

Without --apply it only reports.  The moved attempt keeps its files under
work/clips/<clip>/superseded-<timestamp>/, outside the attempt_* names the runner scans, so
nothing is lost and nothing is silently reused.
"""
from novel_manga.application.configuration import project_root
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = project_root() / "outputs"


def main() -> int:
    import sys
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    novel, assets = sys.argv[1], set(sys.argv[2].split(","))
    flagged_only = "--flagged-only" in sys.argv
    apply = "--apply" in sys.argv
    stamp = time.strftime("%m%d-%H%M%S")
    hits = []
    for d in sorted((ROOT / novel).glob(f"{novel}_*")):
        idx = d.name.rsplit("_", 1)[-1]
        plan, review = d / "clip_plan.json", d / "episode_review.json"
        if not idx.isdigit() or not plan.is_file() or not (d / f"{novel}_{idx}.mp4").is_file():
            continue
        try:
            clips = json.loads(plan.read_text(encoding="utf-8"))["clips"]
        except (OSError, ValueError):
            continue
        verdicts = {}
        if review.is_file():
            try:
                verdicts = json.loads(review.read_text(encoding="utf-8")).get("clips") or {}
            except (OSError, ValueError):
                verdicts = {}
        for clip in clips:
            if clip.get("kind") != "video":
                continue
            used = {r.get("asset_id") for r in clip.get("references") or [] if r.get("role") == "character"}
            if not (used & assets):
                continue
            if flagged_only and verdicts.get(clip["clip_id"], {}).get("identity_ok") is not False:
                continue
            work = d / "work" / "clips" / clip["clip_id"]
            if any(work.glob("attempt_*")):
                hits.append((d.name, clip["clip_id"], work))
    print(f"{novel}: {len(hits)} 个片段引用了 {sorted(assets)}"
          + ("（只算判坏的）" if flagged_only else "") + f"，分布在 {len({h[0] for h in hits})} 集")
    if not apply:
        print("这是预览。加 --apply 才会真的作废缓存。")
        for name, clip_id, _ in hits[:5]:
            print(f"   {name} {clip_id}")
        return 0
    moved = 0
    for name, clip_id, work in hits:
        target = work / f"superseded-{stamp}"
        target.mkdir(parents=True, exist_ok=True)
        for attempt in sorted(work.glob("attempt_*")):
            shutil.move(str(attempt), str(target / attempt.name))
            moved += 1
    print(f"已作废 {moved} 个尝试目录，移到 work/clips/<clip>/superseded-{stamp}/；下次渲染会重生成这些片段。")
    return 0
