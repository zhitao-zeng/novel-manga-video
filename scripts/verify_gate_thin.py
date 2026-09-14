#!/usr/bin/env python
"""The verification gate: only clips the frame-level verifier calls obvious stay must_fix.

    verify_gate_thin.py --novel-dir outputs/X --episodes 1-2043 [--apply] [--verify-file <novel>/verify/verify.jsonl]

For every must_fix clip of the episodes: a verification of the very same take is looked up (verify_clips_thin
records); a clip without one is verified now.  obvious -> stays must_fix (annotated `verified`); anything else ->
tier optional, its retake instruction moved from `feedback` to `feedback_cleared`, so apply_review_feedback and
repair_clips_thin no longer touch it.  A clip the judge passed but the verifier saw an obvious error in (sample or
all mode) is promoted: must_fix, story_ok false, story_issue and feedback from the evidence.  A must_fix carrying
`technical` (black frames) or written by the verify-mode judge itself (`verify`) is never cleared here.
episode_review.json is backed up once as episode_review.json.bak-gate before the first change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from novel_manga.util import atomic_write_json  # noqa: E402
from verify_clips_thin import Verifier, parse_episodes  # noqa: E402
import thin_review as tr  # noqa: E402


def instruction_for_clip(ep_dir, cid, rec):
    """The retake note for a clip the verifier confirmed: its own instruction, else one composed from the evidence."""
    note = str(rec.get("instruction") or "").strip()
    if note:
        return note
    plan = v.load(ep_dir / "clip_plan.json") or {}
    clip = next((c for c in plan.get("clips", []) if c.get("clip_id") == cid), None) or {}
    segments = tr.segment_texts(ep_dir)
    passage = "\n".join(str(segments.get(str(x), "")) for x in (clip.get("segment_ids") or []))
    return tr.instruction_for(str(rec.get("evidence") or ""), tr.scripted_event(clip) if clip else "", passage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-file", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    out = args.verify_file or (args.novel_dir / "verify" / "verify.jsonl")
    v = Verifier(args.novel_dir, out, "local", args.workers)
    eps = sorted(parse_episodes(args.episodes))
    latest: dict = {}
    try:
        for line in out.open(encoding="utf-8"):
            d = json.loads(line)
            if "error" not in d:
                latest[(d["ep"], d["clip"], d["video"], json.dumps(d["take"]))] = d
    except FileNotFoundError:
        pass

    def key_of(n: int, cid: str, clip_verdict: dict, ep_dir: Path):
        video = v.video_of(ep_dir, cid, clip_verdict)
        return None if video is None else (n, cid, str(video), json.dumps(tr.take_identity(video)))

    jobs = []
    for n in eps:
        ep_dir = v.episode_dir(n)
        review = v.load(ep_dir / "episode_review.json") or {}
        for cid, c in (review.get("clips") or {}).items():
            if (c.get("tier") or c.get("fix_tier")) != "must_fix" or c.get("technical") or c.get("verify"):
                continue
            k = key_of(n, cid, c, ep_dir)
            if k is not None and k not in latest:
                claim = "；".join(s for s in (c.get("story_issue"), c.get("identity_issue") if c.get("identity_ok") is False else "", c.get("defect_issue")) if s)
                jobs.append((n, cid, claim, "candidate"))
    if jobs:
        for r in v.run_jobs(jobs, "gate"):
            if "error" not in r:
                latest[(r["ep"], r["clip"], r["video"], json.dumps(r["take"]))] = r
    kept = cleared = promoted = unknown = 0
    for n in eps:
        ep_dir = v.episode_dir(n)
        path = ep_dir / "episode_review.json"
        review = v.load(path) or {}
        changed = False
        for cid, c in (review.get("clips") or {}).items():
            must = (c.get("tier") or c.get("fix_tier")) == "must_fix"
            if must and (c.get("technical") or c.get("verify")):
                kept += 1
                continue
            k = key_of(n, cid, c, ep_dir)
            rec = latest.get(k) if k else None
            if rec is None:
                unknown += must
                continue
            note = {"verdict": rec["verdict"], "evidence": str(rec.get("evidence", ""))[:200], "ts": rec.get("ts")}
            if must and rec["verdict"] == "obvious":
                kept += 1
                if c.get("verified") != note:
                    c["verified"] = note
                    changed = True
                instr = instruction_for_clip(ep_dir, cid, rec)
                if instr and c.get("feedback") != instr:
                    c["feedback"] = instr
                    review.setdefault("feedback", {})[cid] = instr
                    changed = True
            elif must:
                cleared += 1
                for field in ("tier", "fix_tier"):
                    if c.get(field) == "must_fix":
                        c[field] = "optional"
                c["verified"] = note
                feedback = review.get("feedback") or {}
                if cid in feedback:
                    review.setdefault("feedback_cleared", {})[cid] = feedback.pop(cid)
                changed = True
            elif rec["verdict"] == "obvious":
                promoted += 1
                c["tier"] = "must_fix"
                c["story_ok"] = False
                c["story_kind"] = "原文中有动作的人物缺席" if rec.get("actor_missing") else "动作落在错误的人物身上"
                c["story_issue"] = str(rec.get("evidence", ""))[:300]
                c["severity"] = "fail"
                c["verified"] = {**note, "promoted": True}
                c["feedback"] = instruction_for_clip(ep_dir, cid, rec) or ("按原文修正剧情：" + str(rec.get("evidence", ""))[:200])
                review.setdefault("feedback", {})[cid] = c["feedback"]
                changed = True
        if changed and args.apply:
            backup = path.with_name("episode_review.json.bak-gate")
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            atomic_write_json(path, review)
    print(f"gate: kept {kept}, cleared {cleared}, promoted {promoted}, unverifiable {unknown}{'' if args.apply else ' (dry run)'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
