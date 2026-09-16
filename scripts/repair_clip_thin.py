"""Execute one repair candidate; episode scheduling and publication stay with the caller."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from novel_manga.util import read_json as read
from novel_manga.repair.evidence import source_passage
from novel_manga.repair.execution import apply_stage, action_owners, framing_signature
from novel_manga.story.source_identity import identity_rows
from identity_context_thin import prompt_block
from repair_publication_thin import record_speaker_evidence
from repair_context_thin import RepairChapter
import repair_judges_thin as judges

@dataclass
class ClipRepairResult:
    changes: list | None = None
    notes: list[str] = field(default_factory=list)
    appearance_checks: dict = field(default_factory=dict)


def repair_clip(clip, issue, chapter: RepairChapter, review, *, use_history=True, reframe=False,
                identity=False, require_structure=False, save_evidence=False) -> ClipRepairResult:
    result = ClipRepairResult()
    notes, appearance_checks = result.notes, result.appearance_checks
    cid = clip['clip_id']
    episode_dir = chapter.episode_dir
    bible = chapter.bible
    names = chapter.names
    cast_here = chapter.cast_here
    by_index = chapter.by_index
    segments = chapter.segments
    snapshot = chapter.snapshot
    identity_reading = chapter.identity_reading
    protected_bindings = chapter.protected_bindings
    catalog = chapter.catalog
    identities = chapter.identities
    indexes = [i for i in dict.fromkeys(clip.get("shot_indexes") or []) if i in by_index]
    if not indexes:
        notes.append(f"{cid}: no shot indexes")
        return result
    passage = source_passage(segments, clip.get("segment_ids") or [])
    shots = [{**by_index[i], "origin_index": i} for i in indexes]
    original_stages = copy.deepcopy([by_index[i] for i in indexes])
    identity_legend = [{**row,'presence':cast_here.get(row['name'],'not_established')}
                       for row in identity_rows(names,bible,passage,context=identity_reading,catalog=catalog)]
    for shot in shots:
        corrected_names = set(identities.get(shot['origin_index'], {}).values()) if identity else set()
        if corrected_names:
            looks = '；'.join(row['description'] for row in identity_reading.get('appearances', [])
                             if identity_reading['entities'].get(row['entity_id']) in corrected_names)
            # An identity correction discards the previous identity's look.
            # Only source-backed body facts are called verified appearance.
            shot['visual_prompt'] = f"{'、'.join(shot.get('characters',[]))}在{shot.get('location','')}。{shot.get('motion_prompt','')}。原文形态：{looks or '按本章原文及当前资产确定，不继承旧错误身份的外观'}"
    excluded_speakers = set()
    if reframe and not identity and re.search('台词|说话|说出|发言|对白', issue):
        try:
            contract_path = episode_dir / 'source_speaker_contract.json'
            existing_contracts = read(contract_path, [])
            existing_contracts = list({**{(r['stage'],r['turn']):r for r in existing_contracts}, **protected_bindings}.values())
            evidence = []
            binding_passage = '\n'.join(segments.values())
            attributed = judges.speaker_contract(binding_passage, shots, names,
                identity_rows(names,bible,binding_passage,context=identity_reading,catalog=catalog),
                existing_contracts, evidence, identity_context=identity_reading)
            if save_evidence and evidence:
                merged = {(r['stage'],r['turn']):r for r in existing_contracts}
                merged.update({(r['stage'],r['turn']):r for r in evidence})
                record_speaker_evidence(contract_path,list(merged.values()))
        except Exception as error:
            notes.append(f'{cid}: speaker attribution failed: {type(error).__name__}')
            return result
        if not attributed and any(t.get('delivery_mode') in {'visible_dialogue','offscreen_dialogue'} for s in shots for t in s.get('turns', [])):
            notes.append(f'{cid}: source speaker could not be established')
            return result
        for (stage,turn), name in attributed.items():
            old_name = by_index[stage]['turns'][turn-1].get('speaker_name')
            grounded = {r['name'] for r in identity_legend if r['source_names']}
            if old_name != name and old_name not in grounded and cast_here.get(old_name) != 'on_stage':
                excluded_speakers.add(old_name)
            by_index[stage]['turns'][turn-1]['speaker_name']=name
    history = ""
    if use_history:
        from repair_history import history_context
        history = history_context(episode_dir, cid)
    clip_names = [n for n in names if n not in excluded_speakers]
    request_context = json.dumps({'candidate_identities': identity_legend, "cast": clip.get("cast"), "references": clip.get("references"),
                                  "prompt": clip.get("prompt", ""), "prompt_h3": clip.get("prompt_h3", ""),
                                  "advice": ((review.get("clips", {}).get(cid) or {}).get("verify") or {}).get("repair_advice")},
                                 ensure_ascii=False)[:7500] if reframe else ""
    try:
        answer = judges.rewrite_clip(passage, shots, snapshot, issue, clip_names, history, reframe,
            request_context, prompt_block(episode_dir, clip_names, data=chapter.identity_data), indexes, bible,
            require_structure=require_structure)
    except Exception as error:  # noqa: BLE001
        notes.append(f"{cid}: model {type(error).__name__}: {str(error)[:80]}")
        return result
    fixes = {int(f["origin_index"]): f for f in answer.get("stages") or [] if int(f.get("origin_index", -1)) in indexes}
    if not fixes:
        notes.append(f"{cid}: empty answer")
        return result
    for i, fix in fixes.items():
        if reframe:
            # Picture-writing cannot override the source attribution or an
            # identity correction decided before this call.
            fix['speakers'] = [{'turn_index': j, 'speaker_name': t.get('speaker_name','')}
                               for j,t in enumerate(by_index[i].get('turns', []),1)]
        apply_stage(by_index[i], fix, names, reframe=reframe)
    if require_structure:
        revised = [by_index[i] for i in indexes]
        problem = ('reframe changed action owners instead of camera structure' if any(
                       old and old != new for old,new in zip(action_owners(original_stages), action_owners(revised)))
                   else 'reframe only changed wording; cast and shot scales are unchanged'
                   if framing_signature(original_stages) == framing_signature(revised) else '')
        if problem:
            for i, original in zip(indexes, original_stages):
                by_index[i].clear(); by_index[i].update(original)
            notes.append(f'{cid}: {problem}')
            return result
    revised = [by_index[i] for i in indexes]
    if revised != original_stages:
        try:
            # Legacy scripts use list position as their stage address;
            # origin_index can refer to a different, pre-split numbering.
            check = judges.source_appearance_check(passage, [{**by_index[i], 'index': i} for i in indexes], identity_reading)
            appearance_checks[cid] = check
            if check['issues']:
                raise ValueError('source appearance conflict: ' + '; '.join(i['reason'] for i in check['issues']))
        except Exception as error:
            for i, original in zip(indexes, original_stages):
                by_index[i].clear(); by_index[i].update(original)
            notes.append(f'{cid}: {str(error)[:200]}')
            return result
    result.changes = list(fixes.values())
    return result
