"""Existing asset record fields shared by full and selected card construction."""
from __future__ import annotations

def character_spec(asset_id, character, bible, prompt):
    return {
                "asset_id": asset_id,
                "name": character.name,
                "role": character.role,
                "gender": character.gender,
                "age": character.age,
                "appearance": character.appearance,
                "wardrobe": character.wardrobe,
                "visual_archetype": character.visual_archetype,
                "face_anchors": character.face_anchors,
                "silhouette": character.silhouette,
                "hair": character.hair,
                "palette": character.palette,
                "base_costume": character.base_costume,
                "episode_costumes": character.episode_costumes,
                "signature_prop": character.signature_prop,
                "expression_profile": character.expression_profile,
                "motion_signature": character.motion_signature,
                "voice_profile_id": character.voice_profile_id,
                "version": "v001",
                "identity_invariants": [
                    value
                    for value in (
                        character.appearance,
                        *character.face_anchors,
                        character.silhouette,
                        character.hair,
                    )
                    if value
                ],
                "state_variables": {
                    "costume": character.base_costume or character.wardrobe,
                    "injury": "none unless changed by source events",
                    "carried_prop": character.signature_prop or "none",
                },
                "reference_scope": {
                    "inherit": ["identity", "hair", "costume", "2d_rendering"],
                    "exclude": ["pose", "composition", "camera", "background", "lighting"],
                },
                "style_fingerprint": bible.style_fingerprint,
                "prompt": prompt,
            }


def location_spec(asset_id, location, bible, prompt):
    return {
                    "asset_id": asset_id,
                    "name": location,
                    "style_fingerprint": bible.style_fingerprint,
                    "continuity": "固定空间布局、物品锚点、天气、时间、光线方向",
                    "version": "v001",
                    "identity_invariants": [f"{location}固定建筑、出入口和空间层级"],
                    "state_variables": {
                        "time_of_day": "approved_reference_state",
                        "weather": "approved_reference_state",
                        "damage": "none unless changed by source events",
                    },
                    "reference_scope": {
                        "inherit": ["architecture", "space", "color", "lighting", "2d_rendering"],
                        "exclude": ["composition", "camera", "temporary_people", "text"],
                    },
                    "prompt": prompt,
                }
