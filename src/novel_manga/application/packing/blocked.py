"""Repack only broken source-connected clip ranges, preserving other requests."""
from __future__ import annotations
import novel_manga.story.compilation as compilation
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service

import copy
from pathlib import Path

from novel_manga.application.preparation.readiness import plan_issues, read, save_check, collapsed_source_addresses
from novel_manga.util import atomic_write_json


def affected_ranges(clips: list[dict], targets: set[str]) -> list[tuple[int, int]]:
    groups = [{i} for i, c in enumerate(clips) if c['clip_id'] in targets]
    while True:
        before = [set(g) for g in groups]
        for g in groups:
            sources = {s for i in g for s in clips[i].get('shot_indexes', [])}
            g.update(i for i, c in enumerate(clips) if sources.intersection(c.get('shot_indexes', [])))
            g.update(range(min(g), max(g) + 1))
        merged = []
        for g in groups:
            overlaps = [old for old in merged if old & g]
            for old in overlaps:
                g |= old
                merged.remove(old)
            merged.append(g)
        groups = merged
        if groups == before:
            return sorted((min(g), max(g)) for g in groups)


def turn_stream(shots: list[dict]) -> list[tuple]:
    """Splitting a turn may change boundaries, never its speaker, mode or text."""
    result = []
    for shot in shots:
        for t in shot.get('turns', []):
            key = (shot['index'], t.get('speaker_name'), t.get('delivery_mode'), t.get('chat_target'))
            text = t.get('text', '')
            if result and result[-1][0] == key:
                result[-1] = (key, result[-1][1] + text)
            else:
                result.append((key, text))
    return result


def repack(directory: Path, plan: dict, script: dict, *, targets: set[str] | None = None) -> tuple[dict, dict]:
    scope = targets
    addresses = collapsed_source_addresses(plan, script)
    if scope is not None:
        addresses = {cid: indexes for cid, indexes in addresses.items() if cid in scope}
    if addresses:
        plan = copy.deepcopy(plan)
        for clip in plan['clips']:
            if clip['clip_id'] in addresses:
                clip['shot_indexes'] = addresses[clip['clip_id']]
    targets = (set(plan_issues(plan, script)) | set(addresses)) if scope is None else set(scope)
    ctx = packing_context.context_for_plan(directory, directory.parent / 'story_bible.json', plan)
    shots = packing_service.prepared_shots(copy.deepcopy(script), directory, identity_data=ctx.get("identity_data"))
    # Explicit part numbers can be internally consistent yet no longer match
    # the shortened source stage (e.g. 359 / stage 6). Check reconstruction too.
    for clip in plan.get('clips', []):
        if clip.get('kind') == 'video' and clip.get('shot_indexes') and (scope is None or clip['clip_id'] in scope):
            try:
                packing_service.shots_for_plan(plan, shots, {clip['clip_id']}, settings=ctx.get("compiler_options"))
            except ValueError:
                targets.add(clip['clip_id'])
    if not targets:
        return plan, {'changed': [], 'groups': []}
    by_index = {s['index']: s for s in shots}
    clips = plan['clips']
    intervals = affected_ranges(clips, targets)
    occupied = {c['clip_id'] for c in clips}
    occupied.update(p.name for p in (directory / 'work/clips').glob('clip_*'))
    next_id = max((int(x[5:]) for x in occupied if x[5:].isdigit()), default=0) + 1
    result, groups, cursor, changed = [], [], 0, []
    for lo, hi in intervals:
        original = clips[lo:hi + 1]
        indexes = set(i for c in original for i in c.get('shot_indexes', []))
        if not indexes or indexes - set(by_index):
            raise ValueError(f'missing source stages: {sorted(indexes - set(by_index))}')
        selected = [copy.deepcopy(s) for s in shots if s['index'] in indexes]
        packed = packing_service.pack(copy.deepcopy(selected), settings=ctx.get("compiler_options"))
        if turn_stream(selected) != turn_stream([s for c in packed for s in c['shots']]):
            raise ValueError('repack changed source dialogue or its order')
        ids = [c['clip_id'] for c in original]
        while len(ids) < len(packed):
            ids.append(f'clip_{next_id:02d}')
            next_id += 1
        # Old clip-specific cast restrictions do not apply to newly cut ranges.
        replacement = [packing_service.clip_entry(c, cid, ctx, override={}) for c, cid in zip(packed, ids)]
        result.extend(clips[cursor:lo])
        result.extend(replacement)
        cursor = hi + 1
        changed.extend(c['clip_id'] for c in original)
        groups.append({'old': [c['clip_id'] for c in original],
                       'new': [c['clip_id'] for c in replacement], 'source_indexes': sorted(indexes)})
    result.extend(clips[cursor:])
    updated = {**plan, 'clips': result, 'totals': compilation.plan_totals(result, shots, ctx)}
    # Explicit shot_parts now carry the cut; retire only superseded legacy hints.
    if plan.get('split_long_stages'):
        legacy = copy.deepcopy(plan['split_long_stages'])
        legacy['split'] = {k: v for k, v in legacy.get('split', {}).items() if not set(v) & set(changed)}
        updated['split_long_stages'] = legacy
    remaining = plan_issues(updated, script)
    if scope is not None:
        replacement_ids = {cid for group in groups for cid in group['new']}
        remaining = {cid: reasons for cid, reasons in remaining.items() if cid in replacement_ids}
    if remaining:
        raise ValueError(f'repacked plan still blocked: {remaining}')
    return updated, {'changed': changed, 'groups': groups, 'source_addresses': addresses,
                     'old_clip_count': len(clips), 'new_clip_count': len(result)}


def repair_episode(directory: Path, *, apply: bool = False) -> dict:
    plan = read(directory / 'clip_plan.json', {})
    script = read(directory / 'chapter_script.json', {})
    updated, report = repack(directory, plan, script)
    if apply and report['changed']:
        from novel_manga.application.repair.history import begin_trial
        notes = read(directory / 'review_feedback.json', {})
        after_notes = {k: v for k, v in notes.items() if k not in report['changed']}
        targets = set(report['changed']) | {cid for group in report['groups'] for cid in group['new']}
        begin_trial(directory, targets, 'repack_blocked', after_plan=updated,
                    after_notes=after_notes, changes=report)
        atomic_write_json(directory / 'review_feedback.json', after_notes)
        atomic_write_json(directory / 'clip_plan.json', updated)
        save_check(directory, updated, {})
        atomic_write_json(directory / 'plan_repair.json', report)
    return report
