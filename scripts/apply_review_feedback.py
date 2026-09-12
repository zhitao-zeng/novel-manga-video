#!/usr/bin/env python
"""Hand the automatic review's retake instructions to the lanes, without rendering anything here.

    apply_review_feedback.py --novel-dir outputs/X [--episodes 12,48-60] [--pick N] [--gate] [--cards character_006,...] [--apply]

thin_review writes a must_fix clip's instruction into episode_review.json `feedback`; the only thing a lane
acts on is review_feedback.json, and thin_batch writes that file only in --unattended mode, where it also
renders on the spot.  The conductor's review jobs run --review-only --no-render, so on 雾月 630 episodes
carried 848 instructions that nothing ever applied.  This writes the file and stops: the episode turns
`stale` (thin_runs.episode_status compares the file with the stamp in thin_media_report.json), a lane
re-renders it, and only the corrected clips regenerate - the correction changes their prompt, so their
request hash, while every other clip is served from the cache.  A new correction also resets the render
count, so an episode that used its runs gets them back for exactly this.

--gate adds the standing instruction for clips that failed the speech gate (thin_media_report
gate_failed_clips) so a lane takes them again after their runs were used up.
--pick N chooses N episodes spread over the defect kinds (a pilot to measure the repair rate before the
whole book goes); --episodes names them.  Nothing is written without --apply.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_runs import episode_status, gate_failures  # noqa: E402

GATE_NOTE = "台词必须完整、清晰地说出来，与给定台词逐字一致；说话时口型可见，不得含混、吞字或用旁白代替"
KINDS = (
    ("双人", re.compile(r"分身|克隆|重复出现|长相.{0,6}(一致|相似|一样)|同一张脸|高度相似|多出.{0,6}(莱恩|一名|一个|一位)")),
    ("崩坏", re.compile(r"崩坏|手指|穿模|无身体|悬浮的.{0,4}头|五官|畸形|多余的(手|臂|腿)")),
    ("缺席", re.compile(r"完全缺失|未出现在|未出场|没有出现在|缺失了")),
)


def parse_episodes(spec: str) -> set[int]:
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


def kind_of(text: str) -> str:
    for name, pattern in KINDS:
        if pattern.search(text):
            return name
    return "其它"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", default="", help="only these episodes, e.g. 12,48-60")
    parser.add_argument("--pick", type=int, default=0, help="pilot: this many episodes spread over the defect kinds")
    parser.add_argument("--gate", action="store_true", help="also instruct clips that failed the speech gate")
    parser.add_argument("--cards", default="", help="asset ids whose card was replaced, e.g. character_006: every clip that references one and whose review says identity_ok=false is instructed too (the reviewer's own sentence)")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    novel_id = novel_dir.name
    only = parse_episodes(args.episodes) if args.episodes else None
    cards = {c.strip() for c in args.cards.split(",") if c.strip()}
    card_names = set()
    for asset_id in cards:  # the replaced card's character: only an identity issue that names them counts
        try:
            card_names.add(str(json.loads((novel_dir / "series_assets" / "characters" / asset_id / "spec.json").read_text(encoding="utf-8")).get("name", "")))
        except (OSError, ValueError):
            pass

    candidates = []  # (episode number, directory, {clip: note}, kinds)
    for d in sorted(novel_dir.glob(f"{novel_id}_*"), key=lambda p: int(p.name.rsplit("_", 1)[-1]) if p.name.rsplit("_", 1)[-1].isdigit() else 0):
        tail = d.name.rsplit("_", 1)[-1]
        if not tail.isdigit():
            continue
        n = int(tail)
        if only is not None and n not in only:
            continue
        try:
            review = json.loads((d / "episode_review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            review = {}
        notes = {cid: str(text) for cid, text in (review.get("feedback") or {}).items() if text}
        if args.gate:
            for cid in gate_failures(d):
                notes.setdefault(cid, GATE_NOTE)
        if cards:
            try:
                plan = json.loads((d / "clip_plan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                plan = {}
            for clip in plan.get("clips") or []:
                used = {ref.get("asset_id") for ref in clip.get("references") or [] if ref.get("role") == "character"}
                verdict = (review.get("clips") or {}).get(clip.get("clip_id"), {})
                issue = str(verdict.get("identity_issue") or "")
                if used & cards and verdict.get("identity_ok") is False and any(name in issue for name in card_names):
                    notes.setdefault(clip["clip_id"], str(verdict.get("feedback") or issue or "每个角色必须与其角色卡一致"))
        try:
            existing = json.loads((d / "review_feedback.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        fresh = {cid: note for cid, note in notes.items() if existing.get(cid) != note}
        if not fresh:
            continue
        kinds = {kind_of((review.get("clips") or {}).get(cid, {}).get("identity_issue", "") + " " +
                         (review.get("clips") or {}).get(cid, {}).get("defect_issue", "") + " " + note) for cid, note in fresh.items()}
        candidates.append((n, d, fresh, kinds, existing))

    chosen = candidates
    if args.pick:
        random.seed(args.seed)
        by_kind: dict[str, list] = defaultdict(list)
        for item in candidates:
            for kind in item[3]:
                by_kind[kind].append(item)
        picked: list = []
        seen: set[int] = set()
        order = [name for name, _ in KINDS] + ["其它"]
        while len(picked) < args.pick and any(by_kind.values()):
            for kind in order:
                pool = [item for item in by_kind.get(kind, []) if item[0] not in seen]
                if not pool or len(picked) >= args.pick:
                    continue
                item = random.choice(pool)
                picked.append(item)
                seen.add(item[0])
        chosen = sorted(picked, key=lambda item: item[0])

    clips = sum(len(item[2]) for item in chosen)
    kinds_total: dict[str, int] = defaultdict(int)
    for item in chosen:
        for kind in item[3]:
            kinds_total[kind] += 1
    print(f"{novel_id}: {len(candidates)} 集有未落盘的重拍指令；{'选出' if args.pick else '处理'} {len(chosen)} 集 / {clips} 段；"
          f"按类型（集，可重叠）{dict(kinds_total)}")
    for n, d, fresh, kinds, _ in chosen[:30]:
        print(f"  {d.name}: {', '.join(sorted(fresh))}  [{'/'.join(sorted(kinds))}]  status={episode_status(d, True)}")
    if not args.apply:
        print("（预演，加 --apply 才写 review_feedback.json）")
        return 0
    for n, d, fresh, _, existing in chosen:
        (d / "review_feedback.json").write_text(json.dumps({**existing, **fresh}, ensure_ascii=False, indent=1), encoding="utf-8")
    numbers = ",".join(str(item[0]) for item in chosen)
    (novel_dir / "repair_targets.txt").write_text(numbers, encoding="utf-8")
    print(f"已写 {len(chosen)} 个 review_feedback.json；集号记在 {novel_dir / 'repair_targets.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
