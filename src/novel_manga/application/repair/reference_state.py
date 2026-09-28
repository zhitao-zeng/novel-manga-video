"""Re-read a questioned closed-view selection before asking the writer to change their performance."""
from novel_manga.application.packing.visor import fill
from novel_manga.application.repair.flow import rebuild_clips
from novel_manga.application.rendering.h3 import convert
from novel_manga.application.preparation.request_check import request_consistency
from novel_manga.application.profiles import h3_prompt_outdated
from novel_manga.repair.proposal import RepairProposal
from novel_manga.story.h3 import view_of, request_issues
from novel_manga.util import read_json


def propose(directory, clip, checked):
    references = [r for r in clip.get('references', []) if r.get('role') != 'voice']
    concerns = [row for row in checked.get('findings', []) if row.get('relation') == 'contradiction'
                and row.get('aspect') in {'identity', 'visibility', 'state'}
                and row not in checked.get('out_of_scope', [])]
    if not concerns:
        return None
    cited = {row.get('picture') for row in concerns if row.get('picture')}
    names = {r['name'] for i, r in enumerate(references, 1) if (not cited or i in cited)
             and r.get('role') == 'character' and view_of(r) == 'closed'}
    if not names:
        return None
    script = read_json(directory / 'chapter_script.json', {})
    current = read_json(directory / 'visor_states.json', {})
    reason = '；'.join(checked.get('problems', []))
    challenged = {name: {i: reason for i in clip.get('shot_indexes', [])
                        if str(i) in current.get('wearers', {}).get(name, {})} for name in names}
    if not any(challenged.values()):
        return None
    candidate = fill(directory, script=script, write=False, reconsider=challenged)
    if candidate == current:
        return None
    changed_states = {int(i) for name in names for i, row in candidate.get('wearers', {}).get(name, {}).items()
                      if row != current.get('wearers', {}).get(name, {}).get(i)}
    plan = read_json(directory / 'clip_plan.json', {})
    targets = {c['clip_id'] for c in plan['clips'] if changed_states.intersection(c.get('shot_indexes', []))}
    updated, changed = rebuild_clips(directory, directory.parent / 'story_bible.json', script, plan, targets,
                                     visor_states=candidate)
    if not changed:
        return None
    notes = read_json(directory / 'review_feedback.json', {})
    for entry in updated['clips']:
        if entry['clip_id'] not in changed:
            continue
        note = notes.get(entry['clip_id'], '')
        convert(entry, note=note)
        if h3_prompt_outdated(entry, note) or request_issues(entry):
            return None
        result = request_consistency(entry, directory.parent)
        if not result.get('consistent') or result.get('problems'):
            return None
    return RepairProposal({'changed': changed}, script=script, plan=updated, notes=notes,
                          visor_states=candidate, changes={'reference_state_recheck': challenged})
