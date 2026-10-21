"""thin_batch responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from novel_manga.util import load_dotenv
import argparse
import sys
import json
import os
import novel_manga.application.production.common as production_common
import novel_manga.application.production.flow as production_flow
import novel_manga.application.production.render as production_render
import novel_manga.application.production.reports as production_reports

import novel_manga.planning.audit as pc_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", required=True, help="outputs/<novel-id> created by build_bible_thin.py")
    parser.add_argument('--workspace-index', help='registered chapters mapped to existing isolated episode books')
    parser.add_argument('--first-pass', action='store_true', help='generate only missing first videos; review is independent')
    parser.add_argument("--chapters", default="1", help='e.g. "1", "2-10", "1,3,5-7"')
    parser.add_argument("--source", help="novel text; defaults to the path recorded in novel.json")
    parser.add_argument("--title", help="cover title; defaults to novel.json")
    parser.add_argument("--stage", choices=("all", *production_common.PLAN_STAGES), default="all")
    parser.add_argument("--parallel", type=int, default=3, help="episodes rendered at the same time")
    parser.add_argument("--workers", type=int, default=0, help="clips submitted at once per episode; 0 = one slot per clip (the global --inflight cap still applies)")
    parser.add_argument("--max-redo", type=int, default=5,
                        help="planner redo rounds; a long chapter needs three or four to come "
                             "down to the episode cap, and only a chapter that failed pays for one")
    parser.add_argument("--min-seconds", type=float, default=0.0, help="planner floor for the episode estimate")
    parser.add_argument("--notes-json", help='director notes per chapter: {"3": "...", "*": "for every chapter"}')
    parser.add_argument("--plan-mode", type=int, choices=(15, 30), default=None,
                        help="render only episodes whose clip plan has this clip length (guards a lane against the other mode's plans)")
    parser.add_argument("--replan", action="store_true", help="re-plan chapters that already have a clip plan")
    parser.add_argument("--rerender", action="store_true", help="re-render episodes that already have a final video")
    parser.add_argument("--unattended", action="store_true", help="use the single-episode render --review workflow and its existing per-clip repair budget; then delivery_report.md")
    parser.add_argument("--review-only", action="store_true", help="review existing video takes and write delivery_report.md; no planning, asset building, generation or repair")
    parser.add_argument("--cache-only", action="store_true", help="with --stage render --rerender: rebuild finals from the clips already rendered (after an assembly fix); never generates anything, and an episode with a clip missing from the cache is left as it is")
    parser.add_argument("--retake-failed", action="store_true", help="give the gate-failed clips of finals fresh takes (always on a local-H3 lane; on a paid lane only for a batch a person approved - every take is paid for)")
    parser.add_argument("--resubmit-unconfirmed", action="store_true", help="send again the submissions recorded as unconfirmed (the service may have created them): only after checking the bill")
    parser.add_argument("--no-recurring-cards", action="store_true", help="build only the cards this episode references, not the whole novel's backlog of recurring off-screen characters (repair batches)")
    parser.add_argument("--no-render", action="store_true", help="with --stage render: review the episodes that are already done and render nothing (the conductor's review jobs run without the novel's render key)")
    parser.add_argument("--no-card-review", dest="card_review", action="store_false", default=True,
                        help="skip judging cards before rendering (by default every card is judged once it is built and fixed once if flagged)")
    parser.add_argument("--grow-bible", dest="grow_bible", action="store_true", default=True, help="before planning a chapter, add its new proper-named characters and locations to the bible (default)")
    parser.add_argument("--no-grow-bible", dest="grow_bible", action="store_false")
    parser.add_argument("--no-eager-cards", dest="eager_cards", action="store_false", default=True,
                        help="grow the bible without starting cards for what it grows: the assets stage then draws only the cast the scripts actually reference. "
                             "Growth adds everyone the chapter names, including people the story only mentions, and their cards are paid for on sight")
    parser.add_argument("--prune", action="store_true", help="after an episode is assembled, delete its intermediate audio, stale clips and review frames (keeps clip.mp4 + asr.json for the cache)")
    parser.add_argument("--volume-size", type=int, default=50, help="write volume_review_N.md every N chapters (bible growth, suggestions, flags)")
    parser.add_argument("--min-chapter-chars", type=int, default=300, help="chapters shorter than this (author notes) are skipped")
    parser.add_argument("--merge", type=int, default=1, help="chapters per episode (episode k = chapters (k-1)*N+1..k*N); --chapters then counts episodes")
    parser.add_argument("--tier", choices=("quality", "fast"), help="legacy alias: expand this tier preset into profile.json's resolution/expressions/episode_shape; prefer setting those keys in the book's profile")
    parser.add_argument("--plan-parallel", type=int, default=1, help="chapters planned at the same time (needs a Qwen service with --max-num-seqs > 1)")
    parser.add_argument('--repair-planning', action='store_true', help='Return failed planning through the existing Agent and native validation; no media')
    parser.add_argument('--agent-config', type=Path, help='Existing sandbox configuration for planning repair')
    parser.add_argument("--card-parallel", type=int, default=6, help="asset cards built at the same time (one process per asset)")
    parser.add_argument("--inflight", type=int, default=20, help="global cap on clips in flight across all rendering episodes (0 = none)")
    parser.add_argument("--no-prescreen", dest="prescreen", action="store_false", default=True, help="skip the local content-filter prescreen of prompts")
    parser.add_argument("--no-moderation-repair", dest="moderation_repair", action="store_false", default=True, help="skip the bisect-and-rewrite rescue of prompts the text filter refuses")
    parser.add_argument("--no-audit", dest="audit", action="store_false", default=True,
                        help="渲染前不做结构检查（地点有没有描写、镜头的地点和角色在不在圣经里、时长在不在 4–15 秒）")
    parser.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    args = parser.parse_args()
    if args.first_pass and not args.workspace_index:
        parser.error('--first-pass requires --workspace-index')
    if args.review_only:
        # This option selects an operation, not merely a reporting preference.
        # It wins over --unattended and the default --stage all.
        args.stage = 'render'
        args.no_render = True
        args.unattended = False

    load_dotenv(production_common.ROOT / ".env")
    batch = production_flow.Batch(args)
    chapters = production_common.parse_chapters(args.chapters)
    batch.rows = {chapter: {} for chapter in chapters}
    production_common.log(f"{batch.novel_id}: chapters {chapters[0]}..{chapters[-1]} ({len(chapters)}), stage {args.stage}, source {batch.source.name}")
    if args.audit and not getattr(batch,'workspaces',None) and not args.dry_run and not args.no_render and args.stage in ("all", "render"):
        problems = pc_audit.blocking_problems(batch.novel_dir)
        for problem in problems:
            production_common.log(f"审查不通过 · {problem.where} · {problem.rule}：{problem.detail}")
        if problems:
            production_common.log(f"{len(problems)} 处问题会让成片出错，先修再渲；确认要照渲就加 --no-audit")
            return 1
    if args.stage == "all":
        batch.stream(chapters)  # plan one chapter, render it while the next is planned
        result = production_reports.report(batch, chapters)
        return 1 if batch.planning_stop.is_set() else result
    if args.stage == "plan":
        if not args.dry_run and any(batch.plan_status(ch) != "planned" or args.replan for ch in chapters):
            batch.check_qwen()
        batch.plan_stage(chapters)
    if args.stage == 'prepare':
        if not batch.workspaces:
            parser.error('--stage prepare requires --workspace-index')
        from .workspaces import prepare
        with ThreadPoolExecutor(max_workers=max(1, args.plan_parallel)) as pool:
            futures = {n: pool.submit(prepare, batch, n) for n in chapters}
            for n, future in futures.items():
                answer = future.result()
                batch.rows[n].update(preparation=answer['status'], note=answer.get('error', ''))
                production_common.log(f'ch{n}: preparation {answer["status"]}')
        return 0
    if args.stage == "assets":
        batch.assets(chapters)
    if args.stage == "render":
        if args.first_pass:
            from .workspaces import chapter as workspace_chapter
            chapters=[n for n in chapters if workspace_chapter(batch.workspaces[str(n)])['ready']]
        if args.plan_mode and not args.no_render:
            def clip_plan_mode(chapter: int) -> int | None:
                path = batch.episode_dir(chapter) / "clip_plan.json"
                try:
                    plan = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
                from .runs import clip_plan_mode as mode_of
                return mode_of(plan)
            wrong = [ch for ch in chapters if clip_plan_mode(ch) not in (None, args.plan_mode)]
            if wrong:
                production_common.log(f"skipping {len(wrong)} episodes planned for the other clip length: {wrong[:8]}"
                    f"{' ...' if len(wrong) > 8 else ''}")
                chapters = [ch for ch in chapters if ch not in set(wrong)]
        if os.environ.get("NOVEL_LOCAL_H3_URL") and not args.no_render:
            # One still holding thin_media_report.h3zh.json is waiting for the keep-check that decides which
            # of its old clips stay.  (One without its English prompts is converted when its turn comes,
            # in Batch.h3_ready: deciding that here, once per lane start, left 1597 waiting for good.)
            waiting = [ch for ch in chapters if (batch.episode_dir(ch) / "thin_media_report.h3zh.json").is_file()]
            if waiting:
                production_common.log(f"skipping {len(waiting)} episodes waiting for the H3 keep-check: {waiting[:8]}"
                    f"{' ...' if len(waiting) > 8 else ''}")
                chapters = [ch for ch in chapters if ch not in set(waiting)]
        free = bool(os.environ.get("NOVEL_LOCAL_H3_URL"))
        # Fresh chapters first: an episode that failed before, retried at the
        # head of every round, would hold the slots while new ones wait - and on
        # a free lane so would a final taken back for fresh takes of its clips.
        def tried_and_failed(chapter: int) -> int:
            if not (batch.episode_dir(chapter) / "thin_media_report.json").is_file():
                return 0
            status = batch.render_status(chapter)
            return 1 if status not in {"done", "done_with_warnings"} or (free and status == "done_with_warnings") else 0
        if not args.no_render:
            chapters = sorted(chapters, key=lambda ch: (tried_and_failed(ch), ch))
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            for chapter, future in [(ch, pool.submit(production_render.render, batch, ch)) for ch in chapters]:
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001
                    batch.rows[chapter]["note"] = f"render thread failed: {type(error).__name__}: {str(error)[:120]}"
                    production_common.log(f"ch{chapter}: render thread failed ({type(error).__name__}: {str(error)[:120]})")
    if args.first_pass:
        return 0  # Per-episode production receipts distinguish ready/blocked from film acceptance.
    result = production_reports.report(batch, chapters)
    if args.stage == 'plan' and args.repair_planning and not args.dry_run:
        incomplete = [n for n in chapters if batch.rows[n].get('plan') not in {'planned', 'kept'}
                      and not str(batch.rows[n].get('plan', '')).startswith('skipped')]
        if incomplete:
            return 1 if batch.planning_stop.is_set() else 2
    return 1 if args.stage == 'plan' and batch.planning_stop.is_set() else result
