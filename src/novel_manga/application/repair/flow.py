#!/usr/bin/env python
"""Repair only the clips the story judge failed, keeping every other clip's request (and cached render) intact.

    repair_clips_thin.py --novel-dir outputs/X [--episodes 12,48-60 | --targets] [--workers 4] [--apply]

Re-planning a chapter under the storyboard contract rewrites every stage, so every clip re-renders; the
pilot showed that (1,041 clips, 768 "request changed", the rest new cuts).  A failed clip does not need the
chapter re-planned: its own stages need the contract - who is in frame, who does what to whom, which unnamed
extra is there - written with the judge's complaint, the passage and the ledger's casting snapshot in view.
One small model call per failed clip does that; the storyboard shots of that clip are updated in place
(cuts unchanged), the clip is rebuilt from its recorded shot indexes, and only its request changes.
"""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
from novel_manga.application.configuration import project_root
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service

import copy
import json
import sys
import threading
from pathlib import Path

ROOT = project_root()
from novel_manga.repair.proposal import RepairProposal
from novel_manga.application.repair.context import prepare_context
from novel_manga.application.repair.clip import repair_clip

LANE_FIELDS = ("prompt_h3", "prompt_h3_of")


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def failing_clips(review: dict) -> dict[str, str]:
    return {k: str(v.get("story_issue") or v.get("feedback") or "") for k, v in (review.get("clips") or {}).items()
            if (v.get("tier") or v.get("fix_tier")) == "must_fix" and v.get("story_ok") is False}


REBUILD_LOCK = threading.Lock()  # retain the existing rebuild serialization during this refactor


def rebuild_clips(episode_dir: Path, bible_path: Path, script: dict, plan: dict, clip_ids: set[str], *, repack_report: dict | None = None, visor_states=None, preserve_timing: bool = False, source_checked_turns=None) -> tuple[dict, list[str]]:
    """Rebuild only the named clips from their recorded shot indexes; every other clip keeps its entry (and request)."""
    with REBUILD_LOCK:
        ctx = packing_context.context_for_plan(episode_dir, bible_path, plan)
        ctx['source_script'] = script
        if (episode_dir / 'posture_states.json').is_file():
            from novel_manga.application.packing.posture import fill, changed_stages
            previous = read(episode_dir / 'posture_states.json', {})
            ctx['posture_states'] = fill(episode_dir, script=script, previous=previous, write=False)
            dependent = changed_stages(previous, ctx['posture_states'])
            dependent_clips = {c['clip_id'] for c in plan['clips']
                               if dependent.intersection(c.get('shot_indexes', []))}
            clip_ids = clip_ids | dependent_clips
            if repack_report is not None:
                repack_report['_posture_states'] = ctx['posture_states']
                repack_report['_posture_dependent_clips'] = sorted(dependent_clips)
        if visor_states is not None:
            ctx['visor_states'] = visor_states
            if repack_report is not None:
                repack_report['_visor_states'] = visor_states
        elif (episode_dir / 'visor_states.json').is_file():
            from novel_manga.application.packing.visor import fill
            ctx['visor_states'] = fill(episode_dir, script=script, write=False)
            if repack_report is not None:
                repack_report['_visor_states'] = ctx['visor_states']
        from novel_manga.story.h3 import source_crowds
        bible_data=ctx['bible'].model_dump()
        source_segments={str(s.get('segment_id')):s.get('text','') for s in ctx['identity_data'].segments}
        # A source-checked dialogue correction supersedes only those stages'
        # older speaker facts. Keep the old file intact until publication.
        speaker_facts=None
        if source_checked_turns:
            speaker_facts=[fact for fact in read(episode_dir/'source_speaker_contract.json',[])
                           if fact.get('stage') not in source_checked_turns]
            if repack_report is not None:
                repack_report['_speaker_facts']=speaker_facts
        shots = packing_service.prepared_shots(copy.deepcopy(script), episode_dir, identity_data=ctx.get("identity_data"),
                                               posture_states=ctx.get('posture_states'),speaker_facts=speaker_facts)
        if source_checked_turns:
            from novel_manga.story.dialogue import clip_bindings, dialogue_punctuation
            def spoken(shot):
                return [(t['speaker_name'],t['delivery_mode'],bool(t.get('inner_monologue')),
                         dialogue_punctuation(t['text'])) for t in clip_bindings([copy.deepcopy(shot)])]
            approved={s.get('index',i):spoken(s) for i,s in enumerate(script['shots'],1)
                      if s.get('index',i) in source_checked_turns}
            if any(spoken(s)!=approved[s['index']] for s in shots if s['index'] in approved):
                raise ValueError('compiled dialogue differs from the source-checked correction')
        from novel_manga.application.preparation.readiness import location_issues
        by_index = {s['index']: s for s in shots}
        recut = {c['clip_id'] for c in plan.get('clips', []) if c['clip_id'] in clip_ids
                 and c.get('kind') == 'video' and location_issues(c, by_index)}
        if preserve_timing:
            mixed = [c['clip_id'] for c in plan['clips'] if c['clip_id'] in recut and len({
                by_index[i].get('location') for i in c.get('shot_indexes',[]) if i in by_index
                and by_index[i].get('location')}) > 1]
            if mixed:
                raise ValueError('input repair crosses locations inside existing clips: '+', '.join(mixed)+
                                 '; correct the source-supported shot locations without changing cuts')
            recut = set()  # a location rebinding alone must not run the automatic cutter
        recut_ids = set()
        if recut:
            from novel_manga.application.packing.blocked import repack
            plan, report = repack(episode_dir, plan, script, targets=recut, context=ctx)
            recut_ids = {cid for group in report['groups'] for cid in [*group['old'], *group['new']]}
            if repack_report is not None:
                repack_report.update(report)
        # Each clip recovers its own stage parts: a rewritten stage that no longer splits the way the plan
        # recorded (雾月 batch 2: "stage 13 has 1 parts, plan requires part 1/2") leaves that clip as it was
        # instead of failing the episode - and, before this, the whole batch.
        merged, changed, skipped = [], sorted(recut_ids), []
        for before in plan.get("clips") or []:
            if before["clip_id"] in recut_ids or before["clip_id"] not in clip_ids or before.get("kind") != "video":
                merged.append(before)
                continue
            try:
                pieces = ClipCompiler(ctx.get('compiler_options') or compiler_options()).shots_for_plan(plan, shots, {before['clip_id']}).get(before["clip_id"], [])
            except ValueError as error:
                skipped.append(f"{before['clip_id']}: {str(error)[:80]}")
                pieces = []
            if not pieces:
                merged.append(before)
                continue
            clip = {"kind": "video", "location": pieces[0]["location"], "shots": pieces,
                    "seconds": round(sum(ClipCompiler(ctx.get('compiler_options') or compiler_options()).shot_seconds(p) for p in pieces), 2)}
            if preserve_timing or plan.get('policy') == 'episode-agent-authored-v1':
                if [p['index'] for p in pieces] != before['shot_indexes']:
                    raise ValueError('authored input repair changed the existing shot grouping')
                clip.update(request_seconds=before['request_seconds'],
                            shot_timing=copy.deepcopy(before['shot_timing']))
            if plan.get('policy') == 'episode-agent-authored-v1':
                clip['authored_references']=copy.deepcopy(before.get('references',[]))
                if (clip['location'] == before.get('location')
                        and clip['location'] not in ctx['location_map']):
                    # An authored scene label is separate from its selected card.
                    # Preserve that known binding (or its deliberate absence).
                    places = [r for r in before.get('references', []) if r.get('role') == 'location']
                    if not places:
                        clip['reference_location'] = None
                    elif len(places) == 1 and places[0].get('name') in ctx['location_map']:
                        clip['reference_location'] = places[0]['name']
            override = {**(before.get('override') or {}), **ctx['overrides'].get(before['clip_id'], {})}
            if repack_report is not None and '_source_lines' in repack_report:
                from novel_manga.story.dialogue import clip_bindings
                repack_report.setdefault('_source_lines',{})[before['clip_id']]=clip_bindings(pieces)
            after = packing_service.clip_entry(clip, before["clip_id"], ctx, override)
            def seat(ref):
                return tuple(ref.get(k) for k in ('role', 'name', 'asset_id', 'view'))
            old_refs = {seat(r): r for r in before.get('references', [])}
            for ref in after.get('references', []):
                old = old_refs.get(seat(ref))
                if old and Path(old.get('path', '')).is_absolute() and not override.get('reference_paths', {}).get(ref.get('name')):
                    ref['path'] = old['path']
            crowds=source_crowds(after,bible_data,'\n'.join(source_segments.get(str(s),'') for s in after.get('segment_ids',[])), context=ctx["identity_data"].context)
            if crowds:
                after['crowd_roles']=crowds
            # An unchanged request keeps its existing English rendering. A no-op
            # repair must not create a fresh take just by translating it again.
            if all(after.get(k) == before.get(k) for k in ("prompt", "references", "request_seconds", "lines", "chat_lines",'crowd_roles')):
                # Update recovered provenance without losing any runtime/cache
                # mode (notably prompt_h3_skip) from the existing request.
                kept = {**before, **{key: after[key] for key in ("shot_indexes", "shot_parts") if key in after}}
                merged.append(kept)
                if kept != before:
                    changed.append(before["clip_id"])
                continue
            merged.append({k: v for k, v in after.items() if k not in LANE_FIELDS})
            changed.append(before["clip_id"])
        if skipped:
            print("  left as is (stage parts no longer match the plan): " + "; ".join(skipped), flush=True)
        return {**plan, "clips": merged}, changed


def _proposal_data(novel_dir: Path, index: int, *, save_evidence=False, use_history: bool = True, reframe: bool = False, identity: bool = False, source_issues: dict | None = None, require_structure: bool = False, episode_dir: Path | None = None) -> dict:
    episode_dir = Path(episode_dir) if episode_dir is not None else novel_dir / f"{novel_dir.name}_{index}"
    review = read(episode_dir / "episode_review.json", {})
    failing = source_issues if source_issues is not None else failing_clips(review)
    plan = read(episode_dir / "clip_plan.json", None)
    script = read(episode_dir / "chapter_script.json", None)
    segments = {str(s.get("segment_id")): str(s.get("text") or "") for s in read(episode_dir / "segments.json", [])}
    if identity and plan and script:
        from novel_manga.application.identity.source import resolve_script
        identities = resolve_script(script, novel_dir, [{'segment_id': k, 'text': v} for k, v in segments.items()], chapter=index)
        failing = {c['clip_id']: '原文身份消歧：' + json.dumps({i: identities[i] for i in set(c.get('shot_indexes', [])) if i in identities}, ensure_ascii=False)
                   + '。这是同一个人，统一使用正确姓名、角色卡和说话者；删掉旧错误身份的外貌、职业与服装描述，保留原文动作和全部对白。'
                   for c in plan.get('clips', []) if any(
                       set(mapping) & (set(c.get('cast', [])) | {t.get('speaker_name') for t in c.get('lines', [])})
                       for i,mapping in identities.items() if i in c.get('shot_indexes', []))}
        reframe = True
    if not failing or not plan or not script:
        return {"episode": index, "clips": 0, "why": "nothing to repair" if not failing else "no plan/script"}
    chapter = prepare_context(novel_dir, index, script, segments, identity=identity,
                              identities=identities if identity else None, episode_dir=episode_dir)
    repaired, notes, changes, appearance_checks = [], [], {}, {}
    for clip in plan.get("clips") or []:
        cid = clip.get("clip_id")
        if cid not in failing:
            continue
        outcome = repair_clip(clip, failing[cid], chapter, review, use_history=use_history,
                              reframe=reframe, identity=identity, require_structure=require_structure,
                              save_evidence=save_evidence)
        notes.extend(outcome.notes)
        appearance_checks.update(outcome.appearance_checks)
        if outcome.changes is not None:
            changes[cid] = outcome.changes
            repaired.append(cid)
    if not repaired:
        return {"episode": index, "clips": 0, "why": "; ".join(notes)[:250], 'appearance_checks': appearance_checks}
    if identity and set(failing) - set(repaired):
        return {'episode': index, 'clips': 0, 'why': 'identity preparation incomplete: ' + '; '.join(notes)[:160]}
    try:
        structural = {}
        edited_stages = {i for c in plan['clips'] if c['clip_id'] in repaired for i in c.get('shot_indexes', [])}
        affected = {c['clip_id'] for c in plan['clips'] if edited_stages.intersection(c.get('shot_indexes', []))}
        new_plan, changed = rebuild_clips(episode_dir, novel_dir / "story_bible.json", script, plan, affected, repack_report=structural)
        visor_states = structural.pop('_visor_states', None)
        posture_states = structural.pop('_posture_states', None)
    except Exception as error:  # noqa: BLE001 - one episode's rebuild must not take the batch down
        return {"episode": index, "clips": 0, "failing": len(failing), "why": f"rebuild failed: {type(error).__name__}: {str(error)[:100]}"}
    if identity and set(failing) - set(changed):
        return {'episode': index, 'clips': 0, 'why': 'identity clips could not all be rebuilt; source files left unchanged'}
    old_notes = read(episode_dir / 'review_feedback.json', {})
    new_notes = {k: v for k, v in old_notes.items() if k not in changed} if reframe or structural else old_notes
    result = {"episode": index, "clips": len(changed), "failing": len(failing), "why": "; ".join(notes)[:250], "changed": changed,
              'appearance_checks': appearance_checks}
    result['proposal'] = {'script':script,'plan':new_plan,'notes':new_notes,'changes':changes,
                              'structural_repair': structural}
    if visor_states is not None:
        result['proposal']['visor_states'] = visor_states
    if posture_states is not None:
        result['proposal']['posture_states'] = posture_states
    return result


def propose_episode(novel_dir: Path, index: int, **kwargs) -> RepairProposal:
    return RepairProposal.from_result(_proposal_data(novel_dir, index, **kwargs))


def repair_episode(novel_dir: Path, index: int, apply: bool, *, use_history=True, reframe=False,
                   identity=False, source_issues=None, return_proposal=False, require_structure=False, episode_dir=None) -> dict:
    candidate = propose_episode(novel_dir, index, save_evidence=apply,
                                use_history=use_history, reframe=reframe,
                                identity=identity, source_issues=source_issues, require_structure=require_structure, episode_dir=episode_dir)
    if apply and candidate.changed:
        from novel_manga.application.repair.publication import publish_rewrite
        publish_rewrite(Path(episode_dir) if episode_dir is not None else novel_dir / f'{novel_dir.name}_{index}', candidate,
                        use_history=use_history, identity=identity, reframe=reframe)
    return candidate.as_result(return_proposal)
