"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext




from novel_manga.ingest import read_novel, chapter_table
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json
from pathlib import Path
from novel_manga.application.profiles import effective_profile
from novel_manga.application.profiles import load_genre
from novel_manga.application.profiles import load_profile
import json
import math
import shutil
import novel_manga.planning.budget as pc_budget
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.parts as pc_parts
import novel_manga.planning.text as pc_text
import novel_manga.application.planning.context as planner_context
from novel_manga.planning.methods import get_method


from .errors import PlanningInputError
from .run_types import ChapterSource


def prepare_source(args, ctx: PlannerContext) -> ChapterSource:
    ctx.episode_seconds_min = args.min_seconds
    novel = read_novel(args.source, novel_id=args.novel_id, title=args.title,
                       chapters=chapter_table(Path(args.output_root).resolve() / args.novel_id))
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
        # The budget aims at 90 s and, on the quality tier, accepts anything above nothing; given a
        # third of a chapter the model satisfies "every segment cited" in three clips and stops at 57 s.
        # A part was cut to be about an episode of dialogue, so the floor follows what it holds.
        if not args.min_seconds:
            args.min_seconds = min(pc_parts.PART_FLOOR_SECONDS, round(chosen.est_seconds * pc_parts.PART_FLOOR_SHARE))
            ctx.episode_seconds_min = args.min_seconds
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
    existing_draft = getattr(args, 'compress_existing', None)
    if existing_draft and method:
        raise PlanningInputError('--compress-existing 不改写创作方法持有的分场稿')
    ctx.story_blueprint = {}
    ctx.method_artifacts = {}
    # Scene-exit pairs from the source: the validator makes the question heard whatever the
    # outline did with it, and the payload tells the model they are coming.
    ctx.handoff_pairs = pc_text.handoff_pairs(episode.source_text)
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
    ctx.episode_shape = effective_profile(profile)["episode_shape"]
    try:
        planning_budget = pc_budget.configure_budget(episode.text_count, shape=ctx.episode_shape, min_seconds=args.min_seconds, ctx=ctx)
    except ValueError as error:
        raise PlanningInputError(str(error)) from error
    usual_ceiling = ctx.episode_seconds_max
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
        if not method and args.max_seconds > usual_ceiling:
            # A longer explicit ceiling must also give the writer enough room
            # to tell the story. Keeping the 90s target and 300 spoken chars
            # made ch12 discard its set-up while trying to fit 150s.
            ctx.episode_seconds_target = max(ctx.episode_seconds_target,
                                             min(args.max_seconds - 15, 10 * ctx.max_clip_seconds))
            ctx.spoken_range = (ctx.spoken_range[0], max(ctx.spoken_range[1],
                round(ctx.episode_seconds_target * pc_parts.SPEECH_CHARS_PER_SECOND)))
            planning_budget['spoken_chars'] = list(ctx.spoken_range)
        ctx.episode_seconds_target = min(ctx.episode_seconds_target, args.max_seconds)
        planning_budget['episode_target_seconds'] = ctx.episode_seconds_target
        # Ten authored containers may compile into more video requests when
        # the compiler splits long shots at the real H3 clip cap.
        needed = min(10, math.ceil(args.max_seconds / ctx.max_clip_seconds))
        ctx.clip_range = (ctx.clip_range[0], max(ctx.clip_range[1], needed))
        planning_budget['clip_count'] = list(ctx.clip_range)
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
    return ChapterSource(episode, novel_dir, episode_dir, part, full_bible, chapter_text, profile, method, existing_draft, genre, planning_budget, segments)
