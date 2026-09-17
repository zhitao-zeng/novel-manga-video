#!/usr/bin/env python
"""Re-apply the current fix_tier() rules to reviews already on disk.

    retier_reviews.py --novel-dir outputs/X [--script-check] [--apply]

thin_review writes each failed clip's tier and, for must_fix, the retake instruction, at review time.  When
the tier rules change (2026-09-13: three regex leaks), the files keep the old verdicts and the delivery gate
keeps counting them.  This recomputes `tier` from the stored verdict with the rules as they are now, drops
the `feedback` entry of a clip that is no longer must_fix (keeps the existing text of one that still is),
rebuilds `flags`, and reports the difference.  The verdicts themselves are never changed; nothing is
re-judged.  Without --apply it only counts.  Each file it changes is backed up once as
episode_review.json.bak-retier.

--script-check asks, for every must_fix clip that was never checked, whether the flagged oddity is what the
book wrote (review_judges_thin.script_check: the clip's event line and source segments against the judge's
complaint); a scripted one drops to optional with the evidence stored, and its retake instruction goes.
The model is asked in the dry run too, so the count is real; only the writing waits for --apply.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from novel_manga.review.contracts import STORY_FATAL
from novel_manga.application.review.evidence import load_review_rules, segment_texts
from novel_manga.review.policy import compose_feedback, fix_tier, flag_line
from novel_manga.application.review.judges import script_check
from novel_manga.models.bible import StoryBible


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--script-check", action="store_true", help="ask whether must_fix oddities are scripted")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    rules = load_review_rules(novel_dir)
    novel_id = novel_dir.name

    episodes = changed = 0
    dropped: list[tuple[str, str, str]] = []
    added: list[tuple[str, str]] = []
    retiered = {"must_fix": 0, "optional": 0, "ignore": 0}
    checked = scripted = 0
    for path in sorted(novel_dir.glob(f"{novel_id}_*/episode_review.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        episodes += 1
        clips = report.get("clips") or {}
        feedback = dict(report.get("feedback") or {})
        new_feedback: dict[str, str] = {}
        flags: list[str] = []
        touched = False
        plan_clips: dict[str, dict] = {}
        segments: dict[str, str] = {}
        if args.script_check:
            try:
                plan = json.loads((path.parent / "clip_plan.json").read_text(encoding="utf-8"))
                plan_clips = {c["clip_id"]: c for c in plan.get("clips", []) if isinstance(c, dict)}
            except (OSError, ValueError, KeyError):
                plan_clips = {}
            segments = segment_texts(path.parent)
        for clip_id, verdict in clips.items():
            if not isinstance(verdict, dict) or verdict.get("severity") != "fail":
                continue
            tier = fix_tier(verdict, bible, rules)
            if (args.script_check and tier == "must_fix" and "scripted" not in verdict
                    and not (verdict.get("story_ok") is False and verdict.get("story_kind") in STORY_FATAL)):
                check = script_check(plan_clips.get(clip_id, {}), verdict, segments)
                if check is not None:
                    checked += 1
                    verdict["scripted"] = {"evidence": check["evidence"], "note": check["note"]} if check["scripted"] else False
                    touched = True
                    if check["scripted"]:
                        scripted += 1
                        tier = fix_tier(verdict, bible, rules)
            if verdict.get("tier") != tier:
                touched = True
                verdict["tier"] = tier
                retiered[tier] += 1
            if tier != "ignore":
                flags.append(flag_line(clip_id, verdict, tier))
            if tier == "must_fix":
                new_feedback[clip_id] = feedback.get(clip_id) or compose_feedback(verdict)
                if clip_id not in feedback:
                    added.append((path.parent.name, clip_id))
        for clip_id in feedback:
            if clip_id not in new_feedback:
                dropped.append((path.parent.name, clip_id, str(feedback[clip_id])[:60]))
        if new_feedback != feedback:
            touched = True
        if not touched:
            continue
        changed += 1
        if args.apply:
            backup = path.with_name("episode_review.json.bak-retier")
            if not backup.exists():
                shutil.copy2(path, backup)
            report["feedback"] = new_feedback
            report["flags"] = flags
            path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{novel_id}: {episodes} 集审查，{changed} 集会变；tier 改动 {retiered}；"
          + (f"剧本核对 {checked} 段、剧本要求 {scripted} 段；" if args.script_check else "")
          + f"重拍指令 减 {len(dropped)} 段 / 增 {len(added)} 段" + ("" if args.apply else "（预演，加 --apply 才写）"))
    for episode, clip_id, text in dropped[:8]:
        print(f"  - {episode} {clip_id}: {text}")
    for episode, clip_id in added[:4]:
        print(f"  + {episode} {clip_id}")
    return 0
