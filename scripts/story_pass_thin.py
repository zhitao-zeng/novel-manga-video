#!/usr/bin/env python
"""Read the whole book before scripting any of it.

    story_pass_thin.py --novel-dir outputs/X [--chapters 1-3848] [--summary-workers 4] [--volume-size 50]

Scripting a chapter needs three things that only exist if the chapters before it
were read in order: a bible that already holds the people who will appear (with
the look from the chapter that introduced them), a recap of the last few
chapters, and the arc of the volumes before.  Planning is slow (minutes per
chapter) and can run in parallel blocks; reading is cheap (seconds) but must be
sequential.  This is the reading pass, kept separate so the planners can then run
as many blocks as the GPUs allow with every chapter properly warmed up:

  * bible growth for every chapter, in order (new characters, locations,
    aliases - through thin_review.grow_bible, under the novel-wide lock)
  * a summary + hook per chapter into recap.json (order-free, so a small pool
    runs these ahead of the growth loop)
  * a volume summary every --volume-size chapters into volumes.json

Resumable: a chapter whose growth is recorded in bible_growth.json and whose
summary is in recap.json is skipped.  Safe to run alongside a block-0 planner.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_review import ask_json, grow_bible, summarize_volume  # noqa: E402

from novel_manga.ingest import read_novel  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402

SUMMARY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "hook"],
    "properties": {"summary": {"type": "string"}, "hook": {"type": "string"}},
}


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def parse_chapters(spec: str, total: int) -> list[int]:
    chapters: set[int] = set()
    for piece in spec.split(","):
        piece = piece.strip()
        if "-" in piece:
            start, stop = piece.split("-")
            chapters.update(range(int(start), min(int(stop), total) + 1))
        elif piece:
            chapters.add(int(piece))
    return sorted(c for c in chapters if 1 <= c <= total)


def recap_rows(novel_dir: Path) -> dict[int, dict]:
    path = novel_dir / "recap.json"
    if not path.is_file():
        return {}
    return {int(row.get("chapter", 0)): row for row in json.loads(path.read_text(encoding="utf-8"))}


def write_recap_row(novel_dir: Path, row: dict) -> None:
    path = novel_dir / "recap.json"
    with open(path.with_suffix(".lock"), "w") as lock:  # planners write the same file
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        rows = [r for r in rows if int(r.get("chapter", 0)) != int(row["chapter"])]
        rows.append(row)
        atomic_write_json(path, sorted(rows, key=lambda r: int(r.get("chapter", 0))))


def summarize_chapter(index: int, title: str, text: str) -> dict:
    """One short model call: what happened, and the cliff it leaves."""
    verdict = ask_json([{"type": "text", "text": (
        f"下面是长篇小说的第 {index} 章《{title}》原文。写两样东西，供后面章节的改编保持连续性：\n"
        "summary：120 到 200 字，按发生顺序写这一章发生了什么、人物关系和处境有什么变化、人物现在在哪里；只写原文有的事实，用人物的正名。\n"
        "hook：一句话，这一章结尾留下的悬念或下一章的引子。\n\n" + text[:14000])}],
        SUMMARY_SCHEMA, name="chapter_summary", max_tokens=700)
    return {"chapter": index, "title": title, "summary": str(verdict.get("summary", "")).strip(), "hook": str(verdict.get("hook", "")).strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="defaults to the path in novel.json")
    parser.add_argument("--chapters", default="1-100000")
    parser.add_argument("--summary-workers", type=int, default=4, help="chapter summaries run ahead of the growth loop")
    parser.add_argument("--volume-size", type=int, default=50)
    parser.add_argument("--min-chapter-chars", type=int, default=300)
    parser.add_argument("--no-grow", dest="grow", action="store_false", default=True)
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    meta = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    source = (args.source or Path(meta["source"])).resolve()
    novel = read_novel(source, novel_id=novel_dir.name)
    total = len(novel.episodes)
    chapters = parse_chapters(args.chapters, total)
    growth_path = novel_dir / "bible_growth.json"
    grown = set(json.loads(growth_path.read_text(encoding="utf-8")).keys()) if growth_path.is_file() else set()
    have_summary = recap_rows(novel_dir)
    log(f"{novel_dir.name}: {len(chapters)} chapters ({chapters[0]}-{chapters[-1]}), {len(grown)} already grown, {len(have_summary)} already summarised")

    started = time.monotonic()
    pending: dict[int, Future] = {}
    done_summaries = 0
    with ThreadPoolExecutor(max_workers=max(1, args.summary_workers)) as pool:
        def summarise_ahead(upto: int) -> None:
            """Keep summaries queued a little ahead of the growth loop."""
            for index in chapters:
                if index > upto:
                    break
                if index in have_summary or index in pending:
                    continue
                episode = novel.episodes[index - 1]
                if len(episode.source_text) < args.min_chapter_chars:
                    continue
                pending[index] = pool.submit(summarize_chapter, index, episode.source_title, episode.source_text)

        def collect(block: bool = False) -> None:
            nonlocal done_summaries
            for index, future in list(pending.items()):
                if block or future.done():
                    try:
                        write_recap_row(novel_dir, future.result())
                        done_summaries += 1
                    except Exception as error:  # noqa: BLE001 - one chapter's summary must not stop the pass
                        log(f"ch{index}: summary failed: {type(error).__name__}: {str(error)[:120]}")
                    pending.pop(index, None)

        for position, index in enumerate(chapters):
            episode = novel.episodes[index - 1]
            if len(episode.source_text) < args.min_chapter_chars:
                continue
            summarise_ahead(index + 2 * args.summary_workers)
            if args.grow and str(index) not in grown:
                try:
                    result = grow_bible(novel_dir, episode.source_text, index)
                    added = (result.get("characters") or []) + (result.get("locations") or [])
                    if added:
                        log(f"ch{index}: bible +{len(added)} {added[:6]}")
                except Exception as error:  # noqa: BLE001
                    log(f"ch{index}: growth failed: {type(error).__name__}: {str(error)[:120]}")
                grown.add(str(index))
            collect()
            if index % args.volume_size == 0:
                collect(block=True)
                first = index - args.volume_size + 1
                try:
                    arc = summarize_volume(novel_dir, first, index)
                    log(f"volume {first}-{index}: {str(arc.get('summary', ''))[:60]}…")
                except Exception as error:  # noqa: BLE001
                    log(f"volume {first}-{index} failed: {type(error).__name__}: {str(error)[:120]}")
            if position % 20 == 19:
                elapsed = time.monotonic() - started
                rate = (position + 1) / elapsed * 3600
                log(f"progress {position + 1}/{len(chapters)} (ch{index}), {rate:.0f} chapters/h, {done_summaries} summaries written")
        collect(block=True)
    log(f"story pass done: {len(chapters)} chapters in {(time.monotonic() - started) / 60:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
