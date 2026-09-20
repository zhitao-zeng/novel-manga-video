"""production_flow_thin responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import novel_manga.application.production.assets as production_assets
import novel_manga.application.production.common as production_common
import novel_manga.application.production.render as production_render
import novel_manga.application.production.reports as production_reports
import novel_manga.application.profiles as thin_profile
import novel_manga.application.production.runs as thin_runs

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
            "PYTHONPATH": "src",
            "NOVEL_PLANNER_BACKEND": "deterministic",
            "NOVEL_CREATIVE_PROFILE": os.environ.get("NOVEL_CREATIVE_PROFILE", "short-drama-adaptive-v1"),
            **thin_profile.reference_image_env(os.environ),
            # Someone has checked the bill: submissions recorded as unconfirmed before this run may be sent again - not
            # one that goes unconfirmed during it (the provider compares its record with this start time).
            **({"NOVEL_RESUBMIT_UNCONFIRMED": f"{time.time():.0f}"} if args.resubmit_unconfirmed else {}),
        }
        self.rows: dict[int, dict] = {}
        self.reviewing = bool(args.unattended or args.review_only)
        from novel_manga.application.profiles import is_fast, load_profile
        self.profile = load_profile(self.novel_dir, tier=args.tier)
        self.fast = is_fast(self.profile)
        self.grow_lock = threading.Lock()
        self.cards = production_assets.CardFactory(self, args.card_parallel)
        self.card_review: dict = {}
        self.card_fixes: dict = {}
        self._novel = None
        self.report_lock = threading.Lock()
        self.cards_lock = threading.Lock()  # card creation is serialized across parallel episode renders

    # ---- helpers ----
    def novel(self):
        if self._novel is None:
            from novel_manga.ingest import read_novel
            self._novel = read_novel(self.source, novel_id=self.novel_id, title=self.title)
        return self._novel

    def chapter(self, index: int):
        """Episode `index`: one chapter, or --merge consecutive chapters joined."""
        episodes = self.novel().episodes
        merge = max(1, self.args.merge)
        count = -(-len(episodes) // merge)
        if not 1 <= index <= count:
            raise SystemExit(f"episode {index} out of range 1..{count} (merge {merge})")
        if merge == 1:
            return episodes[index - 1]
        group = episodes[(index - 1) * merge: index * merge]
        return group[0].model_copy(update={"index": index, "source_text": "\n\n".join(e.source_text for e in group), "text_count": sum(e.text_count for e in group)})

    def grow(self, chapter: int) -> None:
        """Chapter-by-chapter bible growth (new characters and locations), before planning."""
        if not self.args.grow_bible or self.args.dry_run:
            return
        if self.plan_status(chapter) == "planned" and not self.args.replan:
            return
        from novel_manga.application.review.bible import grow_bible
        if self.fast and production_common.GROW_STRIDE > 1 and chapter % production_common.GROW_STRIDE != 1:
            return  # fast tier: sample chapters for growth (GROW_STRIDE = 1 grows every one)
        try:
            with self.grow_lock:  # parallel planners must not append to the bible at once
                before = json.loads(self.bible.read_text(encoding="utf-8"))
                grow_bible(self.novel_dir, self.chapter(chapter).source_text, chapter)
                after = json.loads(self.bible.read_text(encoding="utf-8"))
            # New proper-named characters and locations usually appear within a chapter or two, so their
            # cards are started here rather than at the assets stage.  Usually, not always: 超品相师's first
            # chapter is about a legend, and growth added 诸葛亮, 魏延, 司马懿 and 导游 - the bible's own note on
            # 司马懿 reads "背景提及" - each of which was then drawn and paid for.  --no-eager-cards defers them.
            new_ids = [f"character_{i:03d}" for i in range(len(before["characters"]) + 1, len(after["characters"]) + 1)]
            new_ids += [f"location_{i:03d}" for i in range(len(before["locations"]) + 1, len(after["locations"]) + 1)]
            if new_ids and not self.args.dry_run and getattr(self.args, "eager_cards", True):
                self.cards.want(new_ids)
        except Exception as error:  # noqa: BLE001 - growth is best effort; planning still works with the bible as is
            production_common.log(f"ch{chapter}: bible growth failed ({type(error).__name__}: {str(error)[:120]}); planning with the current bible")
            self.rows[chapter]["note"] = f"bible growth failed: {type(error).__name__}"

    def episode_dir(self, chapter: int) -> Path:
        return self.novel_dir / f"{self.novel_id}_{chapter}"

    def run(self, command: list[str], log_path: Path) -> tuple[int, str]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command)}\n")
            handle.flush()
            completed = subprocess.run(command, cwd=production_common.ROOT, env=self.env, stdout=handle, stderr=subprocess.STDOUT, text=True)
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        problem = next((line for line in reversed(tail) if re.search(r"Error|FAILED|planning_failed|Traceback", line)), "")
        return completed.returncode, problem[:200]

    def plan_stage(self, chapters: list[int]) -> None:
        """--stage plan: up to --plan-parallel chapters at once, as --stage all does - it used to be a plain loop
        whatever --plan-parallel said.  The volume checkpoints still come in chapter order."""
        def plan_one(chapter: int) -> int:
            self.grow(chapter)
            self.plan(chapter)
            return chapter
        with ThreadPoolExecutor(max_workers=max(1, self.args.plan_parallel)) as planners:
            for future in [planners.submit(plan_one, chapter) for chapter in chapters]:
                production_reports.volume_checkpoint(self, future.result(), chapters)

    def plan_status(self, chapter: int) -> str:
        directory = self.episode_dir(chapter)
        if (directory / "clip_plan.json").is_file():
            return "planned"
        if (directory / "planning_failed.json").is_file():
            return "failed"
        return "missing"

    def render_status(self, chapter: int) -> str:
        """novel_manga.application.production.runs.episode_status, read the way this lane renders (an H3 lane also watches its English prompts)."""
        return thin_runs.episode_status(self.episode_dir(chapter), bool(os.environ.get("NOVEL_LOCAL_H3_URL")))

    # ---- stages ----
    def check_qwen(self) -> None:
        from novel_manga.llm.config import qwen_endpoints
        alive = []
        for base in qwen_endpoints():
            try:
                probe = urllib.request.Request(f"{base}/models")
                from novel_manga.llm.config import endpoint_key
                key = endpoint_key()
                if key:  # an endpoint behind a key answers 401 to a bare probe
                    probe.add_header("Authorization", "Bearer " + key)
                with urllib.request.urlopen(probe, timeout=20) as response:  # noqa: S310 - local service; a proxied catalogue takes ~8 s
                    response.read(200)
                alive.append(base)
            except Exception:  # noqa: BLE001
                production_common.log(f"Qwen endpoint not reachable: {base}")
        if not alive:
            raise SystemExit("no Qwen endpoint reachable (QWEN38_LOCAL_BASE_URL); start the Qwen containers first")
        production_common.log(f"Qwen endpoints alive: {len(alive)}/{len(qwen_endpoints())}")

    def plan(self, chapter: int, *, replan: bool | None = None, notes: str | None = None) -> None:
        force_replan = self.args.replan if replan is None else replan
        row = self.rows[chapter]
        directory = self.episode_dir(chapter)
        if self.plan_status(chapter) == "planned" and not force_replan:
            row["plan"] = "kept"
            return
        if self.chapter(chapter).text_count < self.args.min_chapter_chars:
            row["plan"] = "skipped (too short)"
            row["note"] = f"{self.chapter(chapter).text_count} chars"
            if not self.args.dry_run:
                # A marker, so the conductor can tell a chapter left out on purpose (an author's note) from
                # one whose planning never finished.
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "planning_skipped.json").write_text(json.dumps(
                    {"reason": "too short", "chars": self.chapter(chapter).text_count,
                     "min_chapter_chars": self.args.min_chapter_chars}), encoding="utf-8")
            return
        if self.args.dry_run:
            row["plan"] = "would plan"
            return
        production_common.log(f"ch{chapter}: planning")
        (directory / "planning_failed.json").unlink(missing_ok=True) if directory.is_dir() else None
        command = [sys.executable, str(production_common.SCRIPTS / "plan_chapter_thin.py"), str(self.source), "--novel-id", self.novel_id, "--title", self.title,
                   "--episode-index", str(chapter), "--bible", str(self.bible), "--output-root", str(self.novel_dir.parent), "--max-redo", str(self.args.max_redo),
                   "--merge", str(max(1, self.args.merge))] + (["--tier", self.args.tier] if self.args.tier else [])
        if self.args.min_seconds:
            command += ["--min-seconds", str(self.args.min_seconds)]
        notes = (self.notes.get(str(chapter)) or self.notes.get("*")) if notes is None else notes
        if notes:
            command += ["--notes", notes]
        code, problem = self.run(command, directory / "plan.log")
        if code != 0:
            failure = directory / "planning_failed.json"
            errors = json.loads(failure.read_text(encoding="utf-8")).get("errors", []) if failure.is_file() else [problem]
            row["plan"] = "failed"
            row["note"] = " | ".join(errors)[:240]
            production_common.log(f"ch{chapter}: planning FAILED: {row['note'][:160]}")
            if force_replan:
                # A superseded plan must not be rendered in place of the one that failed.
                for stale in ("clip_plan.json", "clip_plan.md", "thin_media_report.json", "media_qc_report.json"):
                    (directory / stale).unlink(missing_ok=True)
            return
        code, problem = self.run([sys.executable, str(production_common.SCRIPTS / "build_clip_plan_thin.py"), "--episode-dir", str(directory), "--bible", str(self.bible)] + (["--tier", self.args.tier] if self.args.tier else []), directory / "plan.log")
        if code != 0:
            row["plan"] = "pack failed"
            row["note"] = problem
            production_common.log(f"ch{chapter}: packing FAILED: {problem}")
            return
        totals = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))["totals"]
        row["plan"] = "planned"
        row["clips"] = totals["video_clip_count"]
        row["estimate"] = totals["estimated_seconds"]
        production_common.log(f"ch{chapter}: planned, {totals['video_clip_count']} clips, ~{totals['estimated_seconds']} s")

    def assets(self, chapters: list[int]) -> None:
        planned = [ch for ch in chapters if self.plan_status(ch) == "planned"]
        if not planned:
            return
        if self.args.dry_run:
            production_common.log(f"would build cards for chapters {planned}")
            return
        self.build_cards(planned)
        if not self.reviewing:
            return
        from novel_manga.application.review.cards import remediate_cards, review_cards
        self.card_review = review_cards(self.novel_dir)
        if self.args.unattended and self.card_review["flags"]:
            self.card_fixes = remediate_cards(self.novel_dir, self.card_review)
            if self.card_fixes["deleted"] or self.card_fixes["stylized"]:
                self.build_cards(planned)
                self.card_review = review_cards(self.novel_dir)
        if self.card_review["flags"]:
            production_common.log(f"cards: {len(self.card_review['flags'])} flag(s) remain for the delivery report")

    def build_cards(self, planned: list[int]) -> None:
        for chapter in planned:
            directory = self.episode_dir(chapter)
            code, problem = self.run([sys.executable, str(production_common.SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--assets-only"], directory / "render.log")
            if code != 0:
                self.rows[chapter]["note"] = f"assets: {problem}"
                production_common.log(f"ch{chapter}: card build FAILED: {problem}")
        sheet = self.novel_dir / "series_assets" / "cards_sheet.jpg"
        if sheet.is_file():
            production_common.log(f"cards ready; review {sheet}")


    def moderation_targets(self, chapter: int) -> str:
        """What the platform refused, for the re-plan note: the refused clips' lines and stage text."""
        directory = self.episode_dir(chapter)
        try:
            report = json.loads((directory / "thin_media_report.json").read_text(encoding="utf-8"))
            plan = {c["clip_id"]: c for c in json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))["clips"]}
        except (OSError, ValueError, KeyError):
            return ""
        parts = []
        for c in report.get("clips", []):
            error = str(c.get("error") or "")
            if "SensitiveContentDetected" not in error:
                continue
            clip = plan.get(c.get("clip_id")) or {}
            kind = "输入文字" if "InputText" in error else "生成画面"
            lines = "；".join(f"{l.get('speaker_name', '')}：「{l.get('text', '')}」" for l in (clip.get("lines") or [])[:6])
            prompt = str(clip.get("prompt") or "")
            k = prompt.find("【阶段")
            stage_text = re.sub(r"\s+", " ", prompt[k:k + 300]) if k >= 0 else ""
            first_shot = (clip.get("shot_indexes") or ["?"])[0]
            parts.append(f"第 {first_shot} 镜起的一段（{kind}被拒）：台词 {lines or '无'}；画面：{stage_text}")
        if not parts:
            return ""
        return ("\n平台审核具体拒绝了以下内容。重写时必须把这些句子和描写改成不含打斗伤害、血腥、死亡、武器、辱骂、脱衣裸露、性暗示字眼的等义表达"
                "（台词可以换词、缩短或改为旁观者转述；画面改为对峙、退让和事后结果），其余段落尽量保持不变：\n" + "\n".join(parts))[:1500]

    def moderation_blocked(self, chapter: int) -> bool:
        report = self.episode_dir(chapter) / "thin_media_report.json"
        if not report.is_file():
            return False
        data = json.loads(report.read_text(encoding="utf-8"))
        return any("SensitiveContentDetected" in str(c.get("error", "")) for c in data.get("clips", []))

    def h3_ready(self, chapter: int) -> bool:
        """Whether a local-H3 lane can render this episode now: every video clip has a current English prompt.

        H3 speaks what is written in Chinese, so a clip rendered from the Chinese prompt recites its stage
        directions.  An episode planned or re-packed since the last conversion is converted here, right
        before it renders (build_h3_prompts.py, a local Qwen call per clip); one that still cannot be
        converted waits for the next round - it used to be decided once per lane start, and 1597 waited
        for good while conversion was a pass run by hand."""
        directory = self.episode_dir(chapter)
        if not self.h3_missing(directory):
            return True
        production_common.log(f"ch{chapter}: writing the English (H3) prompts")
        _, problem = self.run([sys.executable, str(production_common.SCRIPTS / "build_h3_prompts.py"), self.novel_id, "--novel-dir", str(self.novel_dir),
                               "--chapters", str(chapter), "--workers", "1"], directory / "h3_prompts.log")
        missing = self.h3_missing(directory)
        if missing:
            why = f" ({problem[:120]})" if problem else ""
            production_common.log(f"ch{chapter}: {missing} clip(s) still without an H3 prompt{why}; waiting for a later round")
            return False
        return True

    @staticmethod
    def h3_missing(directory: Path) -> int:
        try:
            plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        notes = thin_runs.corrections(directory)  # a correction is written into the English prompt: a new one makes it due
        from novel_manga.application.preparation.readiness import inspect_episode
        _, blocked = inspect_episode(directory)
        return sum(1 for clip in plan.get("clips", []) if clip.get("kind") == "video"
                   and clip["clip_id"] not in blocked
                   and thin_profile.h3_prompt_outdated(clip, str(notes.get(clip.get("clip_id"), ""))))

    def prepare_cards(self, chapter: int) -> None:
        """Build the cards this episode references; in review mode judge just those
        cards, fix each at most once, rebuild, judge again."""
        directory = self.episode_dir(chapter)
        row = self.rows[chapter]
        plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
        from novel_manga.application.preparation.readiness import inspect_episode
        _, blocked = inspect_episode(directory)
        wanted = {ref["asset_id"] for clip in plan["clips"] if clip["clip_id"] not in blocked
                  for ref in clip.get("references", []) if ref.get("role") in {"character", "location"}}
        # Named characters who keep coming back - in the group chat or off
        # screen - without ever being on camera are never referenced by a clip,
        # so they never got a card; the chat avatar and any later appearance
        # need one.  Second appearance is the threshold.  A repair batch leaves
        # them to the lanes (--no-recurring-cards): 诸天, without a lane for
        # weeks, owed 210 of them, and three repair batches of a few episodes
        # each set about drawing - and paying for - all of them.
        if not self.args.no_recurring_cards:
            try:
                from novel_manga.application.assets.recurring import recurring_without_cards
                recurring = {asset_id for _, asset_id, _ in recurring_without_cards(self.novel_dir)}
                if recurring:
                    production_common.log(f"ch{chapter}: cards for recurring off-screen characters {sorted(recurring)}")
                    wanted |= recurring
            except Exception as error:  # noqa: BLE001 - a card is a nicety, not a blocker
                production_common.log(f"ch{chapter}: recurring-card check failed: {type(error).__name__}")
        self.cards.want(wanted)
        rows = self.cards.wait(wanted)  # only this episode's assets, built in parallel by the factory
        row["card_flags"] = [flag for r in rows for flag in r.get("flags", [])]
        row["card_fixes"] = [fix for r in rows for fix in r.get("fixes", [])]
        problems = [f"{r['asset_id']}: {r['status']}" for r in rows if r.get("status") != "built"]
        if problems:
            row["note"] = "cards: " + "; ".join(problems)[:200]
            production_common.log(f"ch{chapter}: card problems {problems[:3]}")

    def review_episode(self, chapter: int) -> None:
        """Automatic clip review; in unattended mode a failed clip gets the reviewer's
        correction appended to its prompt and is regenerated once, then reviewed again."""
        from novel_manga.application.review.episode import review_episode
        row = self.rows[chapter]
        directory = self.episode_dir(chapter)
        review = review_episode(directory)
        feedback_path = directory / "review_feedback.json"
        existing = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.is_file() else {}
        fresh = {clip_id: note for clip_id, note in review["feedback"].items() if clip_id not in existing and note}
        if self.args.unattended and fresh:
            feedback_path.write_text(json.dumps({**existing, **fresh}, ensure_ascii=False, indent=1), encoding="utf-8")
            production_common.log(f"ch{chapter}: regenerating {sorted(fresh)} with the reviewer's corrections")
            self.run([sys.executable, str(production_common.SCRIPTS / "render_clips_thin.py"), "--novel-dir", str(self.novel_dir), "--episode", directory.name, "--workers", str(self.args.workers)], directory / "render.log")
            row["render"] = self.render_status(chapter)
            self.fill_result(chapter)
            review = review_episode(directory)
            row["auto_fixed"] = sorted(fresh)
        row["review_flags"] = review["flags"]
        if review["flags"]:
            production_common.log(f"ch{chapter}: {len(review['flags'])} clip flag(s) remain: {review['flags'][0][:120]}")

    def fill_result(self, chapter: int) -> None:
        report = self.episode_dir(chapter) / "thin_media_report.json"
        if not report.is_file():
            return
        data = json.loads(report.read_text(encoding="utf-8"))
        row = self.rows[chapter]
        row["clips"] = len(data.get("clips", []))
        row["retries"] = sum(max(0, len(c.get("attempts", [])) - 1) for c in data.get("clips", []))
        if data.get("review_feedback"):  # corrections applied by an earlier run count as auto-fixed too
            row["auto_fixed"] = sorted(set(row.get("auto_fixed", [])) | set(data["review_feedback"]))
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
        if self.args.stage == "render":
            # Fresh chapters first: an episode that failed before, retried at the
            # head of every round, would hold the slots while new ones wait.
            def tried_and_failed(chapter: int) -> int:
                report = self.episode_dir(chapter) / "thin_media_report.json"
                return 1 if report.is_file() and self.render_status(chapter) not in {"done", "done_with_warnings"} else 0
            chapters = sorted(chapters, key=lambda ch: (tried_and_failed(ch), ch))
        if not self.args.dry_run and any(self.plan_status(ch) != "planned" or self.args.replan for ch in chapters):
            self.check_qwen()
        futures = []
        with ThreadPoolExecutor(max_workers=max(1, self.args.parallel)) as pool, ThreadPoolExecutor(max_workers=max(1, self.args.plan_parallel)) as planners:
            def plan_one(chapter: int) -> int:
                self.grow(chapter)
                self.plan(chapter)
                return chapter
            # Up to --plan-parallel chapters are planned at once (a chapter's
            # recap may then miss its immediate predecessor); each planned
            # chapter goes to the render pool as soon as it is ready.
            planned_iter = (p.result() for p in [planners.submit(plan_one, ch) for ch in chapters]) if self.args.plan_parallel > 1 else map(plan_one, chapters)
            for chapter in planned_iter:
                if self.rows[chapter].get("plan") in {"planned", "kept"}:
                    futures.append(pool.submit(production_render.render, self, chapter))
                production_reports.volume_checkpoint(self, chapter, chapters)
            for chapter, future in zip([ch for ch in chapters if self.rows[ch].get("plan") in {"planned", "kept"}], futures):
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001 - one episode's thread must not end the batch
                    self.rows[chapter]["note"] = f"render thread failed: {type(error).__name__}: {str(error)[:120]}"
                    production_common.log(f"ch{chapter}: render thread failed ({type(error).__name__}: {str(error)[:120]})")
        self.cards.shutdown()


    # ---- report ----
