"""The legacy tier presets expanded into independent profile keys.

Lives in planning (not application.profiles) because the planner's brief reads
it too, and the dependency rule is one-way: planning must not import application.
"""
from __future__ import annotations

RESOLUTIONS = ("480p", "720p")
EPISODE_SHAPES = ("flex", "fixed")
# The tier presets, spelled out as the independent keys every consumer reads.  A
# book's profile may set the independent keys directly and omit tier altogether;
# tier remains as a legacy alias so existing profiles keep their behaviour.
TIER_PRESETS = {
    "fast": {"resolution": "480p", "expressions": False, "episode_shape": "flex"},
    "quality": {"resolution": "720p", "expressions": True, "episode_shape": "fixed"},
}


def effective_profile(profile: dict) -> dict:
    """The profile with the legacy tier preset expanded into its independent keys.

    Consumers read resolution / expressions / two_views / episode_shape; an
    explicit key in the book's profile wins over the preset, so a book can be
    480p with expression cards (a combination the tier pair could not express).
    The returned dict carries no "tier" key: nobody should branch on it again.
    """
    out = dict(profile)
    preset = TIER_PRESETS.get(str(out.pop("tier", "quality") or "quality"))
    for key, value in (preset or {}).items():
        out.setdefault(key, value)
    out.setdefault("resolution", "720p")
    out.setdefault("expressions", True)
    out.setdefault("episode_shape", "fixed")
    return out
