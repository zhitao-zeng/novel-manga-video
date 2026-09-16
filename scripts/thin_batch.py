"""thin_batch responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from novel_manga.util import load_dotenv
import argparse
import sys
import json
import os
import production_common_thin as production_common
import production_flow_thin as production_flow
import production_render_thin as production_render
import production_reports_thin as production_reports

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", required=True, help="outputs/<novel-id> created by build_bible_thin.py")
    parser.add_argument("--chapters", default="1", help='e.g. "1", "2-10", "1,3,5-7"')
    parser.add_argument("--source", help="novel text; defaults to the path recorded in novel.json")
    parser.add_argument("--title", help="cover title; defaults to novel.json")
    parser.add_argument("--stage", choices=("all", *production_common.PLAN_STAGES), default="all")
    parser.add_argument("--parallel", type=int, default=3, help="episodes rendered at the same time")
    parser.add_argument("--workers", type=int, default=0, help="clips submitted at once per episode; 0 = one slot per clip (the global --inflight cap still applies)")
    parser.add_argument("--max-redo", type=int, default=2, help="planner redo rounds")
    parser.add_argument("--min-seconds", type=float, default=0.0, help="planner floor for the episode estimate")
    parser.add_argument("--notes-json", help='director notes per chapter: {"3": "...", "*": "for every chapter"}')
    parser.add_argument("--plan-mode", type=int, choices=(15, 30), default=None,
                        help="render only episodes whose clip plan has this clip length (guards a lane against the other mode's plans)")
    parser.add_argument("--replan", action="store_true", help="re-plan chapters that already have a clip plan")
    parser.add_argument("--rerender", action="store_true", help="re-render episodes that already have a final video")
    parser.add_argument("--unattended", action="store_true", help="automatic reviews with bounded paid fixes: cards after the assets stage (one redraw/regeneration), clips after each render (one regeneration with the reviewer's correction); then delivery_report.md")
    parser.add_argument("--review-only", action="store_true", help="run the automatic reviews and write delivery_report.md without any paid fix")
    parser.add_argument("--cache-only", action="store_true", help="with --stage render --rerender: rebuild finals from the clips already rendered (after an assembly fix); never generates anything, and an episode with a clip missing from the cache is left as it is")
    parser.add_argument("--retake-failed", action="store_true", help="give the gate-failed clips of finals fresh takes (always on a local-H3 lane; on a paid lane only for a batch a person approved - every take is paid for)")
    parser.add_argument("--resubmit-unconfirmed", action="store_true", help="send again the submissions recorded as unconfirmed (the service may have created them): only after checking the bill")
    parser.add_argument("--no-recurring-cards", action="store_true", help="build only the cards this episode references, not the whole novel's backlog of recurring off-screen characters (repair batches)")
    parser.add_argument("--no-render", action="store_true", help="with --stage render: review the episodes that are already done and render nothing (the conductor's review jobs run without the novel's render key)")
    parser.add_argument("--no-card-review", dest="card_review", action="store_false", default=True,
                        help="skip judging cards before rendering (by default every card is judged once it is built and fixed once if flagged)")
    parser.add_argument("--grow-bible", dest="grow_bible", action="store_true", default=True, help="before planning a chapter, add its new proper-named characters and locations to the bible (default)")
    parser.add_argument("--no-grow-bible", dest="grow_bible", action="store_false")
    parser.add_argument("--prune", action="store_true", help="after an episode is assembled, delete its intermediate audio, stale clips and review frames (keeps clip.mp4 + asr.json for the cache)")
    parser.add_argument("--volume-size", type=int, default=50, help="write volume_review_N.md every N chapters (bible growth, suggestions, flags)")
    parser.add_argument("--min-chapter-chars", type=int, default=300, help="chapters shorter than this (author notes) are skipped")
    parser.add_argument("--merge", type=int, default=1, help="chapters per episode (episode k = chapters (k-1)*N+1..k*N); --chapters then counts episodes")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier; fast = no think-pass/redo, ~60 s, 480p, single attempt, no episode review")
    parser.add_argument("--plan-parallel", type=int, default=1, help="chapters planned at the same time (needs a Qwen service with --max-num-seqs > 1)")
    parser.add_argument("--card-parallel", type=int, default=6, help="asset cards built at the same time (one process per asset)")
    parser.add_argument("--inflight", type=int, default=20, help="global cap on clips in flight across all rendering episodes (0 = none)")
    parser.add_argument("--no-prescreen", dest="prescreen", action="store_false", default=True, help="skip the local content-filter prescreen of prompts")
    parser.add_argument("--no-moderation-repair", dest="moderation_repair", action="store_false", default=True, help="skip the bisect-and-rewrite rescue of prompts the text filter refuses")
    parser.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    args = parser.parse_args()

    load_dotenv(production_common.ROOT / ".env")
    batch = production_flow.Batch(args)
    chapters = production_common.parse_chapters(args.chapters)
    batch.rows = {chapter: {} for chapter in chapters}
    production_common.log(f"{batch.novel_id}: chapters {chapters[0]}..{chapters[-1]} ({len(chapters)}), stage {args.stage}, source {batch.source.name}")
    if args.stage == "all":
        batch.stream(chapters)  # plan one chapter, render it while the next is planned
        return production_reports.report(batch, chapters)
    if args.stage == "plan":
        if not args.dry_run and any(batch.plan_status(ch) != "planned" or args.replan for ch in chapters):
            batch.check_qwen()
        batch.plan_stage(chapters)
    if args.stage == "assets":
        batch.assets(chapters)
    if args.stage == "render":
        if args.plan_mode:
            def clip_plan_mode(chapter: int) -> int | None:
                path = batch.episode_dir(chapter) / "clip_plan.json"
                try:
                    policy = str(json.loads(path.read_text(encoding="utf-8")).get("policy", ""))
                except (OSError, ValueError):
                    return None
                return 15 if policy.endswith("-15s") else 30
            wrong = [ch for ch in chapters if clip_plan_mode(ch) not in (None, args.plan_mode)]
            if wrong:
                production_common.log(f"skipping {len(wrong)} episodes planned for the other clip length: {wrong[:8]}"
                    f"{' ...' if len(wrong) > 8 else ''}")
                chapters = [ch for ch in chapters if ch not in set(wrong)]
        if os.environ.get("NOVEL_LOCAL_H3_URL"):
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
        chapters = sorted(chapters, key=lambda ch: (tried_and_failed(ch), ch))
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            for chapter, future in [(ch, pool.submit(production_render.render, batch, ch)) for ch in chapters]:
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001
                    batch.rows[chapter]["note"] = f"render thread failed: {type(error).__name__}: {str(error)[:120]}"
                    production_common.log(f"ch{chapter}: render thread failed ({type(error).__name__}: {str(error)[:120]})")
    return production_reports.report(batch, chapters)


if __name__ == "__main__":
    sys.exit(main())
