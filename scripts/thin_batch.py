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

    # ---- helpers ----
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
        finally:
            lock.unlink(missing_ok=True)

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
        return 0 if len(done) == len(chapters) or self.args.stage != "all" else 2


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
    parser.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    batch = Batch(args)
    chapters = parse_chapters(args.chapters)
    batch.rows = {chapter: {} for chapter in chapters}
    log(f"{batch.novel_id}: chapters {chapters}, stage {args.stage}, source {batch.source.name}")
    if args.stage in {"all", "plan"}:
        if not args.dry_run and any(batch.plan_status(ch) != "planned" or args.replan for ch in chapters):
            batch.check_qwen()
        for chapter in chapters:  # serial: the local Qwen service runs one sequence at a time
            batch.plan(chapter)
    if args.stage in {"all", "assets"}:
        batch.assets(chapters)
    if args.stage in {"all", "render"}:
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            list(pool.map(batch.render, chapters))
    return batch.report(chapters)


if __name__ == "__main__":
    sys.exit(main())
