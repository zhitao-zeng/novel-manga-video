"""ledger_store_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from pathlib import Path
import json
import threading

import novel_manga.episodes as ep_names


def store(novel_dir: Path) -> Path:
    path = Path(novel_dir) / "entity"
    for sub in ("mentions", "raw", "relations"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def chapter_texts(novel_dir: Path) -> dict[int, str]:
    """Chapter text as the planned episodes hold it (segments.json), for chapters that were planned.  A chapter
    cut into parts is read back from its parts in order - the cut is the later decision, so a segments.json its
    own directory kept from before the cut does not stand in for them."""
    novel_dir = Path(novel_dir)
    whole: dict[int, str] = {}
    parts: dict[int, list[tuple[int, str]]] = {}
    for path in novel_dir.glob(f"{novel_dir.name}_*/segments.json"):
        parsed = ep_names.parse_episode(path.parent.name)
        if parsed is None:
            continue
        rows = read_json(path, [])
        text = "\n".join(str(r.get("text") or "") for r in rows if isinstance(r, dict))
        chapter, part = parsed
        if part is None:
            whole[chapter] = text
        else:
            parts.setdefault(chapter, []).append((part, text))
    return {**whole, **{chapter: "\n".join(t for _, t in sorted(rows)) for chapter, rows in parts.items()}}


def novel_texts(novel_dir: Path) -> dict[int, str]:
    """Every chapter's source text, planned or not: from the novel file named in novel.json, else the segments."""
    novel_dir = Path(novel_dir)
    meta = read_json(novel_dir / "novel.json", {})
    try:
        from novel_manga.ingest import read_novel
        novel = read_novel(Path(meta["source"]).resolve(), novel_id=novel_dir.name)
        return {ep.index: ep.source_text for ep in novel.episodes}
    except Exception:  # noqa: BLE001 - no novel.json or source file: fall back to what was planned
        return chapter_texts(novel_dir)


def segment_texts(novel_dir: Path, chapter: int) -> dict[str, str]:
    novel_dir = Path(novel_dir)
    rows = read_json(novel_dir / f"{novel_dir.name}_{chapter}" / "segments.json", [])
    return {str(r.get("segment_id")): str(r.get("text") or "") for r in rows if isinstance(r, dict)}


def card_ids(novel_dir: Path) -> dict[str, str]:
    return {row["name"]: row["asset_id"] for row in read_json(Path(novel_dir) / "series_assets" / "manifest.json", {}).get("characters", [])
            if row.get("name") and row.get("asset_id")}


def seed_entities(novel_dir: Path) -> list[dict]:
    """The bible's cast keeps its ids and cards; everything the book adds comes after."""
    bible = read_json(Path(novel_dir) / "story_bible.json", {})
    cards = card_ids(novel_dir)
    entities = []
    for number, c in enumerate(bible.get("characters", []), start=1):
        name = str(c.get("name") or "").strip()
        if name:
            entities.append({"id": f"e{number:03d}", "canonical": name, "kind": "person", "asset_id": cards.get(name),
                             "role": str(c.get("role") or "")[:40], "description": str(c.get("appearance") or "")[:80],
                             "named": True, "source": "bible", "status": "active", "merged_into": None})
    return entities


class Ledger:
    """State under <novel>/entity/: entities (records, merged ones kept with merged_into), claims, merges, dropped,
    and one mentions file per chapter.  Extraction may run in a pool ahead of the ordered resolution; the
    snapshot it takes of the candidates is guarded by a lock, so is every mutation."""

    def __init__(self, novel_dir: Path):
        self.novel_dir = Path(novel_dir)
        self.base = store(self.novel_dir)
        self.lock = threading.Lock()
        self.entities: list[dict] = read_json(self.base / "entities.json", None) or seed_entities(self.novel_dir)
        self.claims: list[dict] = read_json(self.base / "claims.json", [])
        self.merges: list[dict] = read_json(self.base / "merges.json", [])
        self.dropped: list[dict] = read_json(self.base / "dropped.json", [])
        # a person's decisions live apart from what the model produced, and are applied again whenever a chapter
        # is re-read - the model's output is never edited in place (the same boundary graph-every-novel and the
        # screenplay analyzer keep: raw analysis kept, corrections separate, views recomputed)
        self.corrections: list[dict] = read_json(self.base / "corrections.json", [])
        self.asked: dict[str, str] = read_json(self.base / "asked.json", {})  # "eA|eB" -> same/different/unsure, asked once
        self.by_id = {e["id"]: e for e in self.entities}
        self.by_name: dict[str, dict] = {}
        self.forms: dict[str, set[str]] = {e["id"]: {e["canonical"]} for e in self.entities}
        self.recent: dict[str, int] = {}  # entity -> last chapter it was on stage or a voice
        for path in sorted((self.base / "mentions").glob("ch_*.json")):
            for m in read_json(path, []):
                if m.get("kind") == "proper" and m.get("entity") in self.forms:
                    self.forms[m["entity"]].add(m["form"])
                if m.get("presence") != "mentioned" and str(m.get("entity", "")).startswith("e"):
                    self.recent[m["entity"]] = max(self.recent.get(m["entity"], 0), int(m.get("chapter", 0)))
        for e in self.entities:  # a merged record's forms belong to its survivor
            if e.get("merged_into"):
                self.forms[self.canonical(e["id"])] |= self.forms.pop(e["id"], set())
        for e in self.entities:
            self.by_name.setdefault(e["canonical"], self.by_id[self.canonical(e["id"])])
        self.link_cards()

    # ---- lookups
    def canonical(self, eid: str) -> str:
        seen = set()
        while self.by_id.get(eid, {}).get("merged_into") and eid not in seen:
            seen.add(eid)
            eid = self.by_id[eid]["merged_into"]
        return eid

    def name_of(self, ref: str) -> str:
        return self.by_id.get(ref, {}).get("canonical", ref)

    def raw_path(self, chapter: int) -> Path:
        return self.base / "raw" / f"ch_{chapter:04d}.json"

    def mentions_path(self, chapter: int) -> Path:
        return self.base / "mentions" / f"ch_{chapter:04d}.json"

    def relations_path(self, chapter: int) -> Path:
        return self.base / "relations" / f"ch_{chapter:04d}.json"

    def has_chapter(self, chapter: int) -> bool:
        return self.mentions_path(chapter).is_file()

    def chapters_read(self) -> list[int]:
        return sorted(int(p.stem[3:]) for p in (self.base / "mentions").glob("ch_*.json") if p.stem[3:].isdigit())

    def link_cards(self) -> None:
        cards = card_ids(self.novel_dir)
        for e in self.entities:
            if not e.get("asset_id") and e["canonical"] in cards:
                e["asset_id"] = cards[e["canonical"]]

    def _add_entity(self, name: str, kind: str, named: bool, description: str, chapter: int, evidence: str = "", span=None) -> dict:
        entity = {"id": f"e{len(self.entities) + 1:03d}", "canonical": name, "kind": kind, "asset_id": None, "role": "",
                  "description": description, "named": named, "source": f"ch{chapter}", "status": "active", "merged_into": None}
        if evidence:
            entity["evidence"], entity["span"] = evidence, list(span or [])
        self.entities.append(entity)
        self.by_id[entity["id"]] = entity
        self.by_name[name] = entity
        self.forms[entity["id"]] = {name}
        return entity

    def _resolve_ref(self, ref: str, local_new: dict[str, str]) -> str | None:
        ref = (ref or "").strip()
        if ref in self.by_id:
            return self.canonical(ref)
        name = ref[4:].strip() if ref.startswith("NEW:") else ref
        if name in local_new:
            return local_new[name]
        if name in self.by_name:
            return self.canonical(self.by_name[name]["id"])
        return None

    # ---- stages


    def relations_through(self, chapter: int) -> list[dict]:
        """Every relation row read so far up to and including this chapter, endpoints canonical."""
        rows = []
        for c in self.chapters_read():
            if c > chapter:
                break
            for r in read_json(self.relations_path(c), []):
                rows.append({**r, "from": self.canonical(r["from"]), "to": self.canonical(r["to"])})
        return rows


    def scan_rows(self, chapter: int) -> list[dict]:
        """The chapter's people in the shape grow_bible reads (name, kind, mentions, speaks_or_close_up)."""
        rows: dict[str, dict] = {}
        for m in read_json(self.mentions_path(chapter), []):
            if not str(m.get("entity", "")).startswith("e"):
                continue
            eid = self.canonical(m["entity"])
            e = self.by_id.get(eid)
            if e is None:
                continue
            row = rows.setdefault(eid, {"name": e["canonical"], "kind": "具名角色" if e.get("named", True) else "称呼或身份",
                                        "mentions": 0, "speaks_or_close_up": False})
            row["mentions"] += int(m.get("count", 1))
            row["speaks_or_close_up"] = row["speaks_or_close_up"] or m.get("presence") in ("on_stage", "voice")
        return list(rows.values())

    def save(self) -> None:
        with self.lock:
            write_json(self.base / "entities.json", self.entities)
            write_json(self.base / "claims.json", self.claims)
            write_json(self.base / "merges.json", self.merges)
            write_json(self.base / "dropped.json", self.dropped)
            write_json(self.base / "corrections.json", self.corrections)
            write_json(self.base / "asked.json", self.asked)
