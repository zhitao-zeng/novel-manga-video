#!/usr/bin/env python
"""One verdict per episode: is it deliverable, and if not, which gate stops it.

    delivery_gate_thin.py --novel-dir outputs/X [--recent-min 3] [--quiet]

Three gates.  An episode is deliverable when it passes the first two; the third is reported
but does not block (shadow mode) until its false-positive rate has been measured.

    技术  thin_runs.episode_status() == "done": the final exists, the media checks passed and
          no clip failed the speech gate.  What thin_batch and the conductor already act on.
    审查  the latest episode_review.json is newer than the final, covers every video clip of
          the current plan, and carries no retake instruction.  thin_review writes `feedback`
          only for must_fix verdicts, so "no feedback" is "nothing a viewer would notice".
          (2026-09-12: 雾月 had 630 such episodes and no review_feedback.json anywhere - the
          instructions were computed and never applied; this gate makes that visible.)
    剧本  no cast member of the clip plan is absent from the chapter text under every surface
          form (canonical name, the part before the dot, every alias).  Measured with the
          bible's own alias table; names with no surface form of two or more characters
          (雾月's "神") cannot be measured and are skipped, not counted.

Writes outputs/X/delivery.json - the counts, the reason combinations and one row per
episode - for the status board and for anyone asking "is this novel done".
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from thin_runs import REVIEW_POLICY, episode_status, gate_failures, render_runs  # noqa: E402

POLICY = "delivery-gate-v1"


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def lane_is_h3(novel_id: str) -> bool:
    """The board's rule; an H3 lane changes what "stale" means in episode_status."""
    try:
        import status_server  # noqa: E402 - guarded: the module has a __main__ block

        keys = status_server._lane_keys().get(novel_id, [])
        return bool(keys) and all(k.get("base_url") for k in keys)
    except Exception:  # noqa: BLE001 - the board is optional here
        return True


def chapter_texts(novel_dir: Path) -> dict[int, str]:
    """Chapter number -> source text, located by the titles novel.json recorded."""
    meta = load(novel_dir / "novel.json") or {}
    source = Path(meta.get("source", ""))
    if not source.is_file():
        source = ROOT / "inputs" / source.name
    if not source.is_file():
        return {}
    text = source.read_text(encoding="utf-8", errors="replace")
    marks, cursor = [], 0
    for entry in meta.get("chapters") or []:
        title = str(entry.get("title") or "")
        at = text.find(title, cursor) if title else -1
        if at < 0:
            continue
        marks.append((int(entry["index"]), at))
        cursor = at + len(title)
    return {i: text[a: marks[k + 1][1] if k + 1 < len(marks) else len(text)] for k, (i, a) in enumerate(marks)}


def surface_forms(novel_dir: Path) -> dict[str, set[str]]:
    aliases = load(novel_dir / "bible_aliases.json") or {}
    bible = load(novel_dir / "story_bible.json") or {}
    forms: dict[str, set[str]] = {}
    for character in bible.get("characters", []):
        name = str(character.get("name", "")).strip()
        if not name:
            continue
        if len(name) < 2:
            forms[name] = set()  # 雾月's "神", 诸天's "龙": the prose can use the bare character, which matches everywhere
            continue
        candidates = {name, name.split("·")[0]} | {a for a, canon in aliases.items() if canon == name}
        forms[name] = {f for f in candidates if len(f) >= 2}
    return forms


def cast_of(plan: dict) -> set[str]:
    names = set()
    for clip in plan.get("clips") or []:
        value = clip.get("cast") or clip.get("clip_cast") or []
        if isinstance(value, list):
            names.update(item if isinstance(item, str) else str(item.get("name", item)) for item in value)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--recent-min", type=int, default=3,
                        help="on screen in at least this many episodes = a real recurring character for the script gate")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    novel_id = novel_dir.name
    h3_lane = lane_is_h3(novel_id)

    dirs = sorted((d for d in novel_dir.iterdir() if d.is_dir() and d.name.startswith(novel_id + "_")
                   and d.name.rsplit("_", 1)[-1].isdigit()), key=lambda d: int(d.name.rsplit("_", 1)[-1]))
    plans = {}
    onscreen: Counter = Counter()
    for d in dirs:
        plan = load(d / "clip_plan.json")
        if plan is not None:
            n = int(d.name.rsplit("_", 1)[-1])
            plans[n] = (plan, cast_of(plan))
            onscreen.update(plans[n][1])
    forms = surface_forms(novel_dir)
    real = {name for name, count in onscreen.items() if count >= args.recent_min} & set(forms)
    measurable = {name for name in real if forms[name]}
    chapters = chapter_texts(novel_dir)
    script_gate_available = len(chapters) >= 50

    rows = []
    for d in dirs:
        n = int(d.name.rsplit("_", 1)[-1])
        final = d / f"{d.name}.mp4"
        try:
            status = episode_status(d, h3_lane)
        except Exception:  # noqa: BLE001 - a broken state file is a finding, not a crash
            status = "unreadable"
        plan, cast = plans.get(n, ({}, set()))

        review = load(d / "episode_review.json") or {}
        clips = review.get("clips") or {}
        feedback = review.get("feedback") or {}
        expected = {c.get("clip_id") for c in plan.get("clips", []) if c.get("kind") == "video"}
        review_state = "not_ready"
        if status in {"done", "done_with_warnings"} and final.is_file():
            review_state = "pending"
            if review.get("policy") == REVIEW_POLICY and mtime(d / "episode_review.json") >= mtime(final):
                if any(c.get("severity") == "review_error" for c in clips.values() if isinstance(c, dict)):
                    review_state = "error"
                elif expected and all(clips.get(cid, {}).get("severity") in {"pass", "minor", "fail"} for cid in expected):
                    review_state = "reviewed"

        body = chapters.get(n, "")
        ghosts = sorted(name for name in cast & measurable if body and not any(f in body for f in forms[name]))

        tech = status == "done"
        from repair_delivery_thin import publication_pending
        reviewed_clean = review_state == "reviewed" and not feedback and not publication_pending(d)
        script_ok = not ghosts
        why = [w for w, bad in (("技术", not tech), ("审查", not reviewed_clean)) if bad]
        rows.append({
            "episode": n, "status": status, "runs": render_runs(d), "gate_failed_clips": len(gate_failures(d)),
            "review": review_state, "must_fix_clips": len(feedback), "ghosts": ghosts,
            "tech": tech, "review_ok": reviewed_clean, "script_ok": script_ok,
            "deliverable": tech and reviewed_clean, "why": " ".join(why),
        })

    total = len(rows)
    deliverable = sum(r["deliverable"] for r in rows)
    reasons = Counter(r["why"] for r in rows if not r["deliverable"])
    ghost_names: Counter = Counter()
    for r in rows:
        ghost_names.update(r["ghosts"])
    summary = {
        "policy": POLICY, "review_policy": REVIEW_POLICY, "novel": novel_id, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "h3_lane": h3_lane, "total": total, "deliverable": deliverable,
        "gates": {
            "tech": {"blocked": sum(not r["tech"] for r in rows),
                     "statuses": dict(Counter(r["status"] for r in rows))},
            "review": {"blocked": sum(r["tech"] and not r["review_ok"] for r in rows),
                       "with_must_fix": sum(bool(r["must_fix_clips"]) for r in rows),
                       "must_fix_clips": sum(r["must_fix_clips"] for r in rows),
                       "states": dict(Counter(r["review"] for r in rows))},
            "script": {"shadow": True, "available": script_gate_available,
                       "flagged": sum(bool(r["ghosts"]) for r in rows),
                       "would_block": sum(r["deliverable"] and not r["script_ok"] for r in rows),
                       "unmeasurable_names": sorted(real - measurable),
                       "top_ghosts": ghost_names.most_common(8)},
        },
        "reasons": dict(reasons.most_common()),
        "episodes": rows,
    }
    (novel_dir / "delivery.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        g = summary["gates"]
        print(f"{novel_id}: 可交付 {deliverable}/{total} ({100 * deliverable / max(total, 1):.1f}%)  "
              f"技术挡 {g['tech']['blocked']} · 审查挡 {g['review']['blocked']}（{g['review']['must_fix_clips']} 段 must_fix）"
              f" · 剧本影子门标 {g['script']['flagged']}（其中会再挡 {g['script']['would_block']}）")
        print(f"  技术状态 {g['tech']['statuses']}")
        print(f"  审查状态 {g['review']['states']}")
        print(f"  未交付原因 {summary['reasons']}")
        if not script_gate_available:
            print("  剧本门未测：novel.json 定位不到章节文本")
        elif g["script"]["top_ghosts"]:
            print(f"  剧本影子门榜首 {g['script']['top_ghosts'][:5]}；不可测名字 {g['script']['unmeasurable_names']}")
        print(f"  写入 {novel_dir / 'delivery.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
