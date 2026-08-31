from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VoxVoiceProfile:
    key: str
    description: str
    seed_text: str
    aliases: tuple[str, ...] = ()


VOICE_PROFILES = (
    VoxVoiceProfile(
        "deep_male",
        "三十五岁中国男性，低沉浑厚、沉稳有力量的声音，标准普通话，吐字清楚，语速自然偏快",
        "雨后的长街没有行人，只有他的脚步声越来越近。",
        ("alloy", "onyx", "uncle_fu", "uncle-fu", "叔叔傅"),
    ),
    VoxVoiceProfile(
        "young_male",
        "二十五岁中国男性，清朗但不单薄的中低音，克制自然，标准普通话，语速自然偏快",
        "他停在门前，抬手敲了三下，却迟迟没有等到回应。",
        ("verse", "dylan"),
    ),
    VoxVoiceProfile(
        "heroic_male",
        "三十岁中国男性，厚实坚定、略带锋芒的声音，标准普通话，节奏利落",
        "远处风声骤然停下，他终于看清了来人的面容。",
        ("ash", "ryan"),
    ),
    VoxVoiceProfile(
        "mature_male",
        "四十五岁中国男性，成熟磁性、从容威严的声音，标准普通话，咬字稳重",
        "所有人都安静下来，等待他宣布最后的决定。",
        ("echo", "eric"),
    ),
    VoxVoiceProfile(
        "teen_male",
        "十八岁中国男性，少年感清晰、自然灵动但不尖细，标准普通话",
        "少年猛地回过头，眼里写满了难以置信。",
        ("fable", "aiden"),
    ),
    VoxVoiceProfile(
        "warm_female",
        "二十八岁中国女性，温暖柔和、清晰有亲和力的声音，标准普通话，语速自然",
        "窗外的雨已经停了，她轻声说出了那个名字。",
        ("coral", "serena"),
    ),
    VoxVoiceProfile(
        "composed_female",
        "三十二岁中国女性，冷静克制、成熟清晰的声音，标准普通话，节奏稳定",
        "她合上手中的文件，平静地看向对面的人。",
        ("sage", "vivian"),
    ),
    VoxVoiceProfile(
        "bright_female",
        "二十二岁中国女性，明亮自然、有活力但不稚嫩的声音，标准普通话",
        "晨光穿过窗帘，她终于露出了轻松的笑容。",
        ("nova", "ono_anna", "ono-anna"),
    ),
    VoxVoiceProfile(
        "gentle_female",
        "二十四岁中国女性，轻柔细腻、情绪自然的声音，标准普通话，吐字清楚",
        "她停顿了一瞬，随后轻轻握住了对方的手。",
        ("shimmer", "sohee"),
    ),
)


_PROFILE_BY_ALIAS = {
    alias.casefold(): profile
    for profile in VOICE_PROFILES
    for alias in (profile.key, *profile.aliases)
}


def resolve_voice_profile(voice: str | None) -> VoxVoiceProfile:
    normalized = str(voice or "alloy").strip().casefold()
    return _PROFILE_BY_ALIAS.get(normalized, VOICE_PROFILES[0])


def stable_voice_seed(voice: str | None, suffix: str = "") -> int:
    profile = resolve_voice_profile(voice)
    digest = hashlib.sha256(f"{profile.key}:{suffix}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def voice_cache_name(voice: str | None) -> str:
    profile = resolve_voice_profile(voice)
    return re.sub(r"[^a-z0-9_-]+", "-", profile.key.casefold()).strip("-") + ".wav"


def extract_performance_style(instructions: str | None) -> str:
    text = str(instructions or "")
    match = re.search(r"(?:语义表演|角色语气|表演意图)[：:]\s*([^。；;]+)", text)
    parts = [match.group(1).strip()] if match else []
    if "画外旁白" in text:
        parts.append("画外旁白，叙事感自然")
    return "，".join(part for part in parts if part)[:120]


def voice_design_text(profile: VoxVoiceProfile) -> str:
    return f"({profile.description}){profile.seed_text}"


def styled_clone_text(text: str, instructions: str | None) -> str:
    style = extract_performance_style(instructions)
    if not style:
        return text
    return f"({style}){text}"
