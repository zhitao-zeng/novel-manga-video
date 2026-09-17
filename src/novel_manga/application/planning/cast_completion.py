#!/usr/bin/env python
"""Put back the characters a storyboard's own shot descriptions name but its casts left out.

    complete_cast_thin.py --novel-dir outputs/X [--chapters 12,48-60] [--apply] [--no-pack] [--repack]

plan_chapter_thin now completes a shot's `characters` from its description at planning time (2026-09-13,
雾月 761: 薇奥拉 kissed 莱恩 in the description, the cast said 莱恩 and the cat, so the cat kissed him).
Storyboards planned before that carry the gap.  This adds the missing names to chapter_script.json
(backup chapter_script.json.bak-cast, once) and rebuilds only the clips whose cast changed.

The clip boundaries stay where they are: the packer cuts on cast changes, so re-packing the completed
storyboard would move the cuts in a quarter of the episodes (星海: 115 of 448), and a moved cut means the
whole episode renders again and every review of it is void. Recorded stage parts recover each clip's own
dialogue range, and each clip's entry is rebuilt in place with the plan's original tier. Only changed
casts, dialogue ranges or references carry a new request; the others keep their prompt and
the lane's English prompt.  An episode whose old cuts cannot be reproduced (a plan from another packer
version) is left alone and counted.  --repack asks for a full re-pack instead (new cuts, whole episode).
Without --apply it only counts; --no-pack rewrites the storyboard but leaves the clip plan alone.
--rebuild-existing also checks plans whose storyboard was already completed by an earlier run.
"""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.application.configuration import project_root
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import novel_manga.planning.cast as pc_cast
import novel_manga.application.planning.context as planner_context
from novel_manga.planning.context import PlannerContext  # noqa: E402
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json  # noqa: E402

SCRIPTS = (project_root() / 'scripts')
LANE_FIELDS = ("prompt_h3", "prompt_h3_of")  # written by the render lane, not the packer


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
        if all((before.get(k) or []) == (after.get(k) or [])
               for k in ("cast", "background_only", "lines", "chat_lines", "references")):
            merged_clips.append(before)
            continue
        entry = {k: v for k, v in after.items() if k not in LANE_FIELDS}
        merged_clips.append(entry)
        changed.append(str(after.get("clip_id")))
    return {**old, "clips": merged_clips}, changed


def rebuild_in_place(episode_dir: Path, bible_path: Path, old_script: dict, new_script: dict, old_plan: dict) -> tuple[dict | None, list[str], str]:
    """Rebuild the clips of `old_plan` with the casts of `new_script`, cuts unchanged.

    Runs in a process whose NOVEL_CLIP_SECONDS_MAX matches the plan (build_clip_plan_thin reads it at
    import).  Returns (merged plan, changed clip ids, "") or (None, [], why) when the old cuts could
    not be reproduced."""
    ctx = packing_context.context_for_plan(episode_dir, bible_path, old_plan)
    shots = packing_service.prepared_shots(copy.deepcopy(new_script), episode_dir, identity_data=ctx.get("identity_data"))
    if len(shots) != len(old_script.get("shots") or []):
        return None, [], "shot count differs"
    try:
        clip_shots = ClipCompiler(ctx.get('compiler_options') or compiler_options()).shots_for_plan(old_plan, shots)
    except ValueError as error:
        return None, [], str(error)
    # The plan remembers which shots each clip covers; the clip is rebuilt from exactly those, so the cuts are
    # the old ones whatever today's packer would decide.  (pack() builds the same dict: kind, location, shots,
    # seconds.)  Title cards and clips without shot bookkeeping keep their old entry.
    rebuilt = []
    for before in old_plan.get("clips") or []:
        pieces = clip_shots.get(before["clip_id"])
        if not pieces:
            rebuilt.append(before)
            continue
        clip = {"kind": "video", "location": before.get("location") or pieces[0]["location"], "shots": pieces,
                "seconds": round(sum(ClipCompiler(ctx.get('compiler_options') or compiler_options()).shot_seconds(p) for p in pieces), 2)}
        rebuilt.append(packing_service.clip_entry(clip, before["clip_id"], ctx))
    merged, changed = splice_plans(old_plan, {"clips": rebuilt})
    if merged is None:
        return None, [], "clip ids differ after rebuild"
    return merged, changed, ""


def episodes_of(novel_dir: Path, wanted: set[int] | None):
    for script_path in sorted(novel_dir.glob(f"{novel_dir.name}_*/chapter_script.json")):
        index = script_path.parent.name.rsplit("_", 1)[-1]
        if index.isdigit() and (wanted is None or int(index) in wanted):
            yield int(index), script_path


def plan_mode(episode_dir: Path) -> str:
    try:
        policy = str(json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8")).get("policy", ""))
    except (OSError, ValueError):
        return "30"
    return "15" if policy.endswith("-15s") else "30"


def main() -> int:
    planner_ctx = PlannerContext.from_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapters", help="episode indexes, e.g. 12,48-60 (default: every episode with a storyboard)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-pack", action="store_true", help="rewrite storyboards only; do not touch clip plans")
    parser.add_argument("--repack", action="store_true", help="full re-pack with new cuts instead of rebuilding in place")
    parser.add_argument("--mode", choices=("15", "30"), help=argparse.SUPPRESS)  # worker: one clip length per process
    parser.add_argument("--dry-rebuild", action="store_true", help="rebuild in memory and report, write nothing")
    parser.add_argument("--rebuild-existing", action="store_true", help="also rebuild plans whose casts were already completed, retaining their original cuts and tier")
    args = parser.parse_args()
    if args.dry_rebuild:
        args.apply = True
    novel_dir = args.novel_dir.resolve()
    bible_path = novel_dir / "story_bible.json"
    everyone = [c.name for c in StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8")).characters]
    aliases = novel_dir / "bible_aliases.json"
    planner_ctx.aliases.update(json.loads(aliases.read_text(encoding="utf-8")) if aliases.is_file() else {})
    planner_context.load_entity_index(novel_dir, ctx=planner_ctx)
    wanted = set(parse_chapters(args.chapters)) if args.chapters else None

    # In-place rebuilding needs build_clip_plan_thin imported under the plan's clip length: one worker per length.
    if args.apply and not args.no_pack and not args.repack and args.mode is None:
        by_mode: dict[str, list[int]] = {}
        for index, script_path in episodes_of(novel_dir, wanted):
            by_mode.setdefault(plan_mode(script_path.parent), []).append(index)
        failed = False
        for mode, indexes in sorted(by_mode.items()):
            env = {**os.environ, "NOVEL_CLIP_SECONDS_MAX": mode}
            if mode == "30":
                env.pop("NOVEL_CLIP_SECONDS_MAX", None)
            command = [sys.executable, str((project_root() / 'scripts/complete_cast_thin.py')), "--novel-dir", str(novel_dir), "--apply", "--mode", mode,
                       "--chapters", ",".join(map(str, indexes))] + (["--dry-rebuild"] if args.dry_rebuild else []) + (["--rebuild-existing"] if args.rebuild_existing else [])
            print(f"[{mode} 秒档] {len(indexes)} 集", flush=True)
            result = subprocess.run(command, env=env, check=False)
            failed = failed or result.returncode != 0
        return 1 if failed else 0

    episodes = shots_changed = clips_changed = rebuilt = skipped = 0
    by_name: Counter = Counter()
    changed_episodes: list[int] = []
    for index, script_path in episodes_of(novel_dir, wanted):
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        original = copy.deepcopy(script)
        touched = 0
        for shot in script.get("shots", []):
            cast, added = pc_cast.complete_characters(list(shot.get("characters") or []), shot, everyone, ctx=planner_ctx)
            if added:
                shot["characters"] = cast
                touched += 1
                by_name.update(added)
        if not touched and not args.rebuild_existing:
            continue
        episodes += 1
        shots_changed += touched
        changed_episodes.append(index)
        if not args.apply:
            continue
        plan_path = script_path.parent / "clip_plan.json"
        old = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
        if args.dry_rebuild:
            merged, changed, why = rebuild_in_place(script_path.parent, bible_path, original, script, old) if old else (None, [], "no plan")
            if merged is None:
                skipped += 1
            else:
                rebuilt += 1
                clips_changed += len(changed)
            continue
        if not args.no_pack and not args.repack:
            # in place: nothing is written unless the rebuild succeeds
            if not old:
                skipped += 1
                print(f"  {script_path.parent.name}: 没有段计划，未动")
                continue
            merged, changed, why = rebuild_in_place(script_path.parent, bible_path, original, script, old)
            if merged is None:
                skipped += 1
                print(f"  {script_path.parent.name}: 保留分段失败（{why}），未动")
                continue
        if touched:
            backup = script_path.with_name("chapter_script.json.bak-cast")
            if not backup.exists():
                shutil.copy2(script_path, backup)
            script_path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.no_pack:
            continue
        if not args.repack:
            if changed:
                backup_plan = plan_path.with_name("clip_plan.json.bak-cast")
                if not backup_plan.exists():
                    shutil.copy2(plan_path, backup_plan)
                atomic_write_json(plan_path, merged)
            rebuilt += 1
            clips_changed += len(changed)
            continue
        if args.repack:
            env = {**os.environ}
            if plan_mode(script_path.parent) == "15":
                env["NOVEL_CLIP_SECONDS_MAX"] = "15"
            else:
                env.pop("NOVEL_CLIP_SECONDS_MAX", None)
            profile = (old.get("totals") or {}).get("profile") or {}
            options = [item for key in ("style", "frame", "tier") if profile.get(key) for item in ("--" + key, str(profile[key]))]
            result = subprocess.run([sys.executable, str(SCRIPTS / "build_clip_plan_thin.py"), "--episode-dir", str(script_path.parent),
                                     "--bible", str(bible_path), *options], cwd=SCRIPTS.parent, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                skipped += 1
                print(f"  {script_path.parent.name}: 重算段计划失败: {result.stderr.strip()[-300:]}")
                continue
            new = json.loads(plan_path.read_text(encoding="utf-8"))
            merged, changed = splice_plans(old, new)
            if merged is None:
                print(f"  {script_path.parent.name}: 分段变了（{len(old.get('clips', []))} → {len(new.get('clips', []))} 段），整集按新计划重渲")
                clips_changed += len(new.get("clips", []))
            else:
                atomic_write_json(plan_path, merged)
                clips_changed += len(changed)
            rebuilt += 1
            continue

    label = f"[{args.mode} 秒档] " if args.mode else ""
    print(f"{label}{novel_dir.name}: 检查 {episodes} 集 / {shots_changed} 镜头需补演员"
          + (f"；{'预检' if args.dry_rebuild else '处理'}段计划 {rebuilt} 集，需更新的段 {clips_changed}，分段对不上跳过 {skipped} 集"
             if args.apply else "（预演，加 --apply 才写）"))
    print("补上最多的角色:", by_name.most_common(10))
    targets = novel_dir / "cast_completion_targets.txt"
    known = set()
    if args.mode and targets.is_file():
        known = {int(x) for x in targets.read_text(encoding="utf-8").split(",") if x.strip()}
    if args.apply and not args.dry_rebuild:
        targets.write_text(",".join(map(str, sorted(known | set(changed_episodes)))), encoding="utf-8")
        print("涉及集号记在", targets)
    return 1 if skipped else 0
