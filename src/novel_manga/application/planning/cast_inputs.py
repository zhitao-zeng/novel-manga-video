"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext




from novel_manga.models.bible import StoryBible
import json
import shutil
import sys
import novel_manga.planning.cast as pc_cast
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.parts as pc_parts
import novel_manga.application.planning.context as planner_context


from .run_types import ChapterSource, ChapterCast


def prepare_cast(args, ctx: PlannerContext, source: ChapterSource) -> ChapterCast:
    episode = source.episode
    novel_dir = source.novel_dir
    episode_dir = source.episode_dir
    part = source.part
    full_bible = source.full_bible
    chapter_text = source.chapter_text
    segments = source.segments
    bible_target = novel_dir/'story_bible.json'
    from novel_manga.application.identity.store import load_chapter
    identity_data = load_chapter(episode_dir)
    if not args.dry_run and not args.replay:
        from novel_manga.application.identity.flow import resolve_chapter
        context = resolve_chapter(episode_dir, data=identity_data)
        # Anyone on stage that the reading calls a character and the bible has no entry for: build them
        # now, then read the chapter again against the larger catalogue.  Re-reading costs no model call
        # (resolve_chapter reuses the saved source actors while the segments are unchanged) and it is the
        # only way the new entries can be bound, because the binding was computed before they existed.
        from novel_manga.application.identity.reading_roster import decided_extras, reading_decisions
        from novel_manga.application.identity.bible_growth import grow_unbound_roster, judge_unknown
        # Rule on anyone the whole-book reading never registered BEFORE growth runs, so a verdict of
        # "character" reaches the same pass that builds them - otherwise the chapter is lost once and
        # only works on the retry.
        #
        # And keep going until it settles.  One pass is not enough because this pass changes the
        # question: in chapter 51 the verdict 钢铁怪物=角色 built an entry, the chapter was read again
        # against the larger catalogue, and only then did 巨大机器人 surface as unbound - after the
        # judging had already happened.  Bounded, because a loop that keeps finding new names is a
        # reading that disagrees with itself, and that should stop the chapter rather than spin.
        for _ in range(3):
            unjudged = decided_extras(reading_decisions(novel_dir),
                                      context.get("unmatched_actors", []), episode.index,
                                      lambda name: pc_cast.name_forms(name, ctx=ctx))[1]
            on_stage = {r["name"] for r in context.get("unmatched_actors", [])
                        if r.get("presence") in {"on_stage", "voice"} and r.get("kind") == "individual"}
            asked = [n for n in unjudged if n in on_stage]
            judged = judge_unknown(novel_dir, asked, chapter_text, episode.index) if asked else {}
            grown = grow_unbound_roster(novel_dir, context, chapter_text, episode.index)
            # Resolution/growth may already have refreshed the on-disk catalogue.
            # Even when this pass adds nothing, bind against that current roster.
            full_bible = StoryBible.model_validate_json(bible_target.read_text(encoding="utf-8"))
            identity_data = load_chapter(episode_dir)
            context = resolve_chapter(episode_dir, data=identity_data)
            if not judged and not grown:
                break
    planner_context.load_entity_index(novel_dir, episode.index, ctx=ctx, identity_data=identity_data)

    # Which characters and locations the planner may name.  The whole bible is
    # never sent: at a few thousand chapters it would be hundreds of people the
    # model has no use for.  A character is offered when they are a lead, when
    # this chapter names them (by name OR by any alias, which the old slice
    # missed - an unoffered character comes back as a duplicate entry), or when
    # they were on screen in the last few chapters, which keeps a scene that
    # refers to someone as "he" from losing them.
    cast = planner_context.cast_history(novel_dir)
    aliases_of = {}
    for alias, target in ctx.aliases.items():
        aliases_of.setdefault(target, []).append(alias)

    def named_here(name: str) -> bool:
        return bool(name) and any(form in chapter_text for form in pc_cast.name_forms(name, ctx=ctx))

    recent_characters = planner_context.recent_names(cast.get("characters", {}), episode.index, pc_constants.CAST_RECENT_CHAPTERS)
    recent_locations = planner_context.recent_names(cast.get("locations", {}), episode.index, pc_constants.CAST_RECENT_CHAPTERS)
    main_cast = [c for c in full_bible.characters if "主角" in c.role]
    ledger_cast_here = planner_context.ledger_cast(novel_dir, episode.index)
    from novel_manga.application.identity.store import current_context
    current_identity = current_context(episode_dir, data=identity_data)
    from . import source_menu
    ctx.anonymous_speakers = source_menu.speakers(ctx.anonymous_speakers, current_identity)
    if current_identity:
        from novel_manga.story.source_identity import active_cast_names
        # Someone the reading left out of the bible on purpose is an extra, not an unbound actor - but
        # "not in the roster" is not by itself that decision.  It is also what a character the reading
        # MISSED looks like, and what everyone in a chapter the reading never reached looks like.  So the
        # decision has to be one the reading actually recorded: a form it saw and declined, or any form
        # in a chapter it read, where not promoting someone to a character is itself the verdict.
        from novel_manga.application.identity.reading_roster import decided_extras, reading_decisions
        reading = reading_decisions(novel_dir)
        # Only the people the reading actually ruled on play as extras.  Anyone it never ruled on was
        # put to it above; if that produced no verdict, they are still unaccounted for and the binding
        # check stops the chapter, which is the honest outcome.
        decided = (decided_extras(reading, current_identity.get('unmatched_actors', []), episode.index,
                                  lambda name: pc_cast.name_forms(name, ctx=ctx))[0]
                   if reading['roster'] else [])
        active_names = active_cast_names(current_identity, decided)
        present = [c for c in full_bible.characters if c.name in active_names]
        main_cast, carried = [], []
    elif ledger_cast_here:
        # the ledger read this chapter: offer exactly the people the book puts in it (on stage or a voice),
        # not everyone whose name-form happens to occur and not whoever was on screen three chapters ago
        present = [c for c in full_bible.characters if ledger_cast_here.get(c.name) in ("on_stage", "voice")]
        carried = []
        print(f"ledger cast for chapter {episode.index}: {[c.name for c in present]}", file=sys.stderr)
    else:
        present = [c for c in full_bible.characters if named_here(c.name)]
        carried = [c for c in full_bible.characters if c.name in recent_characters]
    sliced_characters = list({c.name: c for c in [*main_cast, *present, *carried]}.values())
    from novel_manga.application.identity.context import typed_entities
    entity_types = typed_entities(novel_dir, current_identity, data=identity_data)
    sliced_characters = [c for c in sliced_characters if entity_types.get(c.name, {}).get('kind') != 'object']
    # A location is known from the chapter that added it (bible_growth.json);
    # the base bible's locations count as known from the start.  Never offer a
    # place the story has not reached, and when nothing is named, fall back to
    # the most recently introduced places BEFORE this chapter, not the newest
    # ones in a bible that may be a thousand chapters ahead.
    added_at: dict[str, int] = {}
    props_added_at: dict[str, int] = {}
    growth_path = novel_dir / "bible_growth.json"
    if growth_path.is_file():
        try:
            for ch, entry in json.loads(growth_path.read_text(encoding="utf-8")).items():
                for loc in entry.get("locations", []) or []:
                    added_at.setdefault(str(loc).split("：", 1)[0].strip(), int(ch))
                for prop in entry.get("props", []) or []:
                    props_added_at.setdefault(str(prop).strip(), int(ch))
        except (OSError, ValueError):
            added_at = {}
            props_added_at = {}

    from novel_manga.application.identity.reading_locations import for_chapter
    sliced_locations = planner_context.offered_locations(
        list(full_bible.locations), chapter=episode.index, named_here=named_here,
        recent=set(recent_locations), added_at=added_at,
        window=pc_constants.CAST_RECENT_CHAPTERS,
        read_here={row['name'] for row in for_chapter(novel_dir, episode.index)})
    source_places = source_menu.locations(novel_dir, episode.index, chapter_text)
    offered_names = {entry.split('：', 1)[0].strip() for entry in sliced_locations}
    sliced_locations.extend(row['name'] for row in source_places if row['name'] not in offered_names)
    bible = full_bible.model_copy(update={"characters": sliced_characters, "locations": sliced_locations})
    location_map = {full.split("：", 1)[0].strip(): full for full in bible.locations}
    names = [character.name for character in bible.characters]
    everyone = [character.name for character in full_bible.characters]
    grammar_path = args.grammar or (novel_dir / "visual_grammar.json")
    grammar = json.loads(grammar_path.read_text(encoding="utf-8")) if grammar_path.is_file() else None
    episode_dir.mkdir(parents=True, exist_ok=True)
    bible_target = novel_dir / "story_bible.json"
    if not bible_target.is_file():
        shutil.copy2(args.bible, bible_target)

    ctx.separate_pairs = planner_context.load_separate_pairs(novel_dir)
    if ctx.separate_pairs:
        print(f"keeping apart: {'、'.join(f'{a}+{b}' for a, b in ctx.separate_pairs)}", file=sys.stderr)
    ledger_snapshot = planner_context.ledger_snapshot_for(novel_dir, episode.index, segments, ledger_cast_here, names) if ledger_cast_here else None
    # Rolling recap: the summaries the planner itself wrote for the previous
    # chapters, for continuity only (who is where, what just happened).
    recap_path = novel_dir / "recap.json"
    recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
    previous_recap = [row for row in recap if int(row.get("chapter", 0)) < episode.index][-5:]
    # Recap text carries the story; the closing picture of the previous episode
    # carries the *scene* - where everyone stood and what they were doing when
    # the last clip ended - so this episode can open by picking it up rather than
    # re-establishing everything.
    previous_ending = None
    previous_script = pc_parts.previous_episode_dir(novel_dir, args.novel_id, episode.index, part or None) / "chapter_script.json"
    if previous_script.is_file():
        try:
            last_shot = (json.loads(previous_script.read_text(encoding="utf-8")).get("shots") or [])[-1]
            previous_ending = {"location": last_shot.get("location"), "characters": last_shot.get("characters"), "end_state": last_shot.get("end_state")}
        except (OSError, ValueError, IndexError, AttributeError):
            previous_ending = None
    for row in previous_recap:  # the planner needs the gist, not the whole paragraph
        if len(str(row.get("summary", ""))) > 180:
            row["summary"] = str(row["summary"])[:180] + "…"
    # Chapter recaps only reach five chapters back; the volume summaries carry
    # the arc so a thousand-chapter story does not drift.
    volumes_path = novel_dir / "volumes.json"
    volumes = json.loads(volumes_path.read_text(encoding="utf-8")) if volumes_path.is_file() else []
    previous_volumes = [row for row in volumes if int(row.get("to", 0)) < episode.index][-2:]
    return ChapterCast(full_bible, bible, names, everyone, location_map, grammar, ledger_snapshot, identity_data, props_added_at, source_places, recap_path, previous_recap, previous_ending, previous_volumes, sliced_characters, sliced_locations)
