"""Compile and validate one scoped input candidate; publication belongs to the caller."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .context import EpisodeContext
from .arguments import CompileConflict, first_pass_scope
from novel_manga.application.repair import history
from novel_manga.repair.proposal import RepairProposal
from novel_manga.util import atomic_write_json, read_json


@dataclass(frozen=True)
class InputCompilation:
    ctx: EpisodeContext
    row: dict
    plan: dict
    script: dict
    candidate_script: dict
    shots: dict
    patches: list
    target: dict
    targets: set
    counts: dict
    scope: list | None
    phases: dict
    locations: dict
    characters: dict
    cid: str


@dataclass(frozen=True)
class CompiledInput:
    proposal: RepairProposal | None = None
    plan: dict | None = None
    changed: list | None = None


def compile_input(case: InputCompilation, receipt, record, progress) -> CompiledInput:
    from novel_manga.application.repair.flow import rebuild_clips
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.application.preparation.request_check import request_consistency
    from novel_manga.application.preparation.readiness import render_risks, worn_reference_pairs
    from novel_manga.application.preparation.wearer_cards import check_references
    from novel_manga.story.h3 import request_issues
    ctx = case.ctx
    row = case.row
    plan = case.plan
    script = case.script
    candidate_script = case.candidate_script
    shots = case.shots
    patches = case.patches
    target = case.target
    targets = case.targets
    counts = case.counts
    scope = case.scope
    phases = case.phases
    locations = case.locations
    characters = case.characters
    cid = case.cid
    directory = ctx.episode_dir
    progress('compile')
    structural = {'_source_lines':{}} if any('turns' in p for p in patches) else {}
    source_checked_turns={p['index'] for p in patches if 'turns' in p or p.get('speaker_map')}
    candidate, changed = rebuild_clips(directory, ctx.novel_dir/'story_bible.json', candidate_script, plan, targets,
                                      repack_report=structural, preserve_timing=True,
                                      **({'source_checked_turns':source_checked_turns} if source_checked_turns else {}))
    if not changed:
        if phases or locations or candidate_script != script:
            raise ValueError('input correction did not reach the compiled request')
        check_references(ctx.novel_dir, worn_reference_pairs({'clips': [target]}))
        remaining, _ = render_risks(target, ctx.novel_dir)
        checked = request_consistency(target, ctx.novel_dir)
        if remaining or checked.get('consistent') is not True or checked.get('problems'):
            raise ValueError('existing inputs remain blocked: ' + '; '.join(remaining + (checked.get('problems') or [])))
        record.update(status='checked_existing_wearing', step='complete')
        atomic_write_json(receipt, record)
        return CompiledInput()
    old_ids=[c['clip_id'] for c in plan['clips']]
    new_ids=[c['clip_id'] for c in candidate['clips']]
    if old_ids!=new_ids:
        raise CompileConflict(f'input repair changed clip grouping; before={old_ids}; after={new_ids}',
                              {'kind':'recut','before':old_ids,'after':new_ids})
    extra=sorted(set(changed)-targets)
    verified_dependencies=set(structural.pop('_posture_dependent_clips', []))
    if extra and set(extra) <= verified_dependencies:
        first_pass_scope(ctx, set(extra))
        exhausted = [key for key in extra if counts.get(key,0) >= history.preparation_limit(directory,key)]
        if exhausted:
            raise ValueError('dependent input repair exceeds remaining video budget: '+', '.join(exhausted))
        if (phases or locations or characters) and scope is not None and not set(extra) <= set(scope):
            raise CompileConflict('source state scope excludes dependent clips: '+', '.join(extra),
                {'kind':'dependency_scope','requested_clips':sorted(targets),'extra_clips':extra})
        targets.update(extra)
        record['dependent_clips']=extra
        atomic_write_json(receipt, record)
        extra=[]
    if extra:
        old={c['clip_id']:c for c in plan['clips']};new={c['clip_id']:c for c in candidate['clips']}
        fields={cid:sorted(k for k in set(old[cid])|set(new[cid]) if old[cid].get(k)!=new[cid].get(k)) for cid in extra}
        details={'kind':'dependency_scope','requested_clips':sorted(targets),'extra_clips':extra,'changed_fields':fields,
                 'code':'novel_manga/application/repair/flow.py:rebuild_clips'}
        raise CompileConflict(f'compiler changed dependent clips outside requested scope: {fields}; '
                              'clip grouping stayed unchanged; inspect the dependency scope before retrying',details)
    old_clips = {c['clip_id']: c for c in plan['clips']}
    notes = {key: value for key, value in read_json(directory/'review_feedback.json', {}).items()
             if key not in changed}
    cache_path = receipt.with_suffix('')/'requests.json'
    translations = read_json(cache_path, {})
    for clip in candidate['clips']:
        key = clip['clip_id']
        if key not in changed:
            continue
        before = old_clips[key]
        if any(clip.get(field) != before.get(field) for field in ('request_seconds', 'shot_indexes', 'shot_timing')):
            raise ValueError('input repair changed protected timing or shot addresses')
        from novel_manga.story.dialogue import dialogue_punctuation
        from .speech_budget import capacity_issue, expected_lines
        old_shots = {s.get('index', i): s for i, s in enumerate(script['shots'], 1)}
        old_lines = expected_lines(before, old_shots, shots, patches)
        rewritten_turns=any('turns' in p and p['index'] in before.get('shot_indexes',[]) for p in patches)
        if rewritten_turns:
            old_lines=structural.get('_source_lines',{}).get(key)
            if old_lines is None:
                raise ValueError('source dialogue compilation evidence missing')
        new_lines = clip.get('lines', [])
        if rewritten_turns or any('turn_texts' in p and p['index'] in before.get('shot_indexes', []) for p in patches):
            if problem := capacity_issue(clip):
                raise ValueError(problem)
        approved_owners = {pair for patch in patches if patch['index'] in before.get('shot_indexes', [])
                           for pair in (patch.get('speaker_map') or {}).items()}
        if (len(old_lines) != len(new_lines) or any(
                (old.get('delivery_mode'), bool(old.get('inner_monologue')), dialogue_punctuation(old.get('text', ''))) !=
                (new.get('delivery_mode'), bool(new.get('inner_monologue')), dialogue_punctuation(new.get('text', '')))
                or (old.get('speaker_name') != new.get('speaker_name') and
                    (old.get('speaker_name'), new.get('speaker_name')) not in approved_owners)
                for old, new in zip(old_lines, new_lines))):
            raise ValueError('input repair changed protected dialogue or owners')
        if 'generation_seed' in before:
            clip['generation_seed'] = before['generation_seed']
        clip['repair_take'] = int(before.get('repair_take', 0)) + 1
        progress('translate:'+key)
        from novel_manga.application.profiles import h3_stamp
        cached = translations.get(key) or {}
        translation_errors = []
        if cached.get('prompt_h3_of') == h3_stamp(clip, '') and cached.get('prompt_h3'):
            clip.update(cached)
        else:
            convert(clip, note='', episode_dir=directory, source_script=candidate_script,
                    problems=translation_errors)
        if translation_errors:
            raise ValueError(f'input repair H3 translation rejected {key}: ' + '; '.join(translation_errors))
        if not str(clip.get('prompt_h3') or '').strip():
            raise ValueError('input repair translation produced no English H3 request; retain current plan')
        if clip.get('prompt_h3_of') != h3_stamp(clip, ''):
            raise ValueError(f'input repair H3 translation is stale for {key}; retain current plan')
        if request_issues(clip):
            raise ValueError('repaired request format failed: ' + '; '.join(request_issues(clip)))
        translations[key] = {field: clip.get(field) for field in ('prompt_h3', 'prompt_h3_of')}
        atomic_write_json(cache_path, translations)
        progress('check:'+key)
        checked = request_consistency(clip, ctx.novel_dir)
        if checked.get('consistent') is not True or checked.get('problems'):
            raise ValueError('repaired inputs still conflict: ' + '; '.join(checked.get('problems') or []))
    check_references(ctx.novel_dir, worn_reference_pairs(candidate))
    for clip in candidate['clips']:
        for ref in clip.get('references', []):
            if clip['clip_id'] in changed and not (ctx.novel_dir/ref['path']).is_file():
                raise ValueError(f'repaired reference missing: {ref["path"]}')
    structural.pop('_source_lines',None)
    proposal = RepairProposal(result={'changed': changed}, script=candidate_script, plan=candidate,
        notes=notes, changes={cid: row}, posture_states=structural.pop('_posture_states', None),
        source_speaker_contract=structural.pop('_speaker_facts', None),
        visor_states=structural.pop('_visor_states', None), structural_repair=structural)
    return CompiledInput(proposal, candidate, changed)
