"""ledger_flow_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import novel_manga.application.identity.ledger_extraction as ledger_extraction
import novel_manga.application.identity.ledger_resolution as ledger_resolution
import novel_manga.application.identity.ledger_store as ledger_store

def run_extract(novel_dir: Path, chapters: list[int], workers: int, texts: dict[int, str] | None = None) -> None:
    texts = texts if texts is not None else ledger_store.novel_texts(novel_dir)
    with ledger_store.ledger_lock(novel_dir):
        ledger = ledger_store.Ledger(novel_dir)
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
    texts = texts if texts is not None else ledger_store.novel_texts(novel_dir)
    for c in chapters:
        if c not in texts:
            continue
        with ledger_store.ledger_lock(novel_dir):
            ledger = ledger_store.Ledger(novel_dir)
            summary = ledger_resolution.resolve_chapter(ledger, c, texts[c], workers=workers, texts=texts)
        if summary:
            print(f"  ch{c}: 指称 {summary['mentions']}，新记录 {summary['new'][:5]}，判断 {dict(summary['claims'])}，丢弃 {summary['dropped']}", flush=True)


def adopt_roster(novel_dir: Path, roster: dict, aliases: dict | None = None) -> dict:
    """Adopt only source-backed readings, and publish current views even for a new book."""
    texts = ledger_store.novel_texts(novel_dir)
    with ledger_store.ledger_lock(novel_dir):
        ledger = ledger_store.Ledger(novel_dir)
        known = set(ledger.by_name)
        aliases = {**ledger_store.read_json(Path(novel_dir) / 'bible_aliases.json', {}),
                   **(aliases or {}), **(roster.get('aliases') or {})}
        aliased = {name for name, target in aliases.items() if target in known}
        missing = [name for name in roster.get('characters', []) if name and name not in known and name not in aliased]
        candidates = ledger_extraction.seed_candidates(ledger, missing, texts)
        for name, row in candidates.items():
            ledger._add_entity(name, row['kind'], row['named'], row['description'], row['chapter'], row['evidence'], row['span'])
        ledger.save()
    return {'seeded': list(candidates), 'unresolved': [name for name in missing if name not in candidates]}
