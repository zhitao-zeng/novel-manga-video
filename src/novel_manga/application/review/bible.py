"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import re
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
import novel_manga.story.identity as story_names
import novel_manga.story.name_rules as name_rules
import novel_manga.review.contracts as review_contracts
from novel_manga.util import atomic_write_json


import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.identity.reading_roster as service_reading_roster
import novel_manga.application.review.entity_extraction as service_entity_extraction
import novel_manga.application.assets.character_design as service_character_design


def review_bible(novel_dir: Path, source: Path, fill: bool, chapters: int | None = None) -> dict:
    """Named characters the novel keeps mentioning that the bible lacks.

    Generic words (少年, 老者, 父亲…) never count: the planner casts such
    people as anonymous roles or background.  A candidate must be a proper
    name, or a recurring appellation that speaks or gets close-ups.  Filling
    is conservative: an entry is added only when the model is confident it is
    not one of the existing characters under another name and can describe
    appearance and wardrobe concretely; everything else is reported for a
    human to decide.
    """
    from novel_manga.ingest import read_novel, chapter_table
    bible_path = novel_dir / "story_bible.json"
    bible = review_models_StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    novel = read_novel(source, novel_id=novel_dir.name, title=bible.novel_title, chapters=chapter_table(novel_dir))
    counts: dict[str, dict] = {}
    for episode in novel.episodes[:chapters] if chapters else novel.episodes:
        for row in service_entity_extraction.extract_names(episode.source_text):
            name = re.sub(r"\s+", "", str(row.get("name", "")))
            if row.get("kind") == "群体" or not name or name in name_rules.GENERIC_NAMES:
                continue
            entry = counts.setdefault(name, {"mentions": 0, "chapters": [], "kind": row.get("kind"), "speaks": False})
            entry["mentions"] += int(row.get("mentions", 0))
            entry["chapters"].append(episode.index)
            entry["speaks"] = entry["speaks"] or bool(row.get("speaks_or_close_up"))
            if row.get("kind") == "具名角色":
                entry["kind"] = "具名角色"
        model_client.log(f"bible: chapter {episode.index} scanned, {len(counts)} names so far")
    known = [c.name for c in bible.characters]
    missing = {
        name: info for name, info in counts.items()
        if info["mentions"] >= review_contracts.MIN_MENTIONS and not story_names.name_matches(name, known) and (info["kind"] == "具名角色" or info["speaks"])
    }
    # "or it speaks" lets a one-scene driver into the bible with a card of its own, because growth
    # only ever sees one chapter and cannot tell that he never comes back.  A reading of the whole
    # book can, so when there is one, it decides.
    roster = service_reading_roster.reading_roster(novel_dir)
    if roster:
        turned_down = [name for name in missing if not story_names.name_matches(name, roster)]
        missing = {name: info for name, info in missing.items() if name not in turned_down}
        if turned_down:
            model_client.log(f"bible: reading says these are extras, not adding: {turned_down}")
    report = {"policy": review_contracts.POLICY, "bible_characters": known, "extracted": counts, "missing": missing, "filled": [], "needs_human": {}, "suggestions": {}}
    if fill and missing:
        bible, report["filled"], report["needs_human"], report["suggestions"] = service_character_design.fill_characters(
            bible, bible_path, missing, novel.text, service_reading_roster.reading_decisions(novel_dir)["cast"])
    atomic_write_json(novel_dir / "bible_review.json", report)
    model_client.log(f"bible: missing {list(missing) or 'none'}; added {report['filled'] or 'nothing'}; for a human: {list(report['needs_human']) or 'nothing'}")
    return report
