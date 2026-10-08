"""Compose fixed H3 syntax around the Agent's unchanged shot directions."""
from __future__ import annotations

import re

from novel_manga.story.h3 import compose
from novel_manga.story.voice_delivery import visual_request
from .arguments import RepairArgumentError


def compose_request(clip, directions):
    if isinstance(directions, str):
        if not directions.strip():
            raise RepairArgumentError('final request requires nonempty English directions')
        return directions
    if not isinstance(directions, dict):
        raise RepairArgumentError('H3 directions require a shots/soundscape object or a complete request string')
    shots = directions.get('shots')
    count = int(clip.get('stage_count') or len(clip.get('shot_indexes', [])))
    if not isinstance(shots, list) or len(shots) != count or any(not isinstance(s, str) or not s.strip() for s in shots):
        actual = len(shots) if isinstance(shots, list) else type(shots).__name__
        raise RepairArgumentError(f'H3 shots requires {count} nonempty English shot bodies; '
            f"source shot_indexes={clip.get('shot_indexes', [])}, received={actual}. "
            'Use one string per authored shot, not one per actor or direction sentence; retain its dialogue slots.')
    sound = directions.get('soundscape', '')
    if not isinstance(sound, str):
        raise RepairArgumentError('H3 soundscape requires an English string')
    if re.search(r'</?d>|<DIALOGUE_|\(S\d+\)|<Audio \d+>', sound):
        raise RepairArgumentError('H3 soundscape cannot contain native speech or dialogue slots')
    visual = visual_request(clip)
    for stage, text in enumerate(shots, 1):
        lines = [r for r in visual.get('dialogue_bindings', []) if r['stage'] == stage]
        wanted = [f'<DIALOGUE_{i}>' for i in range(1, len(lines)+1)]
        if re.findall(r'<DIALOGUE_\d+>', text) != wanted:
            raise RepairArgumentError(f'Shot {stage}: dialogue slots must occur once in order: {wanted}')
        if re.search(r'<d>|\(S\d+\)|\[Shot \d+\]|Planned duration:|At \d\d:\d\d', text):
            raise RepairArgumentError(f'Shot {stage}: host supplies speech bindings, shot headers and timing; write body and dialogue slots only')
    return compose(clip, shots, [('', []) for _ in shots], soundscape=sound, authored=True)


def compose_repair(clip, decision):
    """Write new shot bodies while retaining the accepted reference contract."""
    from novel_manga.application.repair.agent import sections, SECTIONS
    if 'h3' in decision and 'prompt_h3' in decision:
        raise RepairArgumentError('choose structured h3 or complete prompt_h3, not both')
    directions = decision.get('h3', decision.get('prompt_h3'))
    prompt = compose_request(clip, directions)
    if isinstance(directions, dict) and clip.get('prompt_h3'):
        old, new = sections(clip['prompt_h3']), sections(prompt)
        # A structured repair authors actions and sound. It does not change the
        # established picture/voice associations, retention modes or music.
        for name in ('subject_definitions', 'retention_analysis', 'non_diegetic_music'):
            new[name] = old[name]
        prompt = '\n\n'.join(f'{name}:\n{new[name]}' for name in SECTIONS)
    return prompt
