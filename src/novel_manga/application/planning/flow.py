"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext

class PlanningInputError(ValueError):
    pass


class StoryboardAwaitingChoice(PlanningInputError):
    """A sandbox-planned chapter has candidates and nobody has chosen one yet.

    Not a failure: the backend asks for a storyboard, a person picks the take, and planning resumes
    from the recorded choice.  It is its own type so a batch can tell "waiting for me" apart from
    "this chapter is broken".
    """

from novel_manga.ingest import read_novel
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json
from pathlib import Path
from novel_manga.application.profiles import is_fast
from novel_manga.application.profiles import load_genre
from novel_manga.application.profiles import load_profile
import fcntl
import hashlib
import json
import re
import shutil
import sys
import time
import novel_manga.planning.budget as pc_budget
import novel_manga.planning.cast as pc_cast
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.binding as pc_binding
import novel_manga.planning.contracts as pc_contracts
import novel_manga.planning.parts as pc_parts
import novel_manga.planning.outputs as pc_outputs
import novel_manga.planning.metrics as pc_metrics
import novel_manga.planning.prompts as pc_prompts
import novel_manga.planning.text as pc_text
import novel_manga.planning.validation as pc_validation
import novel_manga.planning.decisions as pc_decisions
import novel_manga.application.planning.context as planner_context
import novel_manga.application.planning.requests as planner_requests
from novel_manga.planning.methods import get_method
from novel_manga.planning.methods.blueprint import blueprint_schema, blueprint_prompt, validate_blueprint


def save_method_artifacts(directory, ctx, attempt):
    if not ctx.method_artifacts:
        return
    target = directory / f'method_attempt_{attempt:02d}'
    target.mkdir(exist_ok=True)
    for name, value in ctx.method_artifacts.items():
        atomic_write_json(target / f'{name}.json', value)

def run(args, ctx: PlannerContext) -> int:
    ctx.episode_seconds_min = args.min_seconds
    novel = read_novel(args.source, novel_id=args.novel_id, title=args.title)
    merge = max(1, args.merge)
    episode_count = -(-len(novel.episodes) // merge)
    if not 1 <= args.episode_index <= episode_count:
        raise SystemExit(f"episode index out of range 1..{episode_count} (merge {merge})")
    if merge == 1:
        episode = novel.episodes[args.episode_index - 1]
    else:
        # Thin web-novel chapters: one episode covers N consecutive chapters.
        group = novel.episodes[(args.episode_index - 1) * merge: args.episode_index * merge]
        joined = "\n\n".join(f"{e.source_title}\n{e.source_text}" for e in group)
        episode = group[0].model_copy(update={
            "index": args.episode_index,
            "source_title": f"{group[0].source_title} 至 {group[-1].source_title.split(' ', 1)[0]}" if len(group) > 1 else group[0].source_title,
            "source_text": joined, "text_count": sum(e.text_count for e in group),
            "source_start": group[0].source_start, "source_end": group[-1].source_end,
        })
    novel_dir = Path(args.output_root).resolve() / args.novel_id
    part = int(getattr(args, "part", 0) or 0)
    if part:
        # One of the chapter's episodes: the same Episode shape --merge builds from several chapters,
        # built here from a stretch of one.  A chapter's dialogue runs some 260 seconds of screen and an
        # episode is 105, so a chapter that says much is two or three episodes, cut where the story
        # turns (parts.json, decided before any of them is planned).  Downstream nothing changes: the
        # part gets its own coverage segments, its own directory, its own plan and video.
        rows = pc_parts.paragraphs(episode.source_text, episode.source_title)
        parts = pc_parts.read_parts(novel_dir / pc_parts.part_dir_name(args.novel_id, episode.index, None))
        chosen = next((p for p in parts if p.part == part), None)
        if chosen is None:
            raise PlanningInputError(f"第 {episode.index} 章没有第 {part} 集的分集记录（parts.json 里有 {len(parts)} 集）；先跑分集")
        body = pc_parts.part_text(rows, chosen)
        offset = episode.source_text.find(rows[chosen.first - 1]) if rows else 0
        episode = episode.model_copy(update={
            "source_title": f"{episode.source_title}{chosen.label}",
            "source_text": body, "text_count": len(body),
            "source_start": episode.source_start + max(0, offset),
            "source_end": episode.source_start + max(0, offset) + len(body),
        })
    full_bible = StoryBible.model_validate_json(Path(args.bible).read_text(encoding="utf-8"))
    # Per-chapter slice: a long novel's bible has hundreds of entries, but the
    # prompt and the JSON enums only need the main cast plus whoever and
    # wherever this chapter mentions.  Asset ids come from positions in the
    # full bible (the packer maps names back), so slicing costs nothing.
    chapter_text = episode.source_text
    episode_dir = novel_dir / pc_parts.part_dir_name(args.novel_id, episode.index, part or None)
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier,
                           story_method=getattr(args, 'story_method', None),
                           planning_backend=getattr(args, 'planning_backend', None),
                           agent_skill=getattr(args, 'agent_skill', None))
    try:
        method = get_method(profile.get('story_method'))
    except ValueError as error:
        raise PlanningInputError(str(error)) from error
    ctx.story_method = method.key if method else ''
    ctx.story_blueprint = {}
    ctx.method_artifacts = {}
    genre = load_genre(profile)
    # The book's own roles win over the genre's eight, and the file is read on every run so that a
    # role added while reviewing cards is in the next plan without rebuilding anything.
    ctx.anonymous_speakers = planner_context.anonymous_roles(
        novel_dir, genre.get("anonymous_roles") or pc_constants.DEFAULT_ANONYMOUS_SPEAKERS)
    ctx.system_prompt = pc_constants.DEFAULT_SYSTEM_PROMPT
    # The brief's worked examples (light sources, avoid lines, text on props,
    # anonymous roles) come from the genre file; the xianxia wording in the
    # brief itself is only the default they replace.
    for key, default in pc_constants.PROMPT_EXAMPLE_DEFAULTS.items():
        ctx.system_prompt = ctx.system_prompt.replace(default, str((genre.get("prompt_examples") or {}).get(key) or default))
    ctx.text_on_props_gate = genre.get("text_on_props", "report") == "gate"
    fast = is_fast(profile)
    ctx.fast_tier = fast
    try:
        planning_budget = pc_budget.configure_budget(episode.text_count, fast=fast, min_seconds=args.min_seconds, ctx=ctx)
    except ValueError as error:
        raise PlanningInputError(str(error)) from error
    if method:
        # A method may tell the chapter through action and silence. The existing
        # duration/source checks remain; a speech quota must not invent dialogue.
        ctx.spoken_range = (0, ctx.spoken_range[1])
        planning_budget['spoken_chars'] = list(ctx.spoken_range)
        planning_budget['episode_max_seconds'] = getattr(args, 'max_seconds', None)
    elif getattr(args, 'max_seconds', None) is not None:
        ctx.episode_seconds_max = args.max_seconds
        planning_budget['episode_max_seconds'] = args.max_seconds
    if getattr(args, 'max_seconds', None) is not None:
        ctx.episode_seconds_target = min(ctx.episode_seconds_target, args.max_seconds)
        planning_budget['episode_target_seconds'] = ctx.episode_seconds_target
    ctx.chat_self, ctx.chat_card_mode = "", True
    chat_screen_path = novel_dir / "chat_screen.json"
    if chat_screen_path.is_file():
        ctx.chat_self = str(json.loads(chat_screen_path.read_text(encoding="utf-8")).get("self_name", "")).strip()
        ctx.chat_card_mode = str(json.loads(chat_screen_path.read_text(encoding="utf-8")).get("render", "card")) == "card"
    episode_dir.mkdir(parents=True, exist_ok=True)
    bible_target = novel_dir / 'story_bible.json'
    if not bible_target.is_file():
        shutil.copy2(args.bible, bible_target)
    segments = pc_text.split_segments(episode.source_text, episode.source_title, pc_constants.SEGMENT_COUNT)
    atomic_write_json(episode_dir / 'segments.json', segments)
    from novel_manga.application.identity.store import load_chapter
    identity_data = load_chapter(episode_dir)
    if not args.dry_run and not args.replay:
        from novel_manga.application.identity.flow import resolve_chapter
        context = resolve_chapter(episode_dir, data=identity_data)
        # Anyone on stage that the reading calls a character and the bible has no entry for: build them
        # now, then read the chapter again against the larger catalogue.  Re-reading costs no model call
        # (resolve_chapter reuses the saved source actors while the segments are unchanged) and it is the
        # only way the new entries can be bound, because the binding was computed before they existed.
        from novel_manga.application.review.bible import (decided_extras, grow_unbound_roster,
                                                           judge_unknown, reading_decisions)
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
            if not judged and not grown:
                break
            full_bible = StoryBible.model_validate_json(bible_target.read_text(encoding="utf-8"))
            identity_data = load_chapter(episode_dir)
            context = resolve_chapter(episode_dir, data=identity_data)
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
    if current_identity:
        from novel_manga.story.source_identity import active_cast_names
        # Someone the reading left out of the bible on purpose is an extra, not an unbound actor - but
        # "not in the roster" is not by itself that decision.  It is also what a character the reading
        # MISSED looks like, and what everyone in a chapter the reading never reached looks like.  So the
        # decision has to be one the reading actually recorded: a form it saw and declined, or any form
        # in a chapter it read, where not promoting someone to a character is itself the verdict.
        from novel_manga.application.review.bible import decided_extras, reading_decisions
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
    growth_path = novel_dir / "bible_growth.json"
    if growth_path.is_file():
        try:
            for ch, entry in json.loads(growth_path.read_text(encoding="utf-8")).items():
                for loc in entry.get("locations", []) or []:
                    added_at.setdefault(str(loc).split("：", 1)[0].strip(), int(ch))
        except (OSError, ValueError):
            added_at = {}

    sliced_locations = planner_context.offered_locations(
        list(full_bible.locations), chapter=episode.index, named_here=named_here,
        recent=set(recent_locations), added_at=added_at,
        window=pc_constants.CAST_RECENT_CHAPTERS)
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
    authored = None
    # The sandbox backend resolves to the same --bind-storyboard path: the agent writes the sheet, a
    # person accepts one, and from there this is an authored chapter like any other.  Nothing below
    # knows or needs to know that a container produced it.
    if str(profile.get("planning_backend", "local")) == "sandbox_agent" and not getattr(args, "bind_storyboard", None):
        from novel_manga.application.agents import storyboard as agent_storyboard
        chosen = agent_storyboard.accepted_sheet(episode_dir)
        if chosen is None:
            current = agent_storyboard.state(episode_dir)
            where = f"（{current.run} / {current.attempt}）" if current.run else ""
            raise StoryboardAwaitingChoice(
                f"第 {episode.index} 章用沙箱后端规划，当前状态：{current.status}{where}。"
                + ("候选：" + "、".join(current.sheets) + "；用 scripts/agent_storyboard_thin.py --accept 选一版"
                   if current.sheets else
                   "还没有候选分镜；先跑 scripts/agent_storyboard_thin.py --propose"))
        args.bind_storyboard, args.bind_sheet = str(chosen[0]), chosen[1] or getattr(args, "bind_sheet", None)
    if getattr(args, "bind_storyboard", None):
        from novel_manga.planning.storyboard import authored_payload, read_workbook
        try:
            sheets = read_workbook(Path(args.bind_storyboard))
        except (ValueError, OSError) as error:
            raise PlanningInputError(str(error)) from error
        sheet = next((s for s in sheets if s.name == args.bind_sheet), None) if args.bind_sheet else (sheets[0] if len(sheets) == 1 else None)
        if sheet is None:
            raise PlanningInputError("请用 --bind-sheet 选择分镜工作表：" + "、".join(s.name for s in sheets))
        authored = authored_payload(sheet)
    if authored:
        # A bound sheet has already chosen its people and places; the slice exists to keep the menu
        # short for a model that is still choosing.  Withholding an authored sheet's own location
        # leaves the scene nowhere to go - 哥谭大学教室 enters the bible at chapter 44 and the sheet for
        # chapter 10 needs it, and the classroom would otherwise have to become the coffee shop.
        wanted_places, places = set(pc_binding.written_names(authored)[1]), dict(location_map)
        for full in full_bible.locations:
            short = full.split("：", 1)[0].strip()
            if short in wanted_places:
                places.setdefault(short, full)
        wanted_people = set(pc_binding.written_names(authored)[0])
        people = {c.name: c for c in sliced_characters}
        for character in full_bible.characters:
            if character.name in wanted_people:
                people.setdefault(character.name, character)
        sliced_characters, sliced_locations = list(people.values()), list(places.values())
        bible = full_bible.model_copy(update={"characters": sliced_characters, "locations": sliced_locations})
        location_map = {full.split("：", 1)[0].strip(): full for full in bible.locations}
        names = [character.name for character in bible.characters]
    segment_ids = [segment["segment_id"] for segment in segments]
    # A bound sheet is not re-planned: the model answers one object per authored shot with only the
    # fields the import left empty, and never sees a schema that would let it rewrite the cuts.
    ctx.authored_storyboard = bool(authored)
    schema = (pc_binding.bind_schema(authored, names, list(location_map), segment_ids, ctx=ctx) if authored
              else pc_contracts.build_schema(names, list(location_map), segment_ids, ctx=ctx))
    from novel_manga.application.identity.context import prompt_context
    identity_context = prompt_context(episode_dir, names, data=identity_data)
    payload = {
        "policy": ctx.policy,
        **({'story_method': {'id': method.key, 'version': method.version, 'name': method.name}} if method else {}),
        "chapter_index": episode.index,
        "chapter_title": episode.source_title,
        "chapter_chars": episode.text_count,
        "story_bible": pc_prompts.compact_bible(bible, location_map),
        **({"visual_grammar": grammar} if grammar else {}),
        "production_profile": profile,
        "planning_budget": planning_budget,
        "available_characters": names,
        "identity_context": identity_context,
        **({"name_aliases": {alias: target for alias, target in ctx.aliases.items() if target in names}, "alias_rule": "name_aliases 里的名字是同一人物的别称、昵称或网名；characters 和 speaker_name 一律写正名"} if any(target in names for target in ctx.aliases.values()) else {}),
        "available_locations": list(location_map),
        "anonymous_offscreen_speakers": ctx.anonymous_speakers,
        "era_setting": {"genre": genre["name"], "allowed": genre.get("era_allowed", ""), "not_allowed": genre.get("era_rejects", ""), "crowd": genre.get("crowd_default", "")},
        **({"previous_volumes_recap": previous_volumes} if previous_volumes else {}),
        **({"previous_chapters_recap": previous_recap} if previous_recap else {}),
        **({"previous_episode_ending": previous_ending, "previous_episode_ending_usage": "这是上一集最后一个画面的状态（地点、在场的人、结束时的动作）。本集开场如果是同一场景可以直接接上，不必重新交代；换了场景就忽略。"} if previous_ending else {}),
        "segments": [{"segment_id": s["segment_id"], "text": s["text"]} for s in segments],
        **({"authored_storyboard": authored, "authored_storyboard_usage": pc_constants.AUTHORED_STORYBOARD_USAGE} if authored else {}),
        **({"ledger_snapshot": ledger_snapshot} if ledger_snapshot else {}),
        "quoted_lines_that_must_be_kept": pc_text.chapter_quotes(episode.source_text),
        "requirements": {
            **pc_budget.budget_requirements(ctx=ctx),
            "turn_text_max_chars": pc_constants.TURN_MAX_CHARS,
            "source_quote_chars": f"{pc_constants.QUOTE_MIN_CHARS}-{pc_constants.QUOTE_MAX_CHARS}",
            "max_skipped_segments": ctx.max_skipped,
            "one_visible_speaker_per_shot": True,
            "no_narration_no_inner_voice": True,
            "recap_usage": "previous_volumes_recap 是前面几十章的主线走向、previous_chapters_recap 是最近几章的细节，两者都只用于保持连续性（人物关系、所在位置、状态），本集只拍当前章的事件，不得把前情内容拍进来",
        },
        **({"director_notes": args.notes} if args.notes else {}),
    }
    print(json.dumps({"segments": [{k: v for k, v in s.items() if k != "text"} for s in segments], "characters": names, "locations": list(location_map), "bible_size": [len(full_bible.characters), len(full_bible.locations)], "recap_chapters": [r.get("chapter") for r in previous_recap], "visual_grammar": (grammar or {}).get("name"), "profile": profile}, ensure_ascii=False), flush=True)
    if args.dry_run:
        atomic_write_json(episode_dir / "request_dry_run.json", payload)
        if method:
            from novel_manga.planning.methods.scenes import screenplay_schema, screenplay_prompt, screenplay_input
            atomic_write_json(episode_dir / 'method_request_dry_run.json', {
                'method': method.describe(), 'system': screenplay_prompt(method),
                'schema': screenplay_schema(payload), 'payload': screenplay_input(payload, args.notes),
            })
        return 0

    started = time.monotonic()
    attempts: list[dict] = []
    repair: dict | None = None
    final_errors: list[str] = []
    result = None
    patch_rounds = 0  # small repair calls used so far on this chapter
    patch_seconds_left = pc_constants.PATCH_TOTAL_SECONDS
    for attempt in range(1, args.max_redo + 2):
        request_payload = {**payload, **({"repair": repair} if repair else {})}
        atomic_write_json(episode_dir / f"request_attempt_{attempt:02d}.json", request_payload)
        raw_path = episode_dir / f"response_attempt_{attempt:02d}.raw.json"
        if args.replay and attempt == 1:
            content = Path(args.replay).read_text(encoding="utf-8")
            meta = {"replayed_from": str(args.replay)}
            if method:
                replay = Path(args.replay)
                number = re.search(r'response_attempt_(\d+)', replay.name)
                outline_path = replay.parent / f'analysis_attempt_{number.group(1) if number else "01"}.txt'
                if not outline_path.is_file():
                    raise PlanningInputError('method replay needs its adjacent analysis_attempt_NN.txt outline')
                outline = outline_path.read_text(encoding='utf-8')
                problems = validate_blueprint(outline, method, payload['segments'])
                if problems:
                    raise PlanningInputError('invalid method replay outline: ' + '; '.join(problems))
                ctx.story_blueprint = json.loads(outline)
        else:
            try:
                content, meta = planner_requests.call_model(base_url=args.base_url, model=args.model, payload=request_payload, schema=schema,
                                           max_tokens=args.max_tokens, timeout=args.timeout, analysis_tokens=args.outline_tokens or (8192 if method else 4096),
                                           notes=args.notes, grammar=grammar, profile=profile, fast=fast, outline_mode=args.outline_mode, seed=args.seed, ctx=ctx)
            except planner_requests.IncompleteOutlineError as error:
                save_method_artifacts(episode_dir, ctx, attempt)
                final_errors = [str(error)]
                attempts.append({"attempt": attempt, "stage": "outline", "outline_complete": False,
                                 "outline_attempts": error.attempts, "errors": final_errors})
                print(json.dumps({"status": "incomplete_outline", "attempt": attempt, "errors": final_errors}, ensure_ascii=False), flush=True)
                break  # no second pass or outer full-draft retry of an unfinished outline
        save_method_artifacts(episode_dir, ctx, attempt)
        raw_path.write_text(content, encoding="utf-8")
        if meta.get("analysis"):
            (episode_dir / f"analysis_attempt_{attempt:02d}.txt").write_text(meta.pop("analysis"), encoding="utf-8")
        try:
            if re.search(r"\s{2000,}", content):
                raise ValueError("constrained decoding derailed into whitespace (finish_reason=%s)" % meta.get("finish_reason"))
            raw = planner_requests.extract_json(content)
            if authored:
                # What the sheet leaves out is its author's choice, and is recorded as one - by the
                # coverage check, not here: only after validation has moved each citation to the
                # segment its quote is really in does "nobody cites this" mean anything.
                raw = pc_binding.merge(authored, raw, character_names=names)
        except (json.JSONDecodeError, ValueError) as error:
            final_errors = [f"response is not one JSON object: {type(error).__name__}: {error}"]
            attempts.append({"attempt": attempt, **meta, "errors": final_errors})
            repair = pc_decisions.invalid_response_feedback(final_errors, meta.get("finish_reason"))
            continue
        validation = pc_validation.validate_and_normalize(raw, segments, bible, location_map, episode.source_text, everyone, ctx=ctx)
        errors, warnings, shots = validation.errors, validation.warnings, validation.shots
        fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        resent = attempts and attempts[-1].get("fingerprint") == fingerprint
        attempts.append({"attempt": attempt, **meta, "errors": errors, "warnings": warnings, "fingerprint": fingerprint, "resent_previous": bool(resent)})
        print(json.dumps({"attempt": attempt, **meta, "error_count": len(errors), "warning_count": len(warnings)}, ensure_ascii=False), flush=True)
        decision = pc_decisions.decide_validation(validation, final_attempt=attempt == args.max_redo + 1,
                    patch_rounds=patch_rounds, patch_seconds_left=patch_seconds_left, allow_floor_waiver=True)
        if decision.floor_waived:
            errors, warnings = decision.errors, decision.warnings
            attempts[-1]["errors"] = errors
            attempts[-1]["floor_waived"] = True
        # A scene-led draft is repaired before projection. Rewriting one legacy
        # stage here would silently diverge from its accepted scene/turn owner.
        patchable = None if ctx.story_blueprint.get('version') == 'scene-screenplay-v2' else decision.targets
        while patchable:
            # Nearly every redo trigger in the trial was local - a forgotten
            # segment, a blood word in one start_state, a paraphrased quote, a
            # missing speaker.  Fix those stages with one small call and re-check
            # instead of a 150-650 s re-plan that tends to break something else.
            patch_rounds += 1
            missing_ids, faulty = patchable
            patch_timeout = min(pc_constants.PATCH_TIMEOUT_SECONDS, patch_seconds_left)
            summary = {"round": patch_rounds, "segments": missing_ids, "stages": list(faulty), "timeout_seconds": round(patch_timeout, 1)}
            print(json.dumps({"attempt": attempt, "patch": summary, "status": "patching"}, ensure_ascii=False), flush=True)
            patch_started = time.monotonic()
            try:
                # An authored sheet has nothing to insert - what it leaves uncited is recorded as its
                # author's choice by the coverage check - and what a patch rewrites on it keeps the
                # author's columns: the model's answer may correct a quote or a state, never a shot.
                patched = planner_requests.patch_plan(raw, [] if authored else missing_ids, faulty, segments, names, list(location_map), timeout=patch_timeout, ctx=ctx)
                if authored:
                    patched = pc_binding.keep_authored(raw, patched)
            except Exception as error:  # noqa: BLE001 - fall through to the normal redo
                failure = {**summary, "failed": f"{type(error).__name__}: {str(error)[:120]}", "elapsed_seconds": round(time.monotonic() - patch_started, 1)}
                attempts[-1].setdefault("patches", []).append(failure)
                print(json.dumps({"attempt": attempt, "patch": failure}, ensure_ascii=False), flush=True)
                break
            finally:
                patch_seconds_left = max(0.0, patch_seconds_left - (time.monotonic() - patch_started))
            validation = pc_validation.validate_and_normalize(patched, segments, bible, location_map, episode.source_text, everyone, ctx=ctx)
            errors, warnings, shots = validation.errors, validation.warnings, validation.shots
            attempts[-1].setdefault("patches", []).append({**summary, "errors_after": len(errors), "errors": errors[:6]})
            print(json.dumps({"attempt": attempt, "patch": summary, "error_count": len(errors)}, ensure_ascii=False), flush=True)
            raw = patched  # the next round, or the full redo, starts from the improved draft
            decision = pc_decisions.decide_validation(validation, final_attempt=attempt == args.max_redo + 1,
                        patch_rounds=patch_rounds, patch_seconds_left=patch_seconds_left)
            patchable = decision.targets
        if not errors and ctx.strict_plan:
            strict = pc_validation.strict_plan_issues(shots, episode.source_text, segments, raw, ctx=ctx)
            strict_decision = pc_decisions.decide_strict(strict, final_attempt=attempt == args.max_redo + 1)
            if strict_decision.strict_waived:
                warnings = [*warnings, *strict_decision.warnings]
                attempts[-1]["strict_waived"] = strict_decision.strict_waived
            elif strict_decision.issues:
                errors = strict_decision.errors
                attempts[-1]["errors"] = errors
                print(json.dumps({"attempt": attempt, "strict_errors": errors}, ensure_ascii=False), flush=True)
        if errors:
            final_errors = errors
            repair = pc_decisions.revision_feedback(errors, raw, resent=bool(resent))
            continue
        result = (raw, shots, warnings)
        break

    if result is None:
        failure = {
            "status": "planning_failed",
            "policy": ctx.policy,
            "episode_index": episode.index,
            "attempts": attempts,
            "errors": final_errors,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
        atomic_write_json(episode_dir / "planning_failed.json", failure)
        print(json.dumps({"status": "planning_failed", "errors": final_errors}, ensure_ascii=False, indent=2))
        return 2

    raw, shots, warnings = result
    # A new script invalidates everything downstream: the packed plan must be
    # rebuilt and a finished-episode report from the old plan would make batch
    # drivers skip the episode as done.
    for stale in ("clip_plan.json", "clip_plan.md", "thin_media_report.json", "media_qc_report.json"):
        (episode_dir / stale).unlink(missing_ok=True)
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    report_metrics = pc_metrics.metrics(shots, episode.source_text, segments, skipped)
    plan = pc_outputs.to_episode_plan(raw, shots, location_map, episode.source_text, episode.source_title, ctx=ctx)
    report = {
        "status": "passed",
        "policy": ctx.policy,
        "model": args.model,
        "planning_budget": planning_budget,
        "outline_mode": f"method:{method.key}" if method else args.outline_mode,
        **({'story_method': method.describe()} if method else {}),
        "seed": args.seed,
        "episode_index": episode.index,
        "source_text_sha256": pc_text.sha256_text(episode.source_text),
        "style_fingerprint": bible.style_fingerprint,
        "hard_gates": {"source_coverage": "passed", "cast_and_speakers": "passed"},
        **({'story_method_checks': {'source_ownership': 'passed', 'beat_coverage': 'passed',
                                    'semantic_quality_review': 'passed' if ctx.story_blueprint.get('review', {}).get('completed') else 'not_run'}} if method else {}),
        "metrics": report_metrics,
        "warnings": [*warnings, *pc_validation.soft_warnings(report_metrics, ctx=ctx)],
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    atomic_write_json(episode_dir / "chapter_script.json", {"video_title": raw.get("video_title"), "source_title": episode.source_title, "episode_index": episode.index, "profile": profile, "hook": raw.get("hook"), "summary": raw.get("summary"), "clip_count": len(raw.get("clips") or []), "shots": shots, "skipped_segments": skipped,
        **({'story_method': method.describe(), 'story_blueprint': ctx.story_blueprint} if method else {})})
    atomic_write_json(episode_dir / "chapter_script_report.json", report)
    with open(recap_path.with_suffix(".lock"), "w") as lock:  # planners may run in parallel
        fcntl.flock(lock, fcntl.LOCK_EX)
        recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
        recap = [row for row in recap if int(row.get("chapter", 0)) != episode.index]
        recap.append({"chapter": episode.index, "title": episode.source_title, "summary": raw.get("summary"), "hook": raw.get("hook")})
        atomic_write_json(recap_path, sorted(recap, key=lambda row: int(row.get("chapter", 0))))
    planner_context.record_cast(novel_dir, episode.index,
                sorted({name for shot in shots for name in shot.get("characters", []) or []}),
                sorted({shot["location"] for shot in shots if shot.get("location")}))
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    (episode_dir / "chapter_script.md").write_text(pc_outputs.render_markdown(raw, shots, report, episode.source_title,
        blueprint=ctx.story_blueprint if method else None), encoding="utf-8")
    if ctx.story_blueprint.get('version') == 'scene-screenplay-v2':
        from novel_manga.planning.methods.scenes import render_screenplay
        (episode_dir / 'scene_script.md').write_text(render_screenplay(ctx.story_blueprint), encoding='utf-8')
    (episode_dir / 'planning_failed.json').unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "episode_dir": str(episode_dir), "metrics": report_metrics, "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0
