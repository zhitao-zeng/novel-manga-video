"""Source addresses shared by authored, scene-directed and ordinary shot plans."""
from __future__ import annotations
import copy


def segment_ids(shots):
    ids = []
    for shot in shots:
        refs = [r.get('segment_id') for r in shot.get('source_refs') or [] if r.get('segment_id')]
        ids.extend(refs or ([shot['segment_id']] if shot.get('segment_id') else []))
    return list(dict.fromkeys(ids))


def restore_plan_sources(plan, script):
    """Restore existing addresses only; requests, cuts, selected assets and retry accounting are untouched."""
    shots = {int(s.get('index', i)): s for i, s in enumerate(script.get('shots') or [], 1)}
    out = copy.deepcopy(plan); changed = []
    for clip in out.get('clips') or []:
        indexes = clip.get('shot_indexes') or []
        if clip.get('kind') != 'video' or not indexes or any(i not in shots for i in indexes):
            continue
        ids = segment_ids([shots[i] for i in indexes])
        if ids and ids != clip.get('segment_ids'):
            clip['segment_ids'] = ids
            # Diagnostic of an actual historical correction, not an operator feature switch. It
            # makes a cached verdict without source context recheck once, including after interruption.
            clip['source_trace_repaired'] = True
            changed.append(clip['clip_id'])
    return out, changed
