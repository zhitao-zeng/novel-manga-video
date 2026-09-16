"""planner_context_thin responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.util import atomic_write_json
from pathlib import Path
import fcntl
import json
import novel_manga.planning.constants as pc_constants

def load_separate_pairs(novel_dir: Path) -> list[tuple[str, str]]:
    """Character pairs this novel's review record says the generator cannot tell apart."""
    path = novel_dir / "confusable_pairs.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pairs = [tuple(entry["pair"]) for entry in data.get("pairs", [])
             if entry.get("failed", 0) >= pc_constants.SEPARATE_MIN_FAILURES and entry.get("rate", 0) >= pc_constants.SEPARATE_MIN_RATE]
    return pairs[:pc_constants.SEPARATE_MAX_PAIRS]


def _ledger_files(novel_dir: Path, chapter: int) -> tuple[dict, list[dict], list[dict]] | None:
    """entities, this chapter's mentions and the claims of the entity ledger (entity_ledger_thin), read directly."""
    base = Path(novel_dir) / "entity"
    mentions_path = base / "mentions" / f"ch_{chapter:04d}.json"
    if not mentions_path.is_file():
        return None
    try:
        entities = {e["id"]: e for e in json.loads((base / "entities.json").read_text(encoding="utf-8"))}
        mentions = json.loads(mentions_path.read_text(encoding="utf-8"))
        claims_path = base / "claims.json"
        claims = json.loads(claims_path.read_text(encoding="utf-8")) if claims_path.is_file() else []
    except (OSError, ValueError):
        return None
    return entities, mentions, claims


def _ledger_canonical(entities: dict, eid: str) -> str:
    seen = set()
    while entities.get(eid, {}).get("merged_into") and eid not in seen:
        seen.add(eid)
        eid = entities[eid]["merged_into"]
    return eid


def ledger_cast(novel_dir: Path, chapter: int) -> dict[str, str]:
    """Who the entity ledger puts in this chapter: name -> on_stage / voice / mentioned.  Empty when the ledger has
    not read the chapter (then the old name-form scan decides the candidates)."""
    files = _ledger_files(novel_dir, chapter)
    if files is None:
        return {}
    entities, mentions, _ = files
    rank = {"on_stage": 2, "voice": 1, "mentioned": 0}
    cast: dict[str, str] = {}
    for m in mentions:
        if not str(m.get("entity", "")).startswith("e"):
            continue
        name = entities.get(_ledger_canonical(entities, m["entity"]), {}).get("canonical")
        presence = str(m.get("presence") or "on_stage")
        if name and rank.get(presence, 0) >= rank.get(cast.get(name, "mentioned"), -1):
            cast[name] = presence
    return cast


def ledger_snapshot_for(novel_dir: Path, chapter: int, segments: list[dict], cast: dict[str, str], names: list[str]) -> dict:
    """The passage-level casting sheet handed to the planner: per segment, the offered characters whose written
    forms occur in it; the chapter cast with presence; the relations the reader must not learn yet."""
    files = _ledger_files(novel_dir, chapter)
    if files is None:
        return {}
    entities, mentions, claims = files
    forms: dict[str, set[str]] = {}
    for m in mentions:
        if str(m.get("entity", "")).startswith("e"):
            name = entities.get(_ledger_canonical(entities, m["entity"]), {}).get("canonical")
            if name in names:
                forms.setdefault(name, set()).add(str(m.get("form") or ""))
    per_segment = {}
    for segment in segments:
        text = str(segment.get("text") or "")
        named = [name for name, fs in forms.items() if any(f and f in text for f in fs)]
        per_segment[segment["segment_id"]] = {"named_here": named}
    secrets = [f"{entities.get(c.get('subject'), {}).get('canonical', c.get('subject'))} {c.get('type')} "
               f"{entities.get(c.get('object'), {}).get('canonical', c.get('object'))}"
               for c in claims if c.get("hidden_from_reader") and int(c.get("chapter", 0)) > chapter]
    return {"usage": "原著逐段出场记录：只让 named_here 的人进该区段阶段的 in_frame；chapter_cast 里 voice 的人只发声、mentioned 的人不出现；"
                     "must_not_reveal 的关系不得在台词或画面里点破",
            "chapter_cast": [{"name": n, "presence": p} for n, p in cast.items() if n in names],
            "segments": per_segment, "must_not_reveal": secrets[:8]}


def cast_history(novel_dir: Path) -> dict:
    """{"characters": {name: [chapters]}, "locations": {...}} for this novel.

    Built from the scripts already written when the file is missing, so an
    existing novel does not need a migration step.
    """
    path = novel_dir / "cast_index.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    index: dict[str, dict[str, list[int]]] = {"characters": {}, "locations": {}}
    for script_path in sorted(novel_dir.glob(f"{novel_dir.name}_*/chapter_script.json")):
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
            chapter = int(script.get("episode_index") or script_path.parent.name.rsplit("_", 1)[1])
        except (OSError, ValueError, IndexError):
            continue
        for shot in script.get("shots", []):
            for name in shot.get("characters", []) or []:
                index["characters"].setdefault(str(name), []).append(chapter)
            if shot.get("location"):
                index["locations"].setdefault(str(shot["location"]), []).append(chapter)
    for group in index.values():
        for name, chapters in group.items():
            group[name] = sorted(set(chapters))
    return index


def recent_names(group: dict, chapter: int, window: int) -> set[str]:
    return {name for name, chapters in group.items() if any(chapter - window <= int(c) < chapter for c in chapters)}


def record_cast(novel_dir: Path, chapter: int, characters: list[str], locations: list[str]) -> None:
    """Record who and where this chapter shows, replacing what an earlier plan of it recorded (planners may run
    in parallel).  Adding only kept a character a re-written chapter no longer has as "seen lately" for the next
    three chapters (星海 14 and 诸天 101 entries disagreed with the scripts on 2026-09-11)."""
    path = novel_dir / "cast_index.json"
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = cast_history(novel_dir)
        for group in index.values():
            for name in list(group):
                group[name] = [c for c in group[name] if int(c) != chapter]
                if not group[name]:
                    del group[name]
        for key, values in (("characters", characters), ("locations", locations)):
            for name in values:
                if not name:
                    continue
                chapters = set(index[key].setdefault(str(name), []))
                chapters.add(chapter)
                index[key][str(name)] = sorted(chapters)
        atomic_write_json(path, index)


def load_entity_index(novel_dir: Path, chapter: int | None = None, *, ctx: PlannerContext, identity_data=None) -> bool:
    """entity_index.json (build_entity_index.py): the forms each character is actually called by in this
    book, already unique.  When it is there, name lookups use it instead of guessing."""
    path = Path(novel_dir) / "entity_index.json"
    ctx.entity_forms.clear()
    ctx.entity_tiers.clear()
    ctx.entity_generic.clear()
    ctx.forms_index.clear()
    from identity_context_thin import effective_aliases
    ctx.aliases.clear()
    ctx.aliases.update(effective_aliases(novel_dir, chapter, data=identity_data))
    try:
        index = identity_data.book.index if identity_data is not None else json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for row in index.get("characters", []):
        forms = [f for f in (row.get("forms") or {}) if len(f) >= 2 or f == row.get("name")]
        ctx.entity_forms[row["name"]] = sorted({row["name"], *forms}, key=len, reverse=True)
        ctx.entity_tiers[row["name"]] = str(row.get("tier") or "")
        ctx.entity_generic[row["name"]] = bool(row.get("generic", len(row["name"]) < 3))
    ctx.forms_index.clear()
    return bool(ctx.entity_forms)
