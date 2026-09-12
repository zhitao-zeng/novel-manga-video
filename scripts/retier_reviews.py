#!/usr/bin/env python
"""Re-apply the current fix_tier() rules to reviews already on disk.

    retier_reviews.py --novel-dir outputs/X [--apply]

thin_review writes each failed clip's tier and, for must_fix, the retake instruction, at review time.  When
the tier rules change (2026-09-13: three regex leaks), the files keep the old verdicts and the delivery gate
keeps counting them.  This recomputes `tier` from the stored verdict with the rules as they are now, drops
the `feedback` entry of a clip that is no longer must_fix (keeps the existing text of one that still is),
rebuilds `flags`, and reports the difference.  The verdicts themselves are never changed; nothing is
re-judged.  Without --apply it only counts.  Each file it changes is backed up once as
episode_review.json.bak-retier.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_review import apply_genre_review_rules, compose_feedback, fix_tier  # noqa: E402
from novel_manga.models import StoryBible  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    apply_genre_review_rules(novel_dir)
    novel_id = novel_dir.name

    episodes = changed = 0
    dropped: list[tuple[str, str, str]] = []
    added: list[tuple[str, str]] = []
    retiered = {"must_fix": 0, "optional": 0, "ignore": 0}
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
        for clip_id, verdict in clips.items():
            if not isinstance(verdict, dict) or verdict.get("severity") != "fail":
                continue
            tier = fix_tier(verdict, bible)
            if verdict.get("tier") != tier:
                touched = True
                verdict["tier"] = tier
                retiered[tier] += 1
            if tier != "ignore":
                prefix = "" if tier == "must_fix" else "[可选] "
                flags.append(f"{clip_id}: {prefix}{verdict.get('identity_issue') or verdict.get('defect_issue') or verdict.get('feedback')}")
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
          f"重拍指令 减 {len(dropped)} 段 / 增 {len(added)} 段" + ("" if args.apply else "（预演，加 --apply 才写）"))
    for episode, clip_id, text in dropped[:8]:
        print(f"  - {episode} {clip_id}: {text}")
    for episode, clip_id in added[:4]:
        print(f"  + {episode} {clip_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
