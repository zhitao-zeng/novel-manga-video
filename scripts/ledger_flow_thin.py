"""ledger_flow_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import ledger_extraction_thin as ledger_extraction
import ledger_resolution_thin as ledger_resolution
import ledger_store_thin as ledger_store

def run_extract(novel_dir: Path, chapters: list[int], workers: int, texts: dict[int, str] | None = None) -> None:
    ledger = ledger_store.Ledger(novel_dir)
    texts = texts or ledger_store.novel_texts(novel_dir)
    todo = [c for c in chapters if c in texts and not (ledger.raw_path(c).is_file() and not ledger_store.read_json(ledger.raw_path(c), {}).get("error"))]
    print(f"{Path(novel_dir).name}: 抽取 {len(todo)} 章（已有 {len(chapters) - len(todo)}），{workers} 路", flush=True)
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for done, (c, raw) in enumerate(zip(todo, pool.map(lambda c: ledger_extraction.extract(ledger, c, texts[c]), todo)), start=1):
            if raw.get("error"):
                print(f"  ch{c}: 出错 {raw['error']}", flush=True)
            if done % 25 == 0 or done == len(todo):
                print(f"  …{done}/{len(todo)} 章，{done / max(time.time() - started, 1) * 60:.1f} 章/分", flush=True)


def run_resolve(novel_dir: Path, chapters: list[int], workers: int, texts: dict[int, str] | None = None) -> None:
    ledger = ledger_store.Ledger(novel_dir)
    texts = texts or ledger_store.novel_texts(novel_dir)
    for c in chapters:
        if c not in texts:
            continue
        summary = ledger_resolution.resolve_chapter(ledger, c, texts[c], workers=workers)
        if summary:
            print(f"  ch{c}: 指称 {summary['mentions']}，新记录 {summary['new'][:5]}，判断 {dict(summary['claims'])}，丢弃 {summary['dropped']}", flush=True)
