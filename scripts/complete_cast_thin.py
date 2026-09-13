#!/usr/bin/env python
"""Put back the characters a storyboard's own shot descriptions name but its casts left out.

    complete_cast_thin.py --novel-dir outputs/X [--chapters 12,48-60] [--apply] [--no-pack]

plan_chapter_thin now completes a shot's `characters` from its description at planning time (2026-09-13,
雾月 761: 薇奥拉 kissed 莱恩 in the description, the cast said 莱恩 and the cat, so the cat kissed him).
Storyboards planned before that carry the gap.  This adds the missing names to chapter_script.json
(backup chapter_script.json.bak-cast, once) and re-packs clip_plan.json with build_clip_plan_thin in the
clip length the existing plan was packed for, so only the clips whose cast changed carry a new request
and get re-rendered.  Without --apply it only counts; --no-pack rewrites the storyboard but leaves the
clip plan alone (tests, or a pack you want to run yourself).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_chapter_thin  # noqa: E402
from novel_manga.models import StoryBible  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
LANE_FIELDS = ("prompt_h3", "prompt_h3_of")  # written by the render lane, not the packer


def splice_plans(old: dict, new: dict) -> tuple[dict | None, list[str]]:
    """The old plan with just the clips whose cast changed taken from the new one.

    A full re-pack rewrites every prompt with today's template and drops the lane's English prompts, which
    would send the whole episode back to the renderer and the translator; only a changed cast needs a new
    request.  None when the clips no longer line up (different boundaries): then the new plan stands."""
    old_clips = list(old.get("clips") or [])
    new_clips = list(new.get("clips") or [])
    if [c.get("clip_id") for c in old_clips] != [c.get("clip_id") for c in new_clips]:
        return None, []
    changed: list[str] = []
    merged_clips = []
    for before, after in zip(old_clips, new_clips):
        if (before.get("cast") or []) == (after.get("cast") or []) and (before.get("background_only") or []) == (after.get("background_only") or []):
            merged_clips.append(before)
            continue
        entry = {k: v for k, v in after.items() if k not in LANE_FIELDS}
        merged_clips.append(entry)
        changed.append(str(after.get("clip_id")))
    return {**old, "clips": merged_clips}, changed


def parse_chapters(spec: str) -> set[int]:
    """12,48-60 -> {12, 48, ..., 60}"""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapters", help="episode indexes, e.g. 12,48-60 (default: every episode with a storyboard)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-pack", action="store_true", help="rewrite storyboards only; do not re-pack clip plans")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    novel_id = novel_dir.name
    bible_path = novel_dir / "story_bible.json"
    everyone = [c.name for c in StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8")).characters]
    aliases = novel_dir / "bible_aliases.json"
    plan_chapter_thin.ALIASES.update(json.loads(aliases.read_text(encoding="utf-8")) if aliases.is_file() else {})
    wanted = set(parse_chapters(args.chapters)) if args.chapters else None

    episodes = shots_changed = clips_changed = packed = 0
    by_name: Counter = Counter()
    changed_episodes: list[int] = []
    for script_path in sorted(novel_dir.glob(f"{novel_id}_*/chapter_script.json")):
        index = script_path.parent.name.rsplit("_", 1)[-1]
        if not index.isdigit() or (wanted is not None and int(index) not in wanted):
            continue
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        touched = 0
        for shot in script.get("shots", []):
            cast, added = plan_chapter_thin.complete_characters(list(shot.get("characters") or []), shot, everyone)
            if added:
                shot["characters"] = cast
                touched += 1
                by_name.update(added)
        if not touched:
            continue
        episodes += 1
        shots_changed += touched
        changed_episodes.append(int(index))
        if not args.apply:
            continue
        backup = script_path.with_name("chapter_script.json.bak-cast")
        if not backup.exists():
            shutil.copy2(script_path, backup)
        script_path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.no_pack:
            continue
        plan_path = script_path.parent / "clip_plan.json"
        old = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
        env = {**os.environ}
        if str(old.get("policy", "")).endswith("-15s"):
            env["NOVEL_CLIP_SECONDS_MAX"] = "15"
        else:
            env.pop("NOVEL_CLIP_SECONDS_MAX", None)
        result = subprocess.run([sys.executable, str(SCRIPTS / "build_clip_plan_thin.py"), "--episode-dir", str(script_path.parent),
                                 "--bible", str(bible_path)], cwd=SCRIPTS.parent, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  {script_path.parent.name}: 重算段计划失败: {result.stderr.strip()[-300:]}")
            continue
        packed += 1
        new = json.loads(plan_path.read_text(encoding="utf-8"))
        merged, changed = splice_plans(old, new)
        if merged is None:
            print(f"  {script_path.parent.name}: 分段变了（{len(old.get('clips', []))} → {len(new.get('clips', []))} 段），整集按新计划重渲")
            clips_changed += len(new.get("clips", []))
        else:
            atomic_write_json(plan_path, merged)
            clips_changed += len(changed)

    print(f"{novel_id}: {episodes} 集 / {shots_changed} 镜头的演员表缺人" + (f"；已改分镜 {episodes} 集，重算 {packed} 集，演员表变了的段 {clips_changed}"
          if args.apply else "（预演，加 --apply 才写）"))
    print("补上最多的角色:", by_name.most_common(10))
    (novel_dir / "cast_completion_targets.txt").write_text(",".join(map(str, changed_episodes)), encoding="utf-8")
    print("涉及集号记在", novel_dir / "cast_completion_targets.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
