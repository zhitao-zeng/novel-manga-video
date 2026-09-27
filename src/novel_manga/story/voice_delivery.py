"""Separate the video model's speech from a thought mixed in after generation."""
from __future__ import annotations
import copy


def speech_sequence(stages):
    """Ordered authored words and delivery, allowing only adjacent same-owner text to be split/joined."""
    result = []
    for stage in stages:
        for turn in stage.get('turns') or []:
            if turn.get('delivery_mode') not in {'visible_dialogue', 'offscreen_dialogue'} or not turn.get('text'):
                continue
            key = (turn.get('speaker_name'), turn['delivery_mode'], bool(turn.get('inner_monologue')))
            if result and result[-1][0] == key:
                result[-1] = (key, result[-1][1] + turn['text'])
            else:
                result.append((key, turn['text']))
    return result


def postmixed(clip: dict) -> bool:
    return clip.get('audio_delivery') == 'postmix'


def visual_request(clip: dict) -> dict:
    if not postmixed(clip):
        return clip
    visual = copy.deepcopy(clip)
    visual.update(audio_delivery='silent_visual', spoken_text='', lines=[], dialogue_bindings=[])
    visual['references'] = [ref for ref in visual.get('references', []) if ref.get('role') != 'voice']
    return visual
