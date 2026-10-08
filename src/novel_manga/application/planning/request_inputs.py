"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext




from novel_manga.util import atomic_write_json
from pathlib import Path
import json
import novel_manga.planning.budget as pc_budget
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.binding as pc_binding
import novel_manga.planning.contracts as pc_contracts
import novel_manga.planning.prompts as pc_prompts
import novel_manga.planning.text as pc_text


from .errors import PlanningInputError, StoryboardAwaitingChoice
from .run_types import ChapterSource, ChapterCast, PlanningInputs


def prepare_request(args, ctx: PlannerContext, source: ChapterSource, cast: ChapterCast) -> PlanningInputs | None:
    episode = source.episode
    episode_dir = source.episode_dir
    full_bible = source.full_bible
    profile = source.profile
    method = source.method
    genre = source.genre
    planning_budget = source.planning_budget
    segments = source.segments
    full_bible = cast.full_bible
    bible = cast.bible
    names = cast.names
    location_map = cast.location_map
    grammar = cast.grammar
    ledger_snapshot = cast.ledger_snapshot
    identity_data = cast.identity_data
    props_added_at = cast.props_added_at
    source_places = cast.source_places
    previous_recap = cast.previous_recap
    previous_ending = cast.previous_ending
    previous_volumes = cast.previous_volumes
    sliced_characters = cast.sliced_characters
    sliced_locations = cast.sliced_locations
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
        if profile.get('inner_voice_delivery') == 'postmix' and not args.dry_run:
            from novel_manga.application.planning.voice_splits import split_authored
            authored, splits = split_authored(authored, episode.source_text, max_seconds=ctx.max_clip_seconds)
            if splits:
                atomic_write_json(episode_dir / 'authored_voice_splits.json', splits)
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
    # Props join the slice the way people and places do: only the ones this chapter's text names.
    # A book without props keeps prop_names empty and the schema byte-identical to before.
    # Pre-reading can register later objects whose generic names also occur early on
    # (a different character's cloak, for example). Keep those out of new adaptation;
    # an authored storyboard retains its explicit choices and full-bible asset ids.
    prop_all = [p for p in full_bible.props or []
                if authored or props_added_at.get(p.name, 0) <= episode.index]
    prop_names = [p.name for p in pc_prompts.props_for_chapter(prop_all, episode.source_text)]
    ctx.wearable_prop_names = (None if authored else
                              [p.name for p in prop_all if p.name in prop_names and p.wearable])
    if prop_all:
        bible = bible.model_copy(update={"props": [p for p in prop_all if p.name in prop_names]})
    # A bound sheet is not re-planned: the model answers one object per authored shot with only the
    # fields the import left empty, and never sees a schema that would let it rewrite the cuts.
    ctx.authored_storyboard = bool(authored)
    ctx.postmix = profile.get('inner_voice_delivery') == 'postmix'
    schema = (pc_binding.bind_schema(authored, names, list(location_map), segment_ids, ctx=ctx, prop_names=prop_names) if authored
              else pc_contracts.build_schema(names, list(location_map), segment_ids, ctx=ctx, prop_names=prop_names))
    from novel_manga.application.identity.context import prompt_context
    identity_context = prompt_context(episode_dir, names, data=identity_data)
    payload = {
        "policy": ctx.policy,
        **({'story_method': {'id': method.key, 'version': method.version, 'name': method.name}} if method else {}),
        "chapter_index": episode.index,
        "chapter_title": episode.source_title,
        "chapter_chars": episode.text_count,
        **({"source_location_evidence": source_places} if source_places else {}),
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
        # 换场问答对：问题是下一场的铺垫，无论提纲怎么压缩都必须可听（validator 会查）
        **({"handoff_pairs_that_must_be_heard": [{"question": q, "answer": a} for q, a in ctx.handoff_pairs]} if ctx.handoff_pairs else {}),
        **({"authored_storyboard": authored, "authored_storyboard_usage": pc_constants.AUTHORED_STORYBOARD_USAGE} if authored else {}),
        **({"ledger_snapshot": ledger_snapshot} if ledger_snapshot else {}),
        "quoted_lines_that_must_be_kept": pc_text.chapter_quotes(episode.source_text),
        "requirements": {
            **pc_budget.budget_requirements(ctx=ctx),
            "turn_text_max_chars": pc_constants.TURN_MAX_CHARS,
            "source_quote_chars": f"{pc_constants.QUOTE_MIN_CHARS}-{pc_constants.QUOTE_MAX_CHARS}",
            "max_skipped_segments": ctx.max_skipped,
            "one_visible_speaker_per_shot": True,
            # 全知旁白禁止；内心独白放开（由编剧按叙事需要选择，规则见系统提示词第3条）。
            # 两者不再共用一个开关：一个管"谁能说话"，一个管"心之所想能否被听见"。
            "no_omniscient_narration": True,
            "inner_monologue": "allowed",
            # An unexplained optional field is ignored: the enum alone did not get a single prop
            # marked in the pilot.  The instruction is what makes the seat get used.
            **({"props_usage": "story_bible.props 是本书已建档的剧情道具。某 stage 画面里道具本体实际出现时（被拿着、被展示、被使用、被特写都算），在该 stage 的 props 字段标出它的名单名字——画面描述不管用什么称呼（玉石、那东西），只要指的就是它就标；最多 2 件，没出现就留空。标注的道具渲染时携带参考图以保持跨集一致"} if prop_names else {}),
            **({"wears_usage": "wears仅表示实际穿在身上的登记装备或服装，可用名单："
                + json.dumps(ctx.wearable_prop_names, ensure_ascii=False)
                + "。名单为空或本镜没有登记穿戴物时用空对象{}；角色基础服装沿用档案与参考图。"
                  "手持、腋下夹着、搬运的包裹或武器属于props，不属于wears。"
                  "null仅表示原文明确脱下此前的穿戴物，不能用来表示没有额外装备。"}
               if prop_names and not authored else {}),
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
        return None
    return PlanningInputs(source, cast, bible, names, location_map, authored, prop_all, prop_names, schema, payload)
