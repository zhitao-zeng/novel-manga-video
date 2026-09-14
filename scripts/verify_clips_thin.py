#!/usr/bin/env python
"""Frame-level verification of rendered clips against the book: the "would a viewer notice" standard.

    verify_clips_thin.py --novel-dir outputs/X --mode candidates|sample|all|replay:<file>[:<mode>] [--sample 300]
                         [--workers 6] [--out <novel>/verify/verify.jsonl] [--judge-tag local] [--skip-episodes a,b]

Per clip: 5-6 frames + up to three cards + the passage, the planner's event line, the ledger's casting sheet and
(for candidates) the first judge's claim -> the model describes every person it sees, then answers five yes/no
questions (same person twice, species or gender wrong, action by the wrong person, an actor missing, a lead's
face swapped) and a verdict: obvious (a viewer who never saw the cards would notice), subtle (only a card
comparison shows it), fine.  Ghost text is recorded apart and does not drive the verdict.

Modes: candidates = the review's must_fix clips (with the judge's claim); sample = N random clips the review
passed (leak estimate); all = every clip with a take, except the candidates; replay = the clips another judge
verified (calibration).  Records are keyed by (episode, clip, video file): a rerun only verifies new takes.
The judge model is QWEN38_LOCAL_* from the environment, re-applied before every call because an imported
module rewrites those variables for the process.  雾月 2026-09-14: the judge confirmed on half of its must_fix
flags and missed 4% of what it passed; this is what made the final fix list.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import thin_review as tr  # noqa: E402
from novel_manga.models import StoryBible  # noqa: E402

SCHEMA = tr.VERIFY_SCHEMA
JUDGE_KEYS = ("QWEN38_LOCAL_BASE_URL", "QWEN38_LOCAL_MODEL", "QWEN38_LOCAL_API_KEY_VAR", "QWEN38_LOCAL_STREAM")


class Verifier:
    def __init__(self, novel_dir: Path, out: Path, judge_tag: str, workers: int):
        self.novel = novel_dir.resolve()
        self.prefix = self.novel.name
        self.out = out
        self.frames = out.parent / "frames"
        self.judge_tag = judge_tag
        self.workers = workers
        self.judge_env = {k: os.environ[k] for k in JUDGE_KEYS if k in os.environ}
        tr.apply_genre_review_rules(self.novel)
        self.bible = StoryBible.model_validate_json((self.novel / "story_bible.json").read_text(encoding="utf-8"))
        self.phases = tr.load_phases(self.novel)
        grammar = self.novel / "visual_grammar.json"
        self.location_time = json.loads(grammar.read_text(encoding="utf-8")).get("location_time", {}) if grammar.is_file() else {}
        self._segments: dict = {}
        self.lock = threading.Lock()

    # ---- files ----
    def episodes(self) -> list[int]:
        return sorted(int(d.name.rsplit("_", 1)[-1]) for d in self.novel.glob(f"{self.prefix}_*") if d.name.rsplit("_", 1)[-1].isdigit())

    def episode_dir(self, n: int) -> Path:
        return self.novel / f"{self.prefix}_{n}"

    @staticmethod
    def load(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def video_of(self, ep_dir: Path, cid: str, review_clip: dict | None) -> Path | None:
        recorded = (review_clip or {}).get("video")
        if recorded:
            p = Path(recorded)
            p = p if p.is_absolute() else ROOT / p
            if p.is_file():
                return p
        attempts = sorted((ep_dir / "work" / "clips" / cid).glob("attempt_*"))
        return next((a / "clip.mp4" for a in reversed(attempts) if (a / "clip.mp4").is_file()), None)

    def done_keys(self) -> set:
        done = set()
        try:
            for line in self.out.open(encoding="utf-8"):
                d = json.loads(line)
                if "error" not in d:
                    done.add((d["ep"], d["clip"], d.get("video"), json.dumps(d.get("take"))))
        except FileNotFoundError:
            pass
        return done

    # ---- one clip ----
    def prompt_for(self, clip: dict, ep_dir: Path, chapter: int, claim: str) -> tuple[list, str]:
        by_name = {c.name: tr.phased(c, tr.phase_for(self.phases, c.name, chapter)) for c in self.bible.characters}
        cast = [n for n in clip.get("cast", []) if n in by_name]
        extras = list(dict.fromkeys([*(clip.get("extras") or []), *(e for s in clip.get("shots", []) for e in (s.get("extras") or []))]))
        listeners = list(dict.fromkeys(l for l in [*(clip.get("listeners") or []), *(l for s in clip.get("shots", []) for l in (s.get("listeners") or []))] if l in by_name))
        offscreen = list(dict.fromkeys(str(r.get("speaker_name") or "") for r in clip.get("lines", []) if r.get("delivery_mode") == "offscreen_dialogue" and r.get("speaker_name")))
        background = [n for n in clip.get("background_only", []) if n in by_name]
        cards = []
        for name in cast[:3 if len(cast) <= 3 else 2]:
            path = next((Path(ref["path"]) for ref in clip.get("references", []) if ref.get("name") == name and str(ref["path"]).endswith("turnaround.jpeg")), None)
            path = tr.phase_card(self.novel, self.phases, name, chapter) or path
            if path is not None and (self.novel / path).is_file():
                cards.append((name, path))
        lines = "；".join(f"{r.get('speaker_name') or '旁白'}：{r['text']}" for r in clip.get("lines", []))
        location = clip.get("location", "")
        expected_time = self.location_time.get(location, "")
        segments = self._segments.get(ep_dir) or self._segments.setdefault(ep_dir, tr.segment_texts(ep_dir))
        text = ("这是一段动画短剧视频的抽帧。你是终审：判断一个没看过角色设定卡、顺着看剧的观众，看这一段会不会觉得画面不对劲。\n"
                f"本段设定：地点 {location}" + (f"（{expected_time}）" if expected_time else "") + f"；出场人物 {'、'.join(cast) or '无具名角色'}"
                + (f"；无参考图的配角（按描述画，不算多出的人）：{'、'.join(extras)}" if extras else "")
                + (f"；按分镜只露背影或不入镜的听者：{'、'.join(listeners)}（不在画面里不算缺席）" if listeners else "")
                + (f"；画外说话的人：{'、'.join(offscreen)}（本来就不在画面里，不算缺席）" if offscreen else "")
                + "".join(f"\n- {tr.describe(by_name[n])}" for n in cast)
                + (f"\n允许在远处背景出现的角色：{'、'.join(background)}" if background else "")
                + tr.story_block(clip, segments) + tr.snapshot_block(clip, ep_dir)
                + f"\n预期台词：{lines or '无'}\n"
                + (f"\n上一位审片员的意见（待核实；他有时会夸大，例如把几个戴同款帽子的人说成克隆、把画外说话的人说成缺席、把背景里的路人说成多出的角色）：{claim}\n" if claim else "")
                + tr.VERIFY_QUESTIONS.replace("evidence：一句话", "claim_confirmed：上一位审片员说的问题在帧里确实看得到（没有给意见时填 false）；\nevidence：一句话"))
        return cards, text

    def verify(self, job: tuple) -> dict:
        ep, cid, claim, mode = job
        ep_dir = self.episode_dir(ep)
        plan = self.load(ep_dir / "clip_plan.json") or {}
        clip = next((c for c in plan.get("clips", []) if c.get("clip_id") == cid), None)
        review = self.load(ep_dir / "episode_review.json") or {}
        video = self.video_of(ep_dir, cid, (review.get("clips") or {}).get(cid))
        if clip is None or video is None:
            return {"ep": ep, "clip": cid, "mode": mode, "error": "no clip or video"}
        take = tr.take_identity(video)
        chapter = tr.chapter_of(ep_dir)
        schema = json.loads(json.dumps(SCHEMA))
        schema["properties"]["claim_confirmed"] = {"type": "boolean"}
        schema["required"].append("claim_confirmed")
        try:
            cards, text = self.prompt_for(clip, ep_dir, chapter, claim)
            frames = tr.clip_frames(video, self.frames / f"{self.prefix}_{ep}" / cid, tr.MAX_IMAGES - len(cards))
            parts, legend = [], []
            for k, (name, path) in enumerate(cards, 1):
                parts.append(tr.image_part(self.novel / path, tr.CARD_SIDE))
                legend.append(f"图{k}=角色卡：{name}")
            for k, frame in enumerate(frames, len(cards) + 1):
                parts.append(tr.image_part(frame, tr.FRAME_WIDTH))
                legend.append(f"图{k}=视频第{k - len(cards)}帧")
            parts.append({"type": "text", "text": "，".join(legend) + "。\n" + text})
            os.environ.update(self.judge_env)
            if "QWEN38_LOCAL_MODEL" in self.judge_env:
                tr.MODEL = self.judge_env["QWEN38_LOCAL_MODEL"]
            answer = tr.ask_json(parts, schema, name="verify", max_tokens=900)
        except Exception as error:  # noqa: BLE001
            return {"ep": ep, "clip": cid, "mode": mode, "video": str(video), "take": take, "error": f"{type(error).__name__}: {str(error)[:100]}"}
        answer.update({"ep": ep, "clip": cid, "mode": mode, "video": str(video), "take": take, "claim": claim[:300],
                       "ts": time.strftime("%m-%d %H:%M"), "judge": self.judge_tag})
        people = answer.get("people") or []
        answer["people"] = [f"{p.get('who')}({p.get('gender')}{'/动物' if p.get('is_animal') else ''}) {p.get('doing')} [{p.get('frames')}]" for p in people][:8]
        return answer

    # ---- job lists ----
    def story_judged(self):
        for n in self.episodes():
            r = self.load(self.episode_dir(n) / "episode_review.json")
            if r and "story" in str(r.get("policy", "")):
                yield n, r

    def jobs_for(self, mode: str, sample: int, skip: set[int]) -> list[tuple]:
        jobs: list[tuple] = []
        if mode == "sample":
            pool = []
            for n, r in self.story_judged():
                if n in skip:
                    continue
                for cid, v in (r.get("clips") or {}).items():
                    if v.get("story_ok") is not False and v.get("severity") in ("pass", "minor") and (v.get("tier") or v.get("fix_tier")) != "must_fix":
                        pool.append((n, cid))
            random.seed(7)
            random.shuffle(pool)
            jobs = [(n, cid, "", "sample") for n, cid in pool[:sample]]
        elif mode.startswith("replay:"):
            parts = mode.split(":")
            want = parts[2] if len(parts) > 2 else "sample"
            seen = set()
            for line in open(parts[1], encoding="utf-8"):
                d = json.loads(line)
                if d.get("mode") == want and "error" not in d and (d["ep"], d["clip"]) not in seen:
                    seen.add((d["ep"], d["clip"]))
                    jobs.append((d["ep"], d["clip"], d.get("claim", ""), want))
        elif mode == "all":
            for n in self.episodes():
                ep_dir = self.episode_dir(n)
                if n in skip or not (ep_dir / f"{self.prefix}_{n}.mp4").is_file():
                    continue
                plan = self.load(ep_dir / "clip_plan.json") or {}
                rc = (self.load(ep_dir / "episode_review.json") or {}).get("clips") or {}
                for c in plan.get("clips", []):
                    if c.get("kind") != "video":
                        continue
                    if ((rc.get(c["clip_id"]) or {}).get("tier") or (rc.get(c["clip_id"]) or {}).get("fix_tier")) == "must_fix":
                        continue
                    jobs.append((n, c["clip_id"], "", "all"))
        else:
            for n, r in self.story_judged():
                if n in skip:
                    continue
                for cid, v in (r.get("clips") or {}).items():
                    if (v.get("tier") or v.get("fix_tier")) == "must_fix":
                        claim = "；".join(s for s in (v.get("story_issue"), v.get("identity_issue") if v.get("identity_ok") is False else "", v.get("defect_issue")) if s)
                        jobs.append((n, cid, claim, "candidate"))
        done = self.done_keys()
        fresh = []
        for j in jobs:
            ep_dir = self.episode_dir(j[0])
            review = self.load(ep_dir / "episode_review.json") or {}
            video = self.video_of(ep_dir, j[1], (review.get("clips") or {}).get(j[1]))
            if video is None or (j[0], j[1], str(video), json.dumps(tr.take_identity(video))) not in done:
                fresh.append(j)
        return fresh

    def run_jobs(self, jobs: list[tuple], label: str) -> list[dict]:
        print(f"{label}: {len(jobs)} clips to verify (judge {self.judge_tag}, {self.workers} workers)", flush=True)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        results = []
        with self.out.open("a", encoding="utf-8") as out, ThreadPoolExecutor(max_workers=self.workers) as pool:
            for k, res in enumerate(pool.map(self.verify, jobs), 1):
                with self.lock:
                    out.write(json.dumps(res, ensure_ascii=False) + "\n")
                    out.flush()
                results.append(res)
                if k % 25 == 0:
                    print(f"{k}/{len(jobs)} {time.strftime('%H:%M')}", flush=True)
        print("done", flush=True)
        return results


def parse_episodes(text: str) -> set[int]:
    out: set[int] = set()
    for part in (text or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--mode", default="candidates")
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("VERIFY_WORKERS", "6")))
    parser.add_argument("--out", type=Path, default=None, help="default <novel>/verify/verify.jsonl")
    parser.add_argument("--judge-tag", default=os.environ.get("VERIFY_JUDGE", "local"))
    parser.add_argument("--skip-episodes", default="", help="episodes another process is changing right now")
    args = parser.parse_args()
    out = args.out or (args.novel_dir / "verify" / "verify.jsonl")
    v = Verifier(args.novel_dir, out, args.judge_tag, args.workers)
    v.run_jobs(v.jobs_for(args.mode, args.sample, parse_episodes(args.skip_episodes)), args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
