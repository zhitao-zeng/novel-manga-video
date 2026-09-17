"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext

class PlanningInputError(ValueError):
    pass

from novel_manga.ingest import read_novel
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json
from pathlib import Path
from thin_profile import is_fast
from thin_profile import load_genre
from thin_profile import load_profile
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
import novel_manga.planning.contracts as pc_contracts
import novel_manga.planning.outputs as pc_outputs
import novel_manga.planning.metrics as pc_metrics
import novel_manga.planning.prompts as pc_prompts
import novel_manga.planning.text as pc_text
import novel_manga.planning.validation as pc_validation
import novel_manga.planning.decisions as pc_decisions
import planner_context_thin as planner_context
import planner_requests_thin as planner_requests

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
    full_bible = StoryBible.model_validate_json(Path(args.bible).read_text(encoding="utf-8"))
    # Per-chapter slice: a long novel's bible has hundreds of entries, but the
    # prompt and the JSON enums only need the main cast plus whoever and
    # wherever this chapter mentions.  Asset ids come from positions in the
    # full bible (the packer maps names back), so slicing costs nothing.
    chapter_text = episode.source_text
    novel_dir = Path(args.output_root).resolve() / args.novel_id
    episode_dir = novel_dir / f"{args.novel_id}_{episode.index}"
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    genre = load_genre(profile)
    ctx.anonymous_speakers = list(genre.get("anonymous_roles") or pc_constants.DEFAULT_ANONYMOUS_SPEAKERS)
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
    from identity_store_thin import load_chapter
    identity_data = load_chapter(episode_dir)
    if not args.dry_run and not args.replay:
        from identity_flow_thin import resolve_chapter
        resolve_chapter(episode_dir, data=identity_data)
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
    from identity_store_thin import current_context
    current_identity = current_context(episode_dir, data=identity_data)
    if current_identity:
        from novel_manga.story.source_identity import active_cast_names
        active_names = active_cast_names(current_identity)
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
    from identity_context_thin import typed_entities
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

    def short_of(full: str) -> str:
        return full.split("：", 1)[0].strip()

    known = [full for full in full_bible.locations if added_at.get(short_of(full), 0) <= episode.index]
    sliced_locations = [full for full in known
                        if named_here(short_of(full)) or short_of(full) in recent_locations
                        or episode.index - pc_constants.CAST_RECENT_CHAPTERS <= added_at.get(short_of(full), -1) <= episode.index]
    if not sliced_locations:
        sliced_locations = sorted(known, key=lambda full: -added_at.get(short_of(full), 0))[:6] or full_bible.locations[:6]
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
    previous_script = novel_dir / f"{args.novel_id}_{episode.index - 1}" / "chapter_script.json"
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
    schema = pc_contracts.build_schema(names, list(location_map), [segment["segment_id"] for segment in segments], ctx=ctx)
    from identity_context_thin import prompt_context
    identity_context = prompt_context(episode_dir, names, data=identity_data)
    payload = {
        "policy": ctx.policy,
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
        else:
            try:
                content, meta = planner_requests.call_model(base_url=args.base_url, model=args.model, payload=request_payload, schema=schema,
                                           max_tokens=args.max_tokens, timeout=args.timeout, analysis_tokens=args.outline_tokens,
                                           notes=args.notes, grammar=grammar, profile=profile, fast=fast, outline_mode=args.outline_mode, seed=args.seed, ctx=ctx)
            except planner_requests.IncompleteOutlineError as error:
                final_errors = [str(error)]
                attempts.append({"attempt": attempt, "stage": "outline", "outline_complete": False,
                                 "outline_attempts": error.attempts, "errors": final_errors})
                print(json.dumps({"status": "incomplete_outline", "attempt": attempt, "errors": final_errors}, ensure_ascii=False), flush=True)
                break  # no second pass or outer full-draft retry of an unfinished outline
        raw_path.write_text(content, encoding="utf-8")
        if meta.get("analysis"):
            (episode_dir / f"analysis_attempt_{attempt:02d}.txt").write_text(meta.pop("analysis"), encoding="utf-8")
        try:
            if re.search(r"\s{2000,}", content):
                raise ValueError("constrained decoding derailed into whitespace (finish_reason=%s)" % meta.get("finish_reason"))
            raw = planner_requests.extract_json(content)
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
        patchable = decision.targets
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
                patched = planner_requests.patch_plan(raw, missing_ids, faulty, segments, names, list(location_map), timeout=patch_timeout, ctx=ctx)
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
        "outline_mode": args.outline_mode,
        "seed": args.seed,
        "episode_index": episode.index,
        "source_text_sha256": pc_text.sha256_text(episode.source_text),
        "style_fingerprint": bible.style_fingerprint,
        "hard_gates": {"source_coverage": "passed", "cast_and_speakers": "passed"},
        "metrics": report_metrics,
        "warnings": [*warnings, *pc_validation.soft_warnings(report_metrics, ctx=ctx)],
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    atomic_write_json(episode_dir / "chapter_script.json", {"video_title": raw.get("video_title"), "source_title": episode.source_title, "episode_index": episode.index, "profile": profile, "hook": raw.get("hook"), "summary": raw.get("summary"), "clip_count": len(raw.get("clips") or []), "shots": shots, "skipped_segments": skipped})
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
    (episode_dir / "chapter_script.md").write_text(pc_outputs.render_markdown(raw, shots, report, episode.source_title), encoding="utf-8")
    print(json.dumps({"status": "passed", "episode_dir": str(episode_dir), "metrics": report_metrics, "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0
