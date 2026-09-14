#!/usr/bin/env python
"""Repair only the clips the story judge failed, keeping every other clip's request (and cached render) intact.

    repair_clips_thin.py --novel-dir outputs/X [--episodes 12,48-60 | --targets] [--workers 4] [--apply]

Re-planning a chapter under the storyboard contract rewrites every stage, so every clip re-renders; the
pilot showed that (1,041 clips, 768 "request changed", the rest new cuts).  A failed clip does not need the
chapter re-planned: its own stages need the contract - who is in frame, who does what to whom, which unnamed
extra is there - written with the judge's complaint, the passage and the ledger's casting snapshot in view.
One small model call per failed clip does that; the storyboard shots of that clip are updated in place
(cuts unchanged), the clip is rebuilt from its recorded shot indexes, and only its request changes.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
os.environ.setdefault("SECOND_REVIEW_JUDGE", "local")
import second_review  # noqa: E402,F401
from novel_manga.util import atomic_write_json  # noqa: E402
from plan_chapter_thin import ledger_cast, ledger_snapshot_for  # noqa: E402
from thin_review import ask_json  # noqa: E402

LANE_FIELDS = ("prompt_h3", "prompt_h3_of")
RULES = (
    "你在修一段动画短剧的分镜。判官对照原文发现这段画面把动作或台词安错了人，或漏了人。下面给你：这段原文、现有分镜的各阶段、"
    "原著账本记的这段谁在场、判官的意见。只输出 JSON。对每个阶段（按 origin_index）重写：\n"
    "in_frame：这一阶段画面里真正出现的具名人物（只能从候选名单选；原文里只被提起、在别处、或只有声音的人不进）。\n"
    "actions：这一阶段谁对谁做了什么，actor/target 是 in_frame 里的名字，action 是谓语短语（如“环住脖子吻住”“递过信封”），没有动作就空数组。\n"
    "extras：原文里在场、有动作或台词、但不在候选名单里的无名人物，用不超过 12 字的外貌描述（如“戴眼镜的灰发老妇人”），没有就空数组。\n"
    "event：改写后的事件句，一句话写清谁做什么，先写动作再写其余。\n"
    "有可见说话者的阶段，in_frame 只放说话的人和这一阶段与他有动作往来的人（听的人不进）。判官说缺席的人若原文这段确实在场，必须进 in_frame。"
)


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def failing_clips(review: dict) -> dict[str, str]:
    return {k: str(v.get("story_issue") or v.get("feedback") or "") for k, v in (review.get("clips") or {}).items()
            if (v.get("tier") or v.get("fix_tier")) == "must_fix" and v.get("story_ok") is False}


def schema_for(names: list[str], indexes: list[int]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["stages"], "properties": {"stages": {
        "type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                                                                  "required": ["origin_index", "in_frame", "actions", "extras", "event"],
                                                                  "properties": {
                                                                      "origin_index": {"type": "integer", "enum": indexes},
                                                                      "in_frame": {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": names}},
                                                                      "actions": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
                                                                                                                            "required": ["actor", "action", "target"],
                                                                                                                            "properties": {"actor": {"type": "string", "enum": [*names, ""]},
                                                                                                                                           "action": {"type": "string"},
                                                                                                                                           "target": {"type": "string", "enum": [*names, ""]}}}},
                                                                      "extras": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                                                                      "event": {"type": "string"}}}}}}


def stage_view(shot: dict) -> str:
    spoken = [f"{t.get('speaker_name') or '无名'}{'（画外）' if t.get('delivery_mode') == 'offscreen_dialogue' else ''}：{str(t.get('text') or '')[:40]}"
              for t in shot.get("turns") or [] if t.get("delivery_mode") in ("visible_dialogue", "offscreen_dialogue") and t.get("text")]
    return (f"阶段 {shot['origin_index']}：画面「{str(shot.get('visual_prompt') or '')[:120]}」 事件「{str(shot.get('motion_prompt') or '')[:160]}」 "
            f"现有人物名单 {shot.get('characters')}" + (f" 台词：{'；'.join(spoken)[:200]}" if spoken else ""))


def repair_prompt(passage: str, shots: list[dict], snapshot: dict, issue: str, names: list[str]) -> str:
    cast = "、".join(f"{c['name']}（{c['presence']}）" for c in snapshot.get("chapter_cast", [])) or "（账本没读这一章）"
    named = {seg: v.get("named_here", []) for seg, v in (snapshot.get("segments") or {}).items()}
    return (RULES + f"\n\n候选名单：{'、'.join(names)}\n账本记的本章在场情况：{cast}\n这段原文里点到名的人：{named}\n"
            f"判官意见：{issue}\n\n原文：\n{passage[:2500]}\n\n现有分镜：\n" + "\n".join(stage_view(s) for s in shots))


def apply_stage(shot: dict, fix: dict, names: list[str]) -> None:
    in_frame = [n for n in fix.get("in_frame") or [] if n in names]
    actions = [{"actor": a["actor"], "action": str(a.get("action") or "").strip()[:40], "target": a.get("target") or ""}
               for a in fix.get("actions") or [] if a.get("actor") in names and str(a.get("action") or "").strip()]
    for a in actions:
        for who in (a["actor"], a["target"]):
            if who and who in names and who not in in_frame:
                in_frame.append(who)
    speakers = [t.get("speaker_name") for t in shot.get("turns") or [] if t.get("delivery_mode") == "visible_dialogue" and t.get("speaker_name") in names]
    listeners: list[str] = []
    if len(set(speakers)) == 1 and in_frame:
        speaker = speakers[0]
        acting = {a["actor"] for a in actions} | {a["target"] for a in actions if a["target"]}
        keep = [c for c in in_frame if c == speaker or c in acting]
        if keep:
            listeners = [c for c in in_frame if c not in keep]
            in_frame = keep
    line = "；".join(f"{a['actor']}{a['action']}{a['target']}" for a in actions)
    event = str(fix.get("event") or shot.get("motion_prompt") or "").strip()
    shot["characters"] = in_frame or shot.get("characters") or []
    shot["motion_prompt"] = (f"{line}。{event}" if line and line not in event else event) or shot.get("motion_prompt", "")
    shot["actions"] = actions
    shot["extras"] = [str(e).strip()[:24] for e in fix.get("extras") or [] if str(e).strip()][:3]
    shot["listeners"] = listeners


REBUILD_LOCK = threading.Lock()  # the packer's per-plan limits and name tables are module state


def rebuild_clips(episode_dir: Path, bible_path: Path, script: dict, plan: dict, clip_ids: set[str]) -> tuple[dict, list[str]]:
    """Rebuild only the named clips from their recorded shot indexes; every other clip keeps its entry (and request)."""
    import build_clip_plan_thin as bcp
    with REBUILD_LOCK:
        ctx = bcp.context_for_plan(episode_dir, bible_path, plan)
        shots = bcp.prepared_shots(copy.deepcopy(script), episode_dir)
        # Each clip recovers its own stage parts: a rewritten stage that no longer splits the way the plan
        # recorded (雾月 batch 2: "stage 13 has 1 parts, plan requires part 1/2") leaves that clip as it was
        # instead of failing the episode - and, before this, the whole batch.
        merged, changed, skipped = [], [], []
        for before in plan.get("clips") or []:
            if before["clip_id"] not in clip_ids or before.get("kind") != "video":
                merged.append(before)
                continue
            try:
                pieces = bcp.shots_for_plan({**plan, "clips": [before]}, shots).get(before["clip_id"], [])
            except ValueError as error:
                skipped.append(f"{before['clip_id']}: {str(error)[:80]}")
                pieces = []
            if not pieces:
                merged.append(before)
                continue
            clip = {"kind": "video", "location": before.get("location") or pieces[0]["location"], "shots": pieces,
                    "seconds": round(sum(bcp.shot_seconds(p) for p in pieces), 2)}
            after = bcp.clip_entry(clip, before["clip_id"], ctx)
            merged.append({k: v for k, v in after.items() if k not in LANE_FIELDS})
            changed.append(before["clip_id"])
        if skipped:
            print("  left as is (stage parts no longer match the plan): " + "; ".join(skipped), flush=True)
        return {**plan, "clips": merged}, changed


def repair_episode(novel_dir: Path, index: int, apply: bool) -> dict:
    episode_dir = novel_dir / f"{novel_dir.name}_{index}"
    review = read(episode_dir / "episode_review.json", {})
    failing = failing_clips(review)
    plan = read(episode_dir / "clip_plan.json", None)
    script = read(episode_dir / "chapter_script.json", None)
    segments = {str(s.get("segment_id")): str(s.get("text") or "") for s in read(episode_dir / "segments.json", [])}
    if not failing or not plan or not script:
        return {"episode": index, "clips": 0, "why": "nothing to repair" if not failing else "no plan/script"}
    bible = read(novel_dir / "story_bible.json", {})
    bible_names = [c["name"] for c in bible.get("characters", []) if c.get("name")]
    cast_here = ledger_cast(novel_dir, index)
    present = [n for n in bible_names if cast_here.get(n) in ("on_stage", "voice")]
    in_script = [n for s in script.get("shots", []) for n in s.get("characters", [])]
    leads = [c["name"] for c in bible.get("characters", []) if "主角" in str(c.get("role", ""))]
    names = list(dict.fromkeys([*leads, *present, *in_script]))[:40] or bible_names[:12]
    by_index = {int(s["origin_index"]): s for s in script.get("shots", []) if "origin_index" in s}
    seg_rows = [{"segment_id": k, "text": v} for k, v in segments.items()]
    snapshot = ledger_snapshot_for(novel_dir, index, seg_rows, cast_here, names) if cast_here else {}
    repaired, notes = [], []
    for clip in plan.get("clips") or []:
        cid = clip.get("clip_id")
        if cid not in failing:
            continue
        indexes = [i for i in dict.fromkeys(clip.get("shot_indexes") or []) if i in by_index]
        if not indexes:
            notes.append(f"{cid}: no shot indexes")
            continue
        passage = "\n".join(segments.get(str(s), "") for s in clip.get("segment_ids") or [])
        shots = [by_index[i] for i in indexes]
        try:
            answer = ask_json([{"type": "text", "text": repair_prompt(passage, shots, snapshot, failing[cid], names)}],
                              schema_for(names, indexes), name="clip_repair", max_tokens=1500)
        except Exception as error:  # noqa: BLE001
            notes.append(f"{cid}: model {type(error).__name__}: {str(error)[:80]}")
            continue
        fixes = {int(f["origin_index"]): f for f in answer.get("stages") or [] if int(f.get("origin_index", -1)) in by_index}
        if not fixes:
            notes.append(f"{cid}: empty answer")
            continue
        for i, fix in fixes.items():
            apply_stage(by_index[i], fix, names)
        repaired.append(cid)
    if not repaired:
        return {"episode": index, "clips": 0, "why": "; ".join(notes)[:160]}
    try:
        new_plan, changed = rebuild_clips(episode_dir, novel_dir / "story_bible.json", script, plan, set(repaired))
    except Exception as error:  # noqa: BLE001 - one episode's rebuild must not take the batch down
        return {"episode": index, "clips": 0, "failing": len(failing), "why": f"rebuild failed: {type(error).__name__}: {str(error)[:100]}"}
    if apply and changed:
        for name in ("chapter_script.json", "clip_plan.json"):
            backup = episode_dir / f"{name}.bak-repair-0914"
            if not backup.is_file():
                backup.write_text((episode_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
        (episode_dir / "chapter_script.json").write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        atomic_write_json(episode_dir / "clip_plan.json", new_plan)
    return {"episode": index, "clips": len(changed), "failing": len(failing), "why": "; ".join(notes)[:120], "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", default="", help="e.g. 12,48-60; default: every episode whose latest review has story-class must_fix clips")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    if args.episodes:
        wanted: list[int] = []
        for part in args.episodes.split(","):
            a, _, b = part.strip().partition("-")
            if a:
                wanted.extend(range(int(a), int(b or a) + 1))
    else:
        wanted = [int(p.parent.name.rsplit("_", 1)[-1]) for p in novel_dir.glob(f"{novel_dir.name}_*/episode_review.json")
                  if failing_clips(read(p, {}))]
        wanted.sort()
    started = time.time()
    print(f"{novel_dir.name}: {len(wanted)} 集有剧情类必修段，{args.workers} 路修段{'' if args.apply else '（不写入）'}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        def one(n: int) -> dict:
            try:
                return repair_episode(novel_dir, n, args.apply)
            except Exception as error:  # noqa: BLE001 - reported per episode, never the whole batch
                return {"episode": n, "clips": 0, "why": f"{type(error).__name__}: {str(error)[:120]}"}
        results = list(pool.map(one, wanted))
    clips = sum(r.get("clips", 0) for r in results)
    failing = sum(r.get("failing", 0) for r in results)
    for r in results:
        if r.get("clips") or r.get("why"):
            print(f"  {novel_dir.name}_{r['episode']}: 修 {r.get('clips', 0)}/{r.get('failing', '?')} 段 {r.get('why', '')}", flush=True)
    print(f"REPAIR RESULT: {len(wanted)} episodes, {clips} clips repaired of {failing} failing, {(time.time() - started) / 60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
