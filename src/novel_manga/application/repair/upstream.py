"""Host-owned source/reference repair for the existing bounded sandbox Agent."""
from __future__ import annotations
import copy
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from novel_manga.application.repair.flow import rebuild_clips
from novel_manga.application.rendering.h3 import convert
from novel_manga.application.preparation.request_check import request_consistency as check_request
from novel_manga.repair.proposal import RepairProposal
from novel_manga.util import atomic_write_json, read_json

POLICY='source-reference-audio-v1'
SHOT_FIELDS={'camera','visual_prompt','motion_prompt','start_state','end_state','sfx','avoid','light'}


def catalog(directory):
    return read_json(directory/'repair_candidates.json',{})


def catalog_for_clip(directory,clip):
    value=copy.deepcopy(catalog(directory))
    bindings={(r.get('role'),r.get('name')) for r in clip.get('references',[])}
    def usable(asset):
        binding=asset.get('binding')or asset
        return (binding.get('role'),binding.get('name')) in bindings
    value['assets']=[asset for asset in value.get('assets',[]) if usable(asset)]
    for asset in value['assets']:
        binding=asset.get('binding')or asset
        asset['source_path']=str((directory.parent/asset['path']).resolve()) if asset.get('path') else None
        selected=[ref for ref in clip.get('references',[]) if ref.get('path') and
                  (ref.get('role'),ref.get('name'))==(binding.get('role'),binding.get('name')) and
                  str((directory.parent/ref['path']).resolve())==asset['source_path']]
        asset['currently_selected']=bool(selected)
        asset['selected_reference_tags']=[ref['tag'] for ref in selected if ref.get('tag')]
    value['source_suggestions']=[row for row in value.get('source_suggestions',[]) if row.get('clip_id')==clip['clip_id']]
    value['current_reference_bindings']=[{'role':role,'name':name} for role,name in sorted(bindings)]
    return value


def records(directory,cid):
    return [read_json(p,{}) for p in (directory/'work/input_repairs').glob(f'{cid}-upstream-*.json')]


def available(directory,cid):
    return len(records(directory,cid))<2


def record(directory,cid,decision,result):
    folder=directory/'work/input_repairs';folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'{cid}-upstream-{len(records(directory,cid))+1:02d}.json'
    atomic_write_json(path,{'operation':'managed_upstream','policy':POLICY,'clip_id':cid,
                            'decision':decision,'result':result,'status':'prepared'})


def source_text(directory,script):
    segments=read_json(directory/'segments.json',[])
    return '\n'.join([str(s.get('text')or'') for s in segments]+[
        str(s.get('source_quote')or'') for s in script.get('shots',[])])


def check_source(source,quote,patches,assets,shots):
    from novel_manga.application.repair.source_support import source_check
    return source_check(source,quote,patches,{},scoped_shots=shots,
                        characters={a['name']:a.get('design',{}) for a in assets if a['role']=='character'},
                        locations={a['name']:a.get('design','') for a in assets if a['role']=='location'})


def check_compilation(shots,clip):
    """Reference compatibility alone cannot certify source/camera fidelity."""
    import json
    from novel_manga.llm.client import ask_json,obj
    from novel_manga.review.endpoints import judge_settings
    return ask_json([{'type':'text','text':
        '独立核对本次源分镜修复后的实际英文请求是否忠实。只检查此次片段：机位/可见与画外物体、'
        '原事件顺序、相同人物和服装、深浅/干湿空间状态、发声与明确静音区段。'
        '正在录制的设备属于视点，不能自动变成被外部相机拍摄的入画道具。'
        '源分镜若要求设备保持画外，英文展示它就是冲突。原生片问题的旧报告不是真实源文。'
        '不要要求逐字翻译，不为未改变的美术细节制造矛盾。只列具体确定的源文/英文冲突。\n'+
        json.dumps({'approved_shots':shots,'actual_english':clip.get('prompt_h3'),'actual_references':clip.get('references',[])},ensure_ascii=False)}],
        obj({'consistent':{'type':'boolean'},'problems':{'type':'array','items':{'type':'string'}}}),
        name='agent_input_compiled_source',max_tokens=1200,timeout=180,settings=judge_settings())


def _selected_assets(directory,choices,clips):
    entries={a['id']:a for a in catalog(directory).get('assets',[])}
    selected=[]
    for choice in choices:
        entry=entries.get(choice.get('candidate_id'))
        if not entry or (entry.get('review')or{}).get('consistent') is not True or (entry.get('review')or{}).get('problems'):
            raise ValueError('reference candidate has no passing image review')
        image=Path(entry['path'])
        if not image.is_absolute():image=directory.parent/image
        with Image.open(image) as opened:opened.verify()
        binding=entry.get('binding')or{'role':entry['role'],'name':entry['name']}
        entry={**entry,'role':binding['role'],'name':binding['name']}
        if not any(r.get('role')==entry['role'] and r.get('name')==entry['name'] for c in clips for r in c.get('references',[])):
            available=sorted({f'{r.get("role")}/{r.get("name")}' for c in clips for r in c.get('references',[])})
            raise ValueError(f'candidate {entry["id"]} requests {entry["role"]}/{entry["name"]}, '
                             f'which has no reference binding in this scope; available bindings: {available}. '
                             'Do not add a visible actor to a POV-only shot; use its applicable location candidate and source fields.')
        selected.append({**entry,'path':str(image.resolve())})
    return selected


def input_proposal(directory,plan,clip,decision):
    script=read_json(directory/'chapter_script.json',{})
    scope=decision.get('clip_ids') or [clip['clip_id']]
    if not isinstance(scope,list) or clip['clip_id'] not in scope or set(scope)-{c['clip_id'] for c in plan['clips']}:
        raise ValueError('input scope must contain current target clips')
    targets=set(scope);chosen=[c for c in plan['clips'] if c['clip_id'] in targets]
    indexes={i for c in chosen for i in c.get('shot_indexes',[])}
    candidate_script=copy.deepcopy(script)
    shots={s.get('index',i):s for i,s in enumerate(candidate_script['shots'],1)}
    patches=decision.get('shots') or [];seen=set()
    for patch in patches:
        index=patch.get('index')
        if index not in indexes or index in seen or set(patch)-{'index'}-SHOT_FIELDS:
            raise ValueError(f'invalid source patch {patch}: allowed shot indexes={sorted(indexes)}; '
                             f'allowed fields={sorted(SHOT_FIELDS)}. Write actual field:text pairs, '
                             'not supported_source_fields. For reference-only selection omit shots entirely.')
        if any(not isinstance(v,str) for k,v in patch.items() if k!='index'):
            raise ValueError('source patch fields must be text')
        if any(index in c.get('shot_indexes',[]) and c['clip_id'] not in targets for c in plan['clips']):
            raise ValueError('source patch must include all clips sharing that shot')
        seen.add(index);shots[index].update(copy.deepcopy(patch))
    assets=_selected_assets(directory,decision.get('assets')or[],chosen)
    if not patches and not assets:raise ValueError('input repair has no source or reference correction')
    quote=str(decision.get('source_quote')or'').strip();source=source_text(directory,script)
    # Selecting an already independently reviewed asset may cite that asset's
    # approved design passage; it need not also occur in this scene's dialogue.
    source+='\n'+'\n'.join(str(a.get('source_quote')or'') for a in assets)
    if not quote or quote not in source:raise ValueError('input repair requires a verbatim source quote')
    # clip_ids permits a scope; it does not ask the current compiler to refresh every entry.
    targets={c['clip_id'] for c in chosen if seen.intersection(c.get('shot_indexes',[])) or any(
        (r.get('role'),r.get('name'))==(a['role'],a['name']) for r in c.get('references',[]) for a in assets)}
    if clip['clip_id'] not in targets:raise ValueError('input correction does not affect its current target')
    indexes={i for c in chosen if c['clip_id'] in targets for i in c.get('shot_indexes',[])}
    check=check_source(source,quote,patches,assets,[shots[i] for i in sorted(indexes)])
    if check.get('consistent') is not True or check.get('problems'):raise ValueError('source correction did not pass review')
    structural={}
    seed_plan=copy.deepcopy(plan)
    for target in seed_plan['clips']:
        if target['clip_id'] not in targets:continue
        for asset in assets:
            bound=[r for r in target.get('references',[]) if (r.get('role'),r.get('name'))==(asset['role'],asset['name'])]
            if not bound:continue
            override=target.get('override') or {};target['override']=override
            if asset.get('mode')=='body_view':
                from novel_manga.story.h3 import view_of
                face=next((r for r in bound if view_of(r)=='expressions'),bound[0])
                face_path=Path(face['path'])
                if not face_path.is_absolute():face_path=directory.parent/face_path
                override.setdefault('reference_views',{})[asset['name']]={'body':asset['path'],
                    'face':str(face_path),'asset_id':asset['id']}
            else:override.setdefault('reference_paths',{})[asset['name']]=asset['path']
    baseline_report={}
    baseline,_=rebuild_clips(directory,directory.parent/'story_bible.json',script,plan,targets,
                             preserve_timing=True,repack_report=baseline_report)
    if baseline_report.get('_compile_skipped'):
        raise ValueError('baseline source compiler skipped clips: '+str(baseline_report['_compile_skipped']))
    candidate,compiled_changes=rebuild_clips(directory,directory.parent/'story_bible.json',candidate_script,seed_plan,targets,
                                            preserve_timing=True,repack_report=structural)
    if structural.get('_compile_skipped'):
        raise ValueError('source correction compiler skipped clips: '+str(structural['_compile_skipped']))
    if [c['clip_id'] for c in candidate['clips']]!=[c['clip_id'] for c in plan['clips']]:
        raise ValueError('input correction changed clip grouping')
    old={c['clip_id']:c for c in plan['clips']}
    if set(compiled_changes)-targets:raise ValueError('input compiler changed unrelated clips')
    base={c['clip_id']:c for c in baseline['clips']}
    effect_fields=('prompt','references','lines','chat_lines','shot_indexes','request_seconds','shot_timing',
                   'action_bindings','wears_bindings','dialogue_bindings','audio_delivery','inner_voice')
    changed={c['clip_id'] for c in candidate['clips'] if c['clip_id'] in targets and any(
        c.get(field)!=base[c['clip_id']].get(field) for field in effect_fields)}
    if not changed:
        selected=[]
        for current in chosen:
            for asset in catalog_for_clip(directory,current)['assets']:
                if asset['id'] in {a['id'] for a in assets} and asset['currently_selected']:
                    selected.append(f'{current["clip_id"]}: candidate {asset["id"]} already selected at '
                                    f'{asset["selected_reference_tags"]}; source_path={asset["source_path"]}')
        detail=('; '+ '; '.join(selected)+
                '. This selection is already active; sandbox filenames are aliases, not different assets. '
                'Recheck actual current frames: selected input does not prove the video passed. '
                'Use keep with picture evidence for an erroneous native finding; '
                'if a real deviation remains and no video allowance is left, stop.') if selected else ''
        raise ValueError('no candidate input effect beyond current compiler drift'+detail)
    candidate['clips']=[c if c['clip_id'] in changed else copy.deepcopy(old[c['clip_id']]) for c in candidate['clips']]
    notes=read_json(directory/'review_feedback.json',{})
    for target in candidate['clips']:
        cid=target['clip_id']
        if cid not in targets:continue
        if cid not in changed:continue
        before=old[cid]
        for key in ('shot_indexes','request_seconds','shot_timing','lines','chat_lines'):
            if target.get(key)!=before.get(key):raise ValueError('input compiler changed protected timing/dialogue')
        target['repair_take']=int(before.get('repair_take',0))+1
        target.pop('prompt_h3',None);target.pop('prompt_h3_of',None)
        errors=[];convert(target,note='',episode_dir=directory,source_script=candidate_script,problems=errors)
        if errors or not target.get('prompt_h3'):raise ValueError('source correction English compilation failed: '+str(errors))
        checked=check_request(target,directory.parent)
        if checked.get('consistent') is not True or checked.get('problems'):
            raise ValueError('corrected source/reference request remains inconsistent: '+str(checked))
        faithful=check_compilation([shots[i] for i in target['shot_indexes']],target)
        if faithful.get('consistent') is not True or faithful.get('problems'):
            raise ValueError('compiled request changed approved source facts: '+str(faithful))
        notes.pop(cid,None)
    if not changed:raise ValueError('source correction did not reach actual request')
    return RepairProposal({'changed':sorted(changed),'why':decision.get('reason','source/reference correction'),
                            'source_check':check,'baseline_recompiled_with_current_code':True,
                            'material_changes':{c['clip_id']:[field for field in effect_fields
                                if c.get(field)!=base[c['clip_id']].get(field)]
                                for c in candidate['clips'] if c['clip_id'] in changed}},
                          script=candidate_script,plan=candidate,notes=notes,
                          changes={cid:decision for cid in changed},
                          posture_states=structural.get('_posture_states'),visor_states=structural.get('_visor_states'))


def create_asset(directory,clip,decision):
    """Use existing source/image-reviewed API factory, preserving old assets."""
    from novel_manga.application.assets import candidates as draft_assets
    script=read_json(directory/'chapter_script.json',{})
    spec=decision.get('asset')or{};role=spec.get('role');name=spec.get('name')
    if role not in {'character','location'} or not any((r.get('role'),r.get('name'))==(role,name) for r in clip.get('references',[])):
        raise ValueError('asset repair must retain an existing character/location binding')
    quote=str(decision.get('source_quote')or'').strip();source=source_text(directory,script)
    if not quote or quote not in source:raise ValueError('asset repair requires verbatim source quote')
    design=spec.get('design')
    if not (isinstance(design,dict) if role=='character' else isinstance(design,str)) or not design:
        raise ValueError('asset repair requires source-backed design')
    check=check_source(source,quote,[],[{'role':role,'name':name,'design':design}],script['shots'])
    if check.get('consistent') is not True or check.get('problems'):raise ValueError('asset source check failed')
    ctx=SimpleNamespace(novel_dir=directory.parent,run_dir=directory/'work/upstream-assets',asset_snapshot_dir=directory.parent)
    ctx.run_dir.mkdir(parents=True,exist_ok=True)
    accepted=draft_assets._asset(ctx,role,name,design,asset_root=directory.parent)
    entry={**accepted,'id':accepted['asset_id'],'path':str(directory.parent/accepted['path']),
           'review':accepted['image_review'],'source_quote':quote}
    saved=catalog(directory);saved.setdefault('assets',[])
    if not any(a['id']==entry['id'] for a in saved['assets']):saved['assets'].append(entry)
    atomic_write_json(directory/'repair_candidates.json',saved)
    return input_proposal(directory,read_json(directory/'clip_plan.json'),clip,
                          {**decision,'action':'input','assets':[{'candidate_id':entry['id']}]})


def adopt_audio(directory,clip,decision):
    from .ambience import adopt
    return adopt(directory,clip,decision)
