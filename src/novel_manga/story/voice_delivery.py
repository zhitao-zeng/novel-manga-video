"""Separate the video model's speech from a thought mixed in after generation."""
from __future__ import annotations
import copy


def postmixed(clip: dict) -> bool:
    return clip.get('audio_delivery') == 'postmix'


def visual_request(clip: dict) -> dict:
    if not postmixed(clip):
        return clip
    visual = copy.deepcopy(clip)
    visual.update(audio_delivery='silent_visual', spoken_text='', lines=[], dialogue_bindings=[])
    visual['references'] = [ref for ref in visual.get('references', []) if ref.get('role') != 'voice']
    return visual
