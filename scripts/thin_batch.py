#!/usr/bin/env python
"""One command per novel: plan + pack chapters (serial), build cards (once), render
episodes (parallel), and a batch report.  Everything already done is skipped.

    .venv/bin/python scripts/thin_batch.py --novel-dir outputs/doupo-2d --chapters 1-10 [--parallel 3]

Stages (``--stage``): ``plan`` → ``assets`` → ``render`` → report; ``all`` (default)
runs them in order.  Re-running the same command is always safe:

* a chapter with ``clip_plan.json`` is not re-planned unless ``--replan``;
* cards are only generated when missing;
* an episode is rendered only when its report is missing, was written for a different
  clip plan, or has clips without video; ``--rerender`` forces it.  A second copy of
  this command rendering the same episode is refused by a lock file.

Loads ``.env`` itself and sets the environment the thin scripts need, so the only
prerequisites are the repo venv, the local Qwen service (for ``plan``) and the
novel directory produced by ``scripts/build_bible_thin.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PLAN_STAGES = ("plan", "assets", "render")
sys.path.insert(0, str(SCRIPTS))
from thin_profile import plan_fingerprint  # noqa: E402


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def parse_chapters(spec: str) -> list[int]:
    chapters: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            chapters.extend(range(int(start), int(end) + 1))
        else:
            chapters.append(int(part))
    return sorted(dict.fromkeys(chapters))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class Batch:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.novel_dir = Path(args.novel_dir).resolve()
        self.novel_id = self.novel_dir.name
        meta_path = self.novel_dir / "novel.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        source = args.source or self.meta.get("source")
        if not source:
            raise SystemExit("no --source given and novel.json has none; run scripts/build_bible_thin.py first")
        self.source = Path(source).resolve()
        self.title = args.title or self.meta.get("title") or self.novel_id
        self.bible = self.novel_dir / "story_bible.json"
        if not self.bible.is_file():
            raise SystemExit(f"{self.bible} missing; run scripts/build_bible_thin.py first")
        self.notes = json.loads(Path(args.notes_json).read_text(encoding="utf-8")) if args.notes_json else {}
        self.env = {
            **os.environ,
            "PYTHONPATH": "src:scripts",
            "NOVEL_PLANNER_BACKEND": "deterministic",
            "NOVEL_CREATIVE_PROFILE": os.environ.get("NOVEL_CREATIVE_PROFILE", "short-drama-adaptive-v1"),
            "PHANROUTER_INLINE_REFERENCE_IMAGES": "1",
        }
        self.rows: dict[int, dict] = {}
        self.reviewing = bool(args.unattended or args.review_only)
        self.card_review: dict = {}
        self.card_fixes: dict = {}
        self._novel = None
        self.report_lock = threading.Lock()
        self.cards_lock = threading.Lock()  # card creation is serialized across parallel episode renders

    # ---- helpers ----
    def novel(self):
        if self._novel is None:
            sys.path.insert(0, str(ROOT / "src"))
            from novel_manga.ingest import read_novel
            self._novel = read_novel(self.source, novel_id=self.novel_id, title=self.title)
        return self._novel

    def chapter(self, index: int):
        episodes = self.novel().episodes
        if not 1 <= index <= len(episodes):
            raise SystemExit(f"chapter {index} out of range 1..{len(episodes)}")
        return episodes[index - 1]

    def grow(self, chapter: int) -> None:
        """Chapter-by-chapter bible growth (new characters and locations), before planning."""
        if not self.args.grow_bible or self.args.dry_run:
            return
        if self.plan_status(chapter) == "planned" and not self.args.replan:
            return
        from thin_review import grow_bible
        try:
            grow_bible(self.novel_dir, self.chapter(chapter).source_text, chapter)
        except Exception as error:  # noqa: BLE001 - growth is best effort; planning still works with the bible as is
            log(f"ch{chapter}: bible growth failed ({type(error).__name__}: {str(error)[:120]}); planning with the current bible")
            self.rows[chapter]["note"] = f"bible growth failed: {type(error).__name__}"

    def episode_dir(self, chapter: int) -> Path:
        return self.novel_dir / f"{self.novel_id}_{chapter}"

    def run(self, command: list[str], log_path: Path) -> tuple[int, str]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command)}\n")
            handle.flush()
            completed = subprocess.run(command, cwd=ROOT, env=self.env, stdout=handle, stderr=subprocess.STDOUT, text=True)
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        problem = next((line for line in reversed(tail) if re.search(r"Error|FAILED|planning_failed|Traceback", line)), "")
        return completed.returncode, problem[:200]

    def plan_status(self, chapter: int) -> str:
        directory = self.episode_dir(chapter)
        if (directory / "clip_plan.json").is_file():
            return "planned"
        if (directory / "planning_failed.json").is_file():
            return "failed"
        return "missing"

    def render_status(self, chapter: int) -> str:
        directory = self.episode_dir(chapter)
        report = directory / "thin_media_report.json"
        plan = directory / "clip_plan.json"
        if not plan.is_file():
            return "no_plan"
        if not report.is_file():
            return "pending"
        data = json.loads(report.read_text(encoding="utf-8"))
        stamped = data.get("clip_plan_fingerprint")
        if stamped and stamped != plan_fingerprint(json.loads(plan.read_text(encoding="utf-8"))):
            return "stale"  # reports from before the stamp are trusted; a re-plan deletes them anyway
        if data.get("failed_clips") or not data.get("assembly"):
            return "clips_failed"
        if data.get("gate_failed_clips") or not data["assembly"].get("thin_passed"):
            return "done_with_warnings"
        return "done"

    # ---- stages ----
    def check_qwen(self) -> None:
        base = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
        try:
            with urllib.request.urlopen(f"{base}/models", timeout=5) as response:  # noqa: S310 - local service
                response.read(200)
        except Exception as error:  # noqa: BLE001
            raise SystemExit(f"Qwen planner service not reachable at {base} ({type(error).__name__}); start the novel-manga-qwen38-vlm container first")

    def plan(self, chapter: int) -> None:
        row = self.rows[chapter]
        directory = self.episode_dir(chapter)
        if self.plan_status(chapter) == "planned" and not self.args.replan:
            row["plan"] = "kept"
            return
        if self.chapter(chapter).text_count < self.args.min_chapter_chars:
            row["plan"] = "skipped (too short)"
            row["note"] = f"{self.chapter(chapter).text_count} chars"
            return
        if self.args.dry_run:
            row["plan"] = "would plan"
            return
        log(f"ch{chapter}: planning")
        (directory / "planning_failed.json").unlink(missing_ok=True) if directory.is_dir() else None
        command = [sys.executable, str(SCRIPTS / "plan_chapter_thin.py"), str(self.source), "--novel-id", self.novel_id, "--title", self.title,
                   "--episode-index", str(chapter), "--bible", str(self.bible), "--output-root", str(self.novel_dir.parent), "--max-redo", str(self.args.max_redo)]
        if self.args.min_seconds:
            command += ["--min-seconds", str(self.args.min_seconds)]
        notes = self.notes.get(str(chapter)) or self.notes.get("*")
        if notes:
            command += ["--notes", notes]
        code, problem = self.run(command, directory / "plan.log")
        if code != 0:
            failure = directory / "planning_failed.json"
            errors = json.loads(failure.read_text(encoding="utf-8")).get("errors", []) if failure.is_file() else [problem]
            row["plan"] = "failed"
            row["note"] = " | ".join(errors)[:240]
            log(f"ch{chapter}: planning FAILED: {row['note'][:160]}")
            return
        code, problem = self.run([sys.executable, str(SCRIPTS / "build_clip_plan_thin.py"), "--episode-dir", str(directory), "--bible", str(self.bible)], directory / "plan.log")
        if code != 0:
            row["plan"] = "pack failed"
            row["note"] = problem
            log(f"ch{chapter}: packing FAILED: {problem}")
            return
        totals = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))["totals"]
        row["plan"] = "planned"
        row["clips"] = totals["video_clip_count"]
        row["estimate"] = totals["estimated_seconds"]
        log(f"ch{chapter}: planned, {totals['video_clip_count']} clips, ~{totals['estimated_seconds']} s")

    def assets(self, chapters: list[int]) -> None:
        planned = [ch for ch in chapters if self.plan_status(ch) == "planned"]
        if not planned:
            return
        if self.args.dry_run:
            log(f"would build cards for chapters {planned}")
            return
        self.build_cards(planned)
        if not self.reviewing:
            return
        from thin_review import remediate_cards, review_cards
        self.card_review = review_cards(self.novel_dir)
        if self.args.unattended and self.card_review["flags"]:
            self.card_fixes = remediate_cards(self.novel_dir, self.card_review)
            if self.card_fixes["deleted"] or self.card_fixes["stylized"]:
                self.build_cards(planned)
                self.card_review = review_cards(self.novel_dir)
        if self.card_review["flags"]:
            log(f"cards: {len(self.card_review['flags'])} flag(s) remain for the delivery report")

    def build_cards(self, planned: list[int]) -> None:
        for chapter in planned:
            directory = self.episode_dir(chapter)
            code, problem = self.run([sys.executable, str(SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--assets-only"], directory / "render.log")
            if code != 0:
                self.rows[chapter]["note"] = f"assets: {problem}"
                log(f"ch{chapter}: card build FAILED: {problem}")
        sheet = self.novel_dir / "series_assets" / "cards_sheet.jpg"
        if sheet.is_file():
            log(f"cards ready; review {sheet}")

    def render(self, chapter: int) -> None:
        row = self.rows[chapter]
        directory = self.episode_dir(chapter)
        status = self.render_status(chapter)
        row["render_before"] = status
        if status == "no_plan":
            row["render"] = "skipped (no plan)"
            return
        if status in {"done", "done_with_warnings"} and not self.args.rerender:
            row["render"] = status
            self.fill_result(chapter)
            if self.reviewing:
                try:
                    self.review_episode(chapter)
                except Exception as error:  # noqa: BLE001
                    row["note"] = f"review failed: {type(error).__name__}: {str(error)[:120]}"
                    log(f"ch{chapter}: episode review failed ({type(error).__name__}: {str(error)[:120]})")
            return
        if self.args.dry_run:
            row["render"] = f"would render ({status})"
            return
        lock = directory / ".render.lock"
        if lock.is_file():
            try:
                pid = int(lock.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid_alive(pid):
                row["render"] = f"locked by pid {pid}"
                log(f"ch{chapter}: another render (pid {pid}) is running; skipped")
                return
            lock.unlink(missing_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        try:
            self.prepare_cards(chapter)
            command = [sys.executable, str(SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--workers", str(self.args.workers)]
            for attempt in (1, 2):
                log(f"ch{chapter}: rendering (attempt {attempt})")
                code, problem = self.run(command, directory / "render.log")
                status = self.render_status(chapter)
                if status in {"done", "done_with_warnings"}:
                    break
                log(f"ch{chapter}: render attempt {attempt} ended with {status} ({problem[:120]})")
                if attempt == 1 and status in {"clips_failed", "pending", "stale"}:
                    continue
                break
            row["render"] = status
            if status not in {"done", "done_with_warnings"}:
                row["note"] = problem
            self.fill_result(chapter)
            if self.reviewing and status in {"done", "done_with_warnings"}:
                try:
                    self.review_episode(chapter)
                except Exception as error:  # noqa: BLE001 - the episode is done; a review failure is a note
                    row["note"] = f"review failed: {type(error).__name__}: {str(error)[:120]}"
                    log(f"ch{chapter}: episode review failed ({type(error).__name__}: {str(error)[:120]})")
            if self.args.prune and self.render_status(chapter) in {"done", "done_with_warnings"}:
                prune_episode(directory)
        finally:
            lock.unlink(missing_ok=True)

    def prepare_cards(self, chapter: int) -> None:
        """Build the cards this episode references; in review mode judge just those
        cards, fix each at most once, rebuild, judge again."""
        directory = self.episode_dir(chapter)
        row = self.rows[chapter]
        with self.cards_lock:  # two episodes building the same character at once raced on the card files
            code, problem = self.run([sys.executable, str(SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--assets-only"], directory / "render.log")
            if code != 0:
                row["note"] = f"cards: {problem}"
                log(f"ch{chapter}: card build problem: {problem[:160]}")
            if not self.reviewing:
                return
            from thin_review import remediate_cards, review_cards
            plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
            wanted = {ref["asset_id"] for clip in plan["clips"] for ref in clip.get("references", [])}
            with self.report_lock:  # the judges share one single-sequence VLM; keep card reviews serialized
                review = review_cards(self.novel_dir, only_ids=wanted)
                if self.args.unattended and review["flags"]:
                    fixes = remediate_cards(self.novel_dir, review)
                    if fixes["stylized"] or fixes["deleted"]:
                        self.run([sys.executable, str(SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--assets-only"], directory / "render.log")
                        review = review_cards(self.novel_dir, only_ids=wanted)
                    row["card_fixes"] = fixes["stylized"] + fixes["deleted"]
            row["card_flags"] = review["flags"]

    def review_episode(self, chapter: int) -> None:
        """Automatic clip review; in unattended mode a failed clip gets the reviewer's
        correction appended to its prompt and is regenerated once, then reviewed again."""
        from thin_review import review_episode
        row = self.rows[chapter]
        directory = self.episode_dir(chapter)
        review = review_episode(directory)
        feedback_path = directory / "review_feedback.json"
        existing = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.is_file() else {}
        fresh = {clip_id: note for clip_id, note in review["feedback"].items() if clip_id not in existing and note}
        if self.args.unattended and fresh:
            feedback_path.write_text(json.dumps({**existing, **fresh}, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"ch{chapter}: regenerating {sorted(fresh)} with the reviewer's corrections")
            self.run([sys.executable, str(SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--workers", str(self.args.workers)], directory / "render.log")
            row["render"] = self.render_status(chapter)
            self.fill_result(chapter)
            review = review_episode(directory)
            row["auto_fixed"] = sorted(fresh)
        row["review_flags"] = review["flags"]
        if review["flags"]:
            log(f"ch{chapter}: {len(review['flags'])} clip flag(s) remain: {review['flags'][0][:120]}")

    def fill_result(self, chapter: int) -> None:
        report = self.episode_dir(chapter) / "thin_media_report.json"
        if not report.is_file():
            return
        data = json.loads(report.read_text(encoding="utf-8"))
        row = self.rows[chapter]
        row["clips"] = len(data.get("clips", []))
        row["retries"] = sum(max(0, len(c.get("attempts", [])) - 1) for c in data.get("clips", []))
        assembly = data.get("assembly") or {}
        if assembly:
            row["duration"] = round(assembly.get("duration", 0.0), 1)
            row["thin_passed"] = assembly.get("thin_passed")
            row["video"] = assembly.get("final_video")
        if data.get("failed_clips"):
            row["note"] = "no video: " + ", ".join(data["failed_clips"])
        elif data.get("gate_failed_clips"):
            row["note"] = "gate failed: " + ", ".join(data["gate_failed_clips"])

    # ---- streaming: plan one chapter, hand it to the render pool, continue ----
    def stream(self, chapters: list[int]) -> None:
        if not self.args.dry_run and any(self.plan_status(ch) != "planned" or self.args.replan for ch in chapters):
            self.check_qwen()
        futures = []
        with ThreadPoolExecutor(max_workers=max(1, self.args.parallel)) as pool:
            for chapter in chapters:
                self.grow(chapter)
                self.plan(chapter)
                if self.plan_status(chapter) == "planned":
                    futures.append(pool.submit(self.render, chapter))
                self.volume_checkpoint(chapter, chapters)
            for chapter, future in zip([ch for ch in chapters if self.plan_status(ch) == "planned"], futures):
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001 - one episode's thread must not end the batch
                    self.rows[chapter]["note"] = f"render thread failed: {type(error).__name__}: {str(error)[:120]}"
                    log(f"ch{chapter}: render thread failed ({type(error).__name__}: {str(error)[:120]})")

    def volume_checkpoint(self, chapter: int, chapters: list[int]) -> None:
        size = max(1, self.args.volume_size)
        if chapter % size and chapter != chapters[-1]:
            return
        volume = (chapter - 1) // size + 1
        first = (volume - 1) * size + 1
        growth_path = self.novel_dir / "bible_growth.json"
        growth = json.loads(growth_path.read_text(encoding="utf-8")) if growth_path.is_file() else {}
        added_characters, added_locations, suggestions = [], [], {}
        for index in range(first, chapter + 1):
            entry = growth.get(str(index), {})
            added_characters += entry.get("characters", [])
            added_locations += entry.get("locations", [])
            suggestions.update(entry.get("suggestions", {}))
        lines = [f"# 第 {volume} 卷复核（第 {first}–{chapter} 章）", "",
                 f"- 本卷新增角色：{', '.join(added_characters) or '无'}", f"- 本卷新增地点：{', '.join(added_locations) or '无'}",
                 f"- 待人工确认的称呼类人物（建议条目在 bible_growth.json）：{', '.join(suggestions) or '无'}", ""]
        flagged = [(ch, self.rows[ch]) for ch in chapters if first <= ch <= chapter and (self.rows[ch].get("card_flags") or self.rows[ch].get("review_flags") or self.rows[ch].get("note"))]
        lines.append("- 本卷标记：" + ("；".join(f"第{ch}章 {row.get('note') or ''} {' '.join(row.get('card_flags', []))} {' '.join(row.get('review_flags', []))}".strip() for ch, row in flagged) or "无"))
        (self.novel_dir / f"volume_review_{volume:03d}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"volume {volume} review written ({len(added_characters)} new characters, {len(added_locations)} new locations, {len(suggestions)} suggestions)")

    # ---- report ----
    def report(self, chapters: list[int]) -> int:
        lines = ["| 章 | 规划 | 段 | 时长 | 薄门 | 渲染 | 备注 |", "|---|---|---|---|---|---|---|"]
        for chapter in chapters:
            row = self.rows[chapter]
            lines.append(f"| {chapter} | {row.get('plan', self.plan_status(chapter))} | {row.get('clips', '')} | {row.get('duration', '')} | {row.get('thin_passed', '')} | {row.get('render', self.render_status(chapter))} | {row.get('note', '')} |")
        table = "\n".join(lines)
        print(table, flush=True)
        payload = {"novel_id": self.novel_id, "chapters": chapters, "rows": self.rows, "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "args": vars(self.args)}
        (self.novel_dir / "batch_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(self.novel_dir / "batch_report.md", "a", encoding="utf-8") as handle:
            handle.write(f"\n## {payload['finished']} · chapters {self.args.chapters} · stage {self.args.stage}\n\n{table}\n")
        done = [ch for ch in chapters if self.rows[ch].get("render", self.render_status(ch)) in {"done", "done_with_warnings"}]
        log(f"{len(done)}/{len(chapters)} episodes have a final video")
        if self.reviewing:
            self.delivery_report(chapters)
        return 0 if len(done) == len(chapters) or self.args.stage != "all" else 2


    def delivery_report(self, chapters: list[int]) -> None:
        bible_review_path = self.novel_dir / "bible_review.json"
        bible_review = json.loads(bible_review_path.read_text(encoding="utf-8")) if bible_review_path.is_file() else {}
        cards_path = self.novel_dir / "series_assets" / "cards_review.json"
        cards = self.card_review or (json.loads(cards_path.read_text(encoding="utf-8")) if cards_path.is_file() else {})
        per_episode_flags = sorted({flag for chapter in chapters for flag in self.rows[chapter].get("card_flags", [])})
        per_episode_fixes = sorted({fix for chapter in chapters for fix in self.rows[chapter].get("card_fixes", [])})
        lines = [f"# {self.title} · 交付报告（{time.strftime('%Y-%m-%d %H:%M')}）", "", f"模式：{'无人值守（自动修复各一次）' if self.args.unattended else '只审核不修复'}；章节 {self.args.chapters}", ""]
        if bible_review:
            lines += ["## 圣经", "", f"- 原文高频但圣经缺失的人物：{', '.join(bible_review.get('missing', {})) or '无'}", f"- 自动补入：{', '.join(bible_review.get('filled', [])) or '无'}", ""]
        lines += ["## 角色卡与地点卡", ""]
        if self.card_fixes:
            lines.append(f"- 自动重画（动画化）：{', '.join(self.card_fixes.get('stylized', [])) or '无'}；删除重建：{', '.join(self.card_fixes.get('deleted', [])) or '无'}")
        if per_episode_fixes:
            lines.append(f"- 各集建卡时自动修复：{', '.join(per_episode_fixes)}")
        lines.append(f"- 仍有标记：{'；'.join(per_episode_flags or cards.get('flags', [])) or '无'}")
        lines += ["", "## 各集", "", "| 集 | 时长 | 段 | 语音门未过 | 薄QC | 自动修正段 | 剩余标记 |", "|---|---|---|---|---|---|---|"]
        for chapter in chapters:
            row = self.rows[chapter]
            report_path = self.episode_dir(chapter) / "thin_media_report.json"
            data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            lines.append(f"| {chapter} | {row.get('duration', '')} | {row.get('clips', '')} | {', '.join(data.get('gate_failed_clips', [])) or '无'} | {row.get('thin_passed', '')} | {', '.join(row.get('auto_fixed', [])) or '无'} | {'；'.join(row.get('review_flags', [])) or '无'} |")
        videos = [row.get("video") for chapter in chapters for row in [self.rows[chapter]] if row.get("video")]
        lines += ["", "## 交付文件", ""] + [f"- {video}" for video in videos]
        (self.novel_dir / "delivery_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"delivery report: {self.novel_dir / 'delivery_report.md'}")


def prune_episode(directory: Path) -> None:
    """Drop what a finished episode no longer needs (~80% of its footprint); the
    clip videos, ASR results and request sidecars stay so re-runs hit the cache."""
    removed = 0
    for pattern in ("clips/*/attempt_*/native*.wav", "clips/*/attempt_*/clip.stale.mp4*", "clips/*/attempt_*/stale_*", "clips/*/attempt_*/native.stale.wav"):
        for path in (directory / "work").glob(pattern):
            path.unlink(missing_ok=True)
            removed += 1
    for sub_dir in ("segments", "review"):
        target = directory / "work" / sub_dir
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    log(f"{directory.name}: pruned {removed} intermediate files/dirs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", required=True, help="outputs/<novel-id> created by build_bible_thin.py")
    parser.add_argument("--chapters", default="1", help='e.g. "1", "2-10", "1,3,5-7"')
    parser.add_argument("--source", help="novel text; defaults to the path recorded in novel.json")
    parser.add_argument("--title", help="cover title; defaults to novel.json")
    parser.add_argument("--stage", choices=("all", *PLAN_STAGES), default="all")
    parser.add_argument("--parallel", type=int, default=3, help="episodes rendered at the same time")
    parser.add_argument("--workers", type=int, default=4, help="clips in flight per episode")
    parser.add_argument("--max-redo", type=int, default=2, help="planner redo rounds")
    parser.add_argument("--min-seconds", type=float, default=0.0, help="planner floor for the episode estimate")
    parser.add_argument("--notes-json", help='director notes per chapter: {"3": "...", "*": "for every chapter"}')
    parser.add_argument("--replan", action="store_true", help="re-plan chapters that already have a clip plan")
    parser.add_argument("--rerender", action="store_true", help="re-render episodes that already have a final video")
    parser.add_argument("--unattended", action="store_true", help="automatic reviews with bounded paid fixes: cards after the assets stage (one redraw/regeneration), clips after each render (one regeneration with the reviewer's correction); then delivery_report.md")
    parser.add_argument("--review-only", action="store_true", help="run the automatic reviews and write delivery_report.md without any paid fix")
    parser.add_argument("--grow-bible", dest="grow_bible", action="store_true", default=True, help="before planning a chapter, add its new proper-named characters and locations to the bible (default)")
    parser.add_argument("--no-grow-bible", dest="grow_bible", action="store_false")
    parser.add_argument("--prune", action="store_true", help="after an episode is assembled, delete its intermediate audio, stale clips and review frames (keeps clip.mp4 + asr.json for the cache)")
    parser.add_argument("--volume-size", type=int, default=50, help="write volume_review_N.md every N chapters (bible growth, suggestions, flags)")
    parser.add_argument("--min-chapter-chars", type=int, default=300, help="chapters shorter than this (author notes) are skipped")
    parser.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    batch = Batch(args)
    chapters = parse_chapters(args.chapters)
    batch.rows = {chapter: {} for chapter in chapters}
    log(f"{batch.novel_id}: chapters {chapters[0]}..{chapters[-1]} ({len(chapters)}), stage {args.stage}, source {batch.source.name}")
    if args.stage == "all":
        batch.stream(chapters)  # plan one chapter, render it while the next is planned
        return batch.report(chapters)
    if args.stage == "plan":
        if not args.dry_run and any(batch.plan_status(ch) != "planned" or args.replan for ch in chapters):
            batch.check_qwen()
        for chapter in chapters:  # serial: the local Qwen service runs one sequence at a time
            batch.grow(chapter)
            batch.plan(chapter)
            batch.volume_checkpoint(chapter, chapters)
    if args.stage == "assets":
        batch.assets(chapters)
    if args.stage == "render":
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            for chapter, future in [(ch, pool.submit(batch.render, ch)) for ch in chapters]:
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001
                    batch.rows[chapter]["note"] = f"render thread failed: {type(error).__name__}: {str(error)[:120]}"
                    log(f"ch{chapter}: render thread failed ({type(error).__name__}: {str(error)[:120]})")
    return batch.report(chapters)


if __name__ == "__main__":
    sys.exit(main())
