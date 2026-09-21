"""Episode packing configuration and input reads, separate from deterministic compilation."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import copy
import json
import os
from novel_manga.models.bible import StoryBible
from novel_manga.planning.context import PlannerContext
from novel_manga.story.compilation import CompilerOptions
from novel_manga.application.planning.context import load_entity_index
from novel_manga.application.identity.store import load_chapter
from novel_manga.application.identity.phases import chapter_of
from novel_manga.application.profiles import frame_spec, is_fast, load_genre, load_profile, load_style

POLICY = "thin-clip-plan-v12-six-stages" + ("-15s" if os.environ.get("NOVEL_CLIP_SECONDS_MAX", "").strip() in {"15", "15.0"} else "")


TWO_VIEW_CAST_LIMIT = 2
TWO_VIEWS = "off"


GENRE_REJECTS: list[str] = []  # from the genre preset; appended to 【不要】


GENRE_CROWD = ""


MAX_CLIP_SECONDS = 30.0


SOFT_CUT_SECONDS = 18.0


MAX_STAGES = 6


DEFAULT_ANON_VOICE = {
    "无名测验员": "画外的中年测验员（男声）",
    "无名族人": "画外一名族人",
    "无名少年": "画外一名少年",
    "无名少女": "画外一名少女",
    "无名群声": "画外的人群",
}


ANON_VOICE = dict(DEFAULT_ANON_VOICE)


PACKER_VERSION = "thin-packer-2026-09-12+split-keeps-cast"


PACK_MODE = "execution"


MIN_STANDALONE_SECONDS = 8.0


CHAT_SCREEN: dict = {
    "app": "微信群聊", "group_name": "", "self_name": "",
    # "card": chat_card.py draws the screen and the runner cuts it in; "video": the old way, Seedance writes the text.
    "render": "card",
    "layout": "顶部居中显示群名；消息按时间从上到下排列；每条消息左侧一个圆形卡通头像，昵称以一行小字显示在气泡上方，气泡内只有消息正文；"
              "他人的消息是白色气泡靠左，本人的消息是绿色气泡靠右且不显示昵称；底部是输入栏；界面简洁干净，字体为清晰的简体中文黑体、字号偏大",
}


VOICES: dict[str, str] = {}  # character -> series_assets/voices/<name>.wav, from the voice bank


def compiler_options(frame=None, *, planning_context=None, environ=None):
    entities = planning_context or PlannerContext.from_env()
    env = os.environ if environ is None else environ
    cap = float(env.get('NOVEL_CLIP_SECONDS_MAX', MAX_CLIP_SECONDS) or 30)
    seconds_from_env = 'NOVEL_CLIP_SECONDS_MAX' in env
    soft_cut = (18.0 if cap > 15 else round(cap * 0.6, 1)) if seconds_from_env else SOFT_CUT_SECONDS
    stages = (6 if cap > 15 else 3) if seconds_from_env else MAX_STAGES
    mode = str(env.get('NOVEL_PACK_MODE', PACK_MODE)).strip() or 'execution'
    return CompilerOptions(max_clip_seconds=cap, soft_cut_seconds=soft_cut,
        max_stages=stages, pack_mode=mode, min_standalone_seconds=MIN_STANDALONE_SECONDS,
        chat_screen=copy.deepcopy(CHAT_SCREEN), anon_voice=copy.deepcopy(ANON_VOICE),
        genre_rejects=list(GENRE_REJECTS), genre_crowd=GENRE_CROWD,
        entity_forms=copy.deepcopy(entities.entity_forms), entity_generic=copy.deepcopy(entities.entity_generic),
        aliases=dict(entities.aliases), frame=dict(frame or frame_spec({'frame':'9:16'})),
        voices=dict(VOICES), two_view_cast_limit=TWO_VIEW_CAST_LIMIT, two_views=TWO_VIEWS)


def load_grammar(path: Path | None, episode_dir: Path) -> dict | None:
    candidate = path or (episode_dir.parent / "visual_grammar.json")
    if candidate and candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def load_voices(novel_dir: Path) -> dict[str, str]:
    """Reference voices built by build_voices_thin.py; empty until the first episodes exist."""
    voices = {}
    manifest = novel_dir / "series_assets" / "voices" / "voices.json"
    if manifest.is_file():
        try:
            rows = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        for name in rows:
            if (novel_dir / "series_assets" / "voices" / f"{name}.wav").is_file():
                voices[name] = f"series_assets/voices/{name}.wav"
    return voices


def load_chat_screen(novel_dir: Path) -> dict:
    """Per-novel chat UI template (outputs/<novel>/chat_screen.json): same group
    name and layout in every clip of every episode."""
    screen = copy.deepcopy(CHAT_SCREEN)
    path = novel_dir / "chat_screen.json"
    if path.is_file():
        screen.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k in screen and v})
    return screen


def load_context(episode_dir: Path, bible_path: Path, grammar_path: Path | None = None, style: str | None = None,
                 frame: str | None = None, tier: str | None = None, *, limits: dict | None = None) -> dict:
    """Load the episode inputs and independent compiler options: genre, frame, voices, chat and limits."""
    planner_ctx = PlannerContext.from_env()
    identity_data = load_chapter(episode_dir)
    bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    grammar = load_grammar(grammar_path, episode_dir)
    chat_screen = load_chat_screen(episode_dir.parent)
    voices = load_voices(episode_dir.parent)
    load_entity_index(episode_dir.parent, chapter_of(episode_dir), ctx=planner_ctx, identity_data=identity_data)
    profile = load_profile(episode_dir.parent, style=style, frame=frame, tier=tier)
    # The method is frozen with the screenplay, not retroactively applied to old
    # chapters when a book changes its default method.
    script_path = episode_dir / 'chapter_script.json'
    script = json.loads(script_path.read_text()) if script_path.is_file() else {}
    method = script.get('story_method') or {}
    if method:
        profile.update(script.get('profile') or {})
        profile.update({k: v for k, v in {'style': style, 'frame': frame, 'tier': tier}.items() if v})
        profile['story_method'] = method['id']
    else:
        profile.pop('story_method', None)
    genre = load_genre(profile)
    options = replace(compiler_options(frame_spec(profile), planning_context=planner_ctx),
        chat_screen=chat_screen, voices=voices,
        genre_rejects=[x for x in [genre.get("era_rejects", "")] + list(genre.get("grammar_rejects_extra", [])) if x],
        genre_crowd=genre.get("crowd_default", ""),
        anon_voice={**DEFAULT_ANON_VOICE, **(genre.get("anon_voice") or {})},
        two_view_cast_limit=0 if is_fast(profile) else 2,
        two_views=str(profile.get('two_views', TWO_VIEWS)),
        camera_policy='authored' if method else 'fixed')
    if limits is not None:
        options = replace(options, **limits)
    overrides_path = episode_dir / "clip_overrides.json"
    return {
        "episode_dir": episode_dir, "bible": bible, "grammar": grammar, "profile": profile, "frame": frame_spec(profile),
        # What this book is actually rendered as, which is not the name of its style package.  The card
        # side has always read render_family (media/asset_style.py); the video side read profile.style and
        # compared it to the literal "3d", so every package whose name is not that word - 唯美 and 3D国漫,
        # both render_family 3d, and 真人, which is photographed - asked H3 for 2D animation while its
        # reference cards were drawn in something else.
        "render_family": str(load_style(profile, episode_dir.parent).get("render_family") or ""),
        "compiler_options": options, "identity_data": identity_data,
        "location_map": {full.split("：", 1)[0].strip(): full for full in bible.locations},
        "overrides": json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.is_file() else {},
    }


def context_for_plan(episode_dir: Path, bible_path: Path, plan: dict) -> dict:
    """Rebuild with the plan's recorded frame, style, tier and clip limits, not today's profile defaults."""
    profile = (plan.get("totals") or {}).get("profile") or {}
    saved = plan.get("limits") or {}
    cap = float(saved.get("max_clip_seconds") or (15 if "-15s" in plan.get("policy", "") else 30))
    limits = {"max_clip_seconds": cap,
              "max_stages": int(saved.get("max_stages") or (3 if cap <= 15 else 6)),
              "soft_cut_seconds": float(saved.get("soft_cut_seconds") or cap * 0.6),
              "camera_policy": saved.get('camera_policy', 'fixed')}
    return load_context(episode_dir, bible_path, style=profile.get("style"), frame=profile.get("frame"),
                        tier=profile.get("tier"), limits=limits)
