"""Load one chapter context for all of its local repair candidates."""
from __future__ import annotations

from dataclasses import dataclass
from novel_manga.story.catalog import IdentityCatalog
from pathlib import Path
from novel_manga.planning.context import PlannerContext
from novel_manga.application.planning.context import ledger_cast, ledger_snapshot_for
from novel_manga.application.identity.store import load_chapter, ChapterIdentityData

@dataclass
class RepairChapter:
    episode_dir: Path
    bible: dict
    names: list
    cast_here: dict
    by_index: dict
    segments: dict
    snapshot: dict
    identity_reading: dict
    identity_data: ChapterIdentityData
    protected_bindings: dict
    catalog: IdentityCatalog
    identities: dict


def prepare_context(novel_dir, index, script, segments, *, identity=False, identities=None):
    planner_ctx = PlannerContext.from_env()
    episode_dir = novel_dir / f"{novel_dir.name}_{index}"
    identities = identities or {}
    identity_data = load_chapter(episode_dir)
    bible = identity_data.catalog.bible
    bible_names = [c["name"] for c in bible.get("characters", []) if c.get("name")]
    cast_here = ledger_cast(novel_dir, index)
    present = [n for n in bible_names if cast_here.get(n) in ("on_stage", "voice")]
    in_script = [n for s in script.get("shots", []) for n in s.get("characters", [])]
    if identity:
        resolved_old = {old for mapping in identities.values() for old in mapping} - set(in_script)
        present = [name for name in present if name not in resolved_old]
    leads = [c["name"] for c in bible.get("characters", []) if "主角" in str(c.get("role", ""))]
    from novel_manga.application.planning.context import load_entity_index
    from novel_manga.planning.cast import mentioned_characters
    from novel_manga.application.identity.flow import resolve_chapter
    identity_reading = resolve_chapter(episode_dir, data=identity_data)
    from novel_manga.application.identity.dialogue import apply_confirmed_speakers
    protected_bindings = apply_confirmed_speakers(episode_dir, script['shots'])
    catalog = identity_data.catalog
    load_entity_index(novel_dir, index, ctx=planner_ctx, identity_data=identity_data)
    source_names = mentioned_characters('\n'.join(segments.values()), bible_names, ctx=planner_ctx)
    resolved_names = [identity_reading['entities'].get(m['entity_id']) for m in identity_reading['mentions']
                      if m.get('presence') in {'on_stage','voice'} and m['entity_id'] != 'UNKNOWN']
    names = list(dict.fromkeys([*(n for n in resolved_names if n in bible_names), *leads, *source_names, *present, *in_script]))[:40]
    if identity_reading.get('actorless_confirmed'):
        names = []
    from novel_manga.application.identity.context import typed_entities
    entity_types = typed_entities(novel_dir, identity_reading, data=identity_data)
    names = [n for n in names if entity_types.get(n, {}).get('kind') != 'object']
    if any(t.get('speaker_name')=='无名群声' and t.get('delivery_mode')=='offscreen_dialogue'
           for shot in script.get('shots',[]) for t in shot.get('turns',[])):
        names = list(dict.fromkeys([*names,'无名群声']))
    # clip_plan addresses the prepared shot's index. origin_index can survive an
    # older split/merge and need not equal that address; retain it in the script.
    by_index = {int(s.get("index", i)): s for i, s in enumerate(script.get("shots", []), 1)}
    seg_rows = [{"segment_id": k, "text": v} for k, v in segments.items()]
    snapshot = ledger_snapshot_for(novel_dir, index, seg_rows, cast_here, names) if cast_here else {}
    return RepairChapter(episode_dir, bible, names, cast_here, by_index, segments, snapshot, identity_reading, identity_data, protected_bindings, catalog, identities)
