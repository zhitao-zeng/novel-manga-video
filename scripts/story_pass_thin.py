#!/usr/bin/env python
"""Read the whole book before scripting any of it.

    story_pass_thin.py --novel-dir outputs/X [--chapters 1-3848] [--scan-workers 6] [--summary-workers 6] [--volume-size 50]

Scripting a chapter needs three things that only exist if the chapters before it
were read in order: a bible that already holds the people who will appear (with
the look from the chapter that introduced them), a recap of the last few
chapters, and the arc of the volumes before.  Planning is slow (minutes per
chapter) and can run in parallel blocks; reading is cheap (seconds) but its
result must be committed in order.  This is the reading pass, kept separate so
the planners can then run as many blocks as the GPUs allow with every chapter
properly warmed up.

Only the *commit* is sequential.  The model calls that do not depend on the
bible run ahead in pools:

  * scan: the chapter's proper names and locations (review_bible_thin.scan_chapter),
    several chapters at once
  * summary + hook per chapter into recap.json, order-free
  * ledger: the entity ledger's reading of the chapter (entity_ledger_thin),
    ahead in the same pool; resolved in chapter order before the commit, so
    the bible grows from the ledger's records instead of a second name scan
  * commit, in chapter order under the novel-wide lock: new names checked
    against the bible as it is *now*, descriptions written for the genuinely
    new ones (review_bible_thin.grow_bible with the scan handed in)
  * a volume summary every --volume-size chapters into volumes.json

Resumable: a chapter whose growth is recorded in bible_growth.json and whose
summary is in recap.json is skipped.
"""
from __future__ import annotations
import ledger_extraction_thin as ledger_extraction
import ledger_resolution_thin as ledger_resolution

import argparse
import fcntl
import json
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_store_thin import Ledger
from ledger_views_thin import build_index  # noqa: E402
from novel_manga.model_client import ask_json
from review_bible_thin import grow_bible, scan_chapter, summarize_volume  # noqa: E402

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


def known_locations(novel_dir: Path) -> list[str]:
    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    return [str(full).split("：", 1)[0].strip() for full in bible.get("locations", [])]


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
    parser.add_argument("--scan-workers", type=int, default=6, help="chapters scanned (names, locations) at once, ahead of the ordered commit")
    parser.add_argument("--summary-workers", type=int, default=6, help="chapter summaries run freely ahead; they are order-free")
    parser.add_argument("--volume-size", type=int, default=50)
    parser.add_argument("--min-chapter-chars", type=int, default=300)
    parser.add_argument("--no-grow", dest="grow", action="store_false", default=True)
    parser.add_argument("--no-ledger", dest="ledger", action="store_false", default=True, help="skip the entity ledger stage")
    parser.add_argument("--write-index", action="store_true", help="also refresh <novel>/entity_index.json from the ledger at the end")
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    meta = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    source = (args.source or Path(meta["source"])).resolve()
    novel = read_novel(source, novel_id=novel_dir.name)
    total = len(novel.episodes)
    chapters = [c for c in parse_chapters(args.chapters, total) if len(novel.episodes[c - 1].source_text) >= args.min_chapter_chars]
    growth_path = novel_dir / "bible_growth.json"
    grown = set(json.loads(growth_path.read_text(encoding="utf-8")).keys()) if growth_path.is_file() else set()
    have_summary = recap_rows(novel_dir)
    to_grow = [c for c in chapters if args.grow and str(c) not in grown]
    to_summarise = [c for c in chapters if c not in have_summary]
    ledger = Ledger(novel_dir) if args.ledger else None
    to_ledger = [c for c in chapters if ledger is not None and not ledger.has_chapter(c)]
    to_commit = sorted(set(to_grow) | set(to_ledger))
    grow_set, ledger_set = set(to_grow), set(to_ledger)
    log(f"{novel_dir.name}: {len(chapters)} chapters ({chapters[0]}-{chapters[-1]}); to grow {len(to_grow)}, to read into the ledger {len(to_ledger)}, to summarise {len(to_summarise)}")

    started = time.monotonic()
    summaries_done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.summary_workers)) as summary_pool, \
            ThreadPoolExecutor(max_workers=max(1, args.scan_workers)) as scan_pool:
        # summaries: submit everything, they are independent of each other
        summary_futures: dict[int, Future] = {
            c: summary_pool.submit(summarize_chapter, c, novel.episodes[c - 1].source_title, novel.episodes[c - 1].source_text)
            for c in to_summarise
        }

        def drain_summaries(block_upto: int | None = None) -> None:
            nonlocal summaries_done
            for index, future in list(summary_futures.items()):
                if future.done() or (block_upto is not None and index <= block_upto):
                    try:
                        write_recap_row(novel_dir, future.result())
                        summaries_done += 1
                    except Exception as error:  # noqa: BLE001 - one chapter's summary must not stop the pass
                        log(f"ch{index}: summary failed: {type(error).__name__}: {str(error)[:120]}")
                    summary_futures.pop(index, None)

        # scans: keep a window of chapters in flight ahead of the ordered commit
        scans: dict[int, Future] = {}
        window = max(1, args.scan_workers) * 2

        def read_ahead(index: int) -> dict:
            """The model calls that need no ordering: the location scan (and the name scan when there is no
            ledger) and the ledger's reading of the chapter."""
            text = novel.episodes[index - 1].source_text
            out = {"scan": None, "raw": None}
            if index in ledger_set:
                out["raw"] = ledger_extraction.extract(ledger, index, text)
            if index in grow_set:
                out["scan"] = scan_chapter(text, known_locations(novel_dir), names=ledger is None)
            return out

        def scan_ahead(position: int) -> None:
            for index in to_commit[position:position + window]:
                if index not in scans:
                    scans[index] = scan_pool.submit(read_ahead, index)

        for position, index in enumerate(to_commit):
            scan_ahead(position)
            try:
                ahead = scans.pop(index).result()
            except Exception as error:  # noqa: BLE001 - fall back to the in-lock scan
                log(f"ch{index}: read-ahead failed ({type(error).__name__}), growing without it")
                ahead = {"scan": None, "raw": None}
            scan = ahead["scan"]
            if index in ledger_set:
                try:
                    summary = ledger_resolution.resolve_chapter(ledger, index, novel.episodes[index - 1].source_text, ahead["raw"], workers=args.scan_workers)
                except Exception as error:  # noqa: BLE001
                    summary, ahead["raw"] = None, None
                    log(f"ch{index}: ledger failed: {type(error).__name__}: {str(error)[:120]}")
                if summary is None:
                    log(f"ch{index}: ledger has no reading" + (f" ({ahead['raw']['error']})" if ahead["raw"] and ahead["raw"].get("error") else ""))
                elif summary["new"] or summary["claims"]:
                    log(f"ch{index}: ledger +{len(summary['new'])} {summary['new'][:5]} claims {dict(summary['claims'])}")
            if index not in grow_set:
                continue
            if ledger is not None and scan is not None:
                if ledger.has_chapter(index):
                    scan["names"] = ledger.scan_rows(index)
                else:
                    scan = None  # no reading for this chapter: grow_bible falls back to its own scan
            try:
                result = grow_bible(novel_dir, novel.episodes[index - 1].source_text, index, scan=scan)
                if ledger is not None:
                    ledger.link_cards()
                added = (result.get("characters") or []) + [str(x).split("：", 1)[0] for x in (result.get("locations") or [])]
                if added:
                    log(f"ch{index}: bible +{len(added)} {added[:6]}")
            except Exception as error:  # noqa: BLE001
                log(f"ch{index}: growth failed: {type(error).__name__}: {str(error)[:120]}")
            drain_summaries()
            if index % args.volume_size == 0:
                drain_summaries(block_upto=index)
                first = index - args.volume_size + 1
                try:
                    arc = summarize_volume(novel_dir, first, index)
                    log(f"volume {first}-{index}: {str(arc.get('summary', ''))[:60]}…")
                except Exception as error:  # noqa: BLE001
                    log(f"volume {first}-{index} failed: {type(error).__name__}: {str(error)[:120]}")
            if position % 20 == 19:
                elapsed = time.monotonic() - started
                log(f"progress {position + 1}/{len(to_commit)} (ch{index}), {(position + 1) / elapsed * 3600:.0f} chapters/h, {summaries_done} summaries written")
        drain_summaries(block_upto=10 ** 9)
        # volumes whose chapters were all summarised earlier (resumed run) but never condensed
        volumes_path = novel_dir / "volumes.json"
        have_volumes = {int(v.get("to", 0)) for v in (json.loads(volumes_path.read_text(encoding="utf-8")) if volumes_path.is_file() else [])}
        summarised = recap_rows(novel_dir)
        for end in range(args.volume_size, (chapters[-1] // args.volume_size) * args.volume_size + 1, args.volume_size):
            first = end - args.volume_size + 1
            if end in have_volumes or not all(c in summarised for c in range(first, end + 1) if c in chapters):
                continue
            try:
                summarize_volume(novel_dir, first, end)
                log(f"volume {first}-{end} written")
            except Exception as error:  # noqa: BLE001
                log(f"volume {first}-{end} failed: {type(error).__name__}: {str(error)[:120]}")
    if ledger is not None:
        ledger.save()
        index = build_index(novel_dir)
        atomic_write_json(novel_dir / "entity" / "index.json", index)
        if args.write_index:
            atomic_write_json(novel_dir / "entity_index.json", index)
        log(f"ledger: {sum(1 for e in ledger.entities if e['status'] == 'active')} records, {sum(1 for c in ledger.claims if c.get('status') == 'pending')} claims for a person, index {'written' if args.write_index else 'kept aside'}")
    log(f"story pass done: {len(chapters)} chapters in {(time.monotonic() - started) / 60:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
