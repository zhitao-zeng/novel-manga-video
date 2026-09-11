"""Per-novel production profile for the thin pipeline: art style and frame.

`outputs/<novel>/profile.json` holds {"style": "2d"|"3d", "frame": "9:16"|"16:9"}.
Every thin script reads it; command-line flags override per run.  The style
text is what the asset factory routes on, so it must contain a 2D token for
2d and a 3D token (and no 2D token) for 3d.
"""
from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path

DEFAULTS = {"style": "2d", "frame": "9:16", "tier": "quality", "genre": "generic"}
TIERS = ("quality", "fast")

FRAMES = {
    "9:16": {"width": 1080, "height": 1920, "text": "竖屏9:16", "video_ratio": "9:16", "image_ratio": "9:16",
             "composition": "竖屏构图：主体沿纵向三分线，特写可占满上半幅，对话双方前后错开"},
    "16:9": {"width": 1920, "height": 1080, "text": "横屏16:9", "video_ratio": "16:9", "image_ratio": "16:9",
             "composition": "横屏构图：主体沿横向三分线，对话双方左右分布并留出环境，特写不要把脸撑满整幅，避免竖屏式的上下堆叠"},
}

STYLE_VISUAL = {
    "2d": ("二维赛璐璐国风动画：清晰且有粗细变化的轮廓线，纯色平涂，两级硬边阴影，哑光皮肤，人物比例自然；"
           "禁止真人照片、塑料玩偶质感和游戏角色创建界面"),
    "3d": ("高品质中国3D国漫CG动画，三维渲染、PBR材质：简化雕塑式东方面部结构，自然人体比例，"
           "哑光细腻皮肤带轻微次表面散射，束状建模发丝，布料、木石、金属有真实体块与克制高光，"
           "电影化体积光与分层景深，整体接近《凡人修仙传》《斗破苍穹》年番的三维国漫质感；"
           "角色必须是一眼可辨的动画角色造型：眼睛略大、五官简化概括、皮肤光滑无毛孔无老年斑，老年角色也用动画化的皱纹表现；"
           "禁止真人照片、真实人物肖像、写实皮肤纹理、塑料玩偶质感、平面线稿和游戏角色创建界面"),
}
STYLE_NAME = {"2d": "二维国漫", "3d": "3D国漫"}


def load_profile(novel_dir: Path, **overrides) -> dict:
    profile = dict(DEFAULTS)
    path = Path(novel_dir) / "profile.json"
    if path.is_file():
        profile.update(json.loads(path.read_text(encoding="utf-8")))
    profile.update({key: value for key, value in overrides.items() if value})
    if profile["frame"] not in FRAMES:
        raise ValueError(f"profile.frame must be one of {list(FRAMES)}")
    if profile["style"] not in STYLE_VISUAL:
        raise ValueError(f"profile.style must be one of {list(STYLE_VISUAL)}")
    if profile.get("tier", "quality") not in TIERS:
        raise ValueError(f"profile.tier must be one of {TIERS}")
    profile.setdefault("tier", "quality")
    return profile


def is_fast(profile: dict | None) -> bool:
    """The fast tier trades polish for throughput: no planning think-pass or
    redo, ~60 s episodes of 3 clips, one reference card per character, 480p,
    no speech-gate regeneration, no episode review."""
    return bool(profile) and profile.get("tier") == "fast"


def frame_spec(profile: dict) -> dict:
    return FRAMES[profile["frame"]]


def styled_bible(bible, profile: dict):
    """Return the bible with visual_style replaced by the profile's style text."""
    return bible.model_copy(update={"visual_style": STYLE_VISUAL[profile["style"]]})


def plan_fingerprint(plan: dict) -> str:
    """Digest of what the renderer actually consumes from a clip plan: per clip
    the kind, prompt, reference images, requested seconds and chat messages.  Policy strings,
    lint notes and totals are left out so a packer version bump does not make
    every rendered episode look stale."""
    material = [
        (clip.get("clip_id"), clip.get("kind"), clip.get("prompt", ""), [ref.get("path") for ref in clip.get("references", [])], clip.get("request_seconds"), clip.get("text", ""),
         # the chat cards are rendered from these, so a message change must
         # count as a plan change even when the video prompts are identical
         [(row.get("speaker_name"), row.get("chat_target", ""), row.get("text")) for row in clip.get("chat_lines", [])])
        for clip in plan.get("clips", [])
    ]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def h3_source_digest(prompt: str, note: str = "") -> str:
    """What build_h3_prompts.py stamps as prompt_h3_of: which Chinese prompt - and director correction, which goes into
    the English prompt - an English one was made from.  Without a correction it is the digest of the prompt alone."""
    note = (note or "").strip()
    material = prompt + (f"\n【导演修正】{note}" if note else "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def h3_prompt_outdated(clip: dict, note: str = "") -> bool:
    """A video clip a local-H3 lane cannot render yet: it has no English prompt, or one made from an
    earlier Chinese prompt (the chapter was re-packed since) or before its current correction."""
    if not clip.get("prompt_h3"):
        return True
    made_from = clip.get("prompt_h3_of")
    return bool(made_from) and made_from != h3_source_digest(clip.get("prompt") or "", note)


def h3_prompt_fingerprint(plan: dict) -> str:
    """Digest of the English prompts a local-H3 lane renders the plan from.  plan_fingerprint covers the
    Chinese prompts only, so the runner stamps this beside it and thin_batch compares it on an H3 lane:
    a new English prompt makes the episode stale there, as a new Chinese one does everywhere."""
    material = [(clip.get("clip_id"), None if clip.get("prompt_h3_skip") else clip.get("prompt_h3"))
                for clip in plan.get("clips", []) if clip.get("kind") == "video"]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()


def qwen_endpoints() -> list[str]:
    """All local Qwen base URLs (QWEN38_LOCAL_BASE_URL may be comma-separated)."""
    raw = os.environ.get("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def endpoint_order(key: str) -> list[str]:
    """Endpoints in the order to try for one request: a stable pick by key
    (spreads chapters over instances) followed by the others as fallbacks."""
    endpoints = qwen_endpoints()
    start = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(endpoints)
    return endpoints[start:] + endpoints[:start]


GENRES_DIR = Path(__file__).resolve().parents[1] / "configs" / "genres"


def load_genre(profile: dict | None) -> dict:
    """Genre preset (configs/genres/<genre>.json) merged over the generic one:
    era objects, text-on-props policy, anonymous roles, card style cues,
    location-card policy, moderation softening pairs, chat-screen default."""
    base = json.loads((GENRES_DIR / "generic.json").read_text(encoding="utf-8"))
    key = (profile or {}).get("genre") or "generic"
    path = GENRES_DIR / f"{key}.json"
    if path.is_file():
        base.update(json.loads(path.read_text(encoding="utf-8")))
    base["key"] = key
    return base


def detect_genre(*texts: str) -> str:
    """Pick the preset whose keywords appear most in the given texts (bible genre
    line, title, opening chapters); generic when nothing matches."""
    scores: dict[str, int] = {}
    for path in GENRES_DIR.glob("*.json"):
        preset = json.loads(path.read_text(encoding="utf-8"))
        hits = sum(text.count(word) for text in texts for word in preset.get("keywords", []))
        if hits:
            scores[path.stem] = hits
    return max(scores, key=scores.get) if scores else "generic"
