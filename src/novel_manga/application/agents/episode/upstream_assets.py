"""Source-backed shared card candidates; adopting a card is not video acceptance."""
from __future__ import annotations
import copy
import fcntl
import json
import shutil
from pathlib import Path

from novel_manga.application.identity.phases import LOOK_FIELDS, chapter_of
from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.review.endpoints import judge_settings
from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError, UnsupportedInput


OPERATION = 'upstream_asset'


def validate(ctx, row):
    if set(row) - {'clip_id','action','asset_id','reason','source_quote','fields','clip_ids','fact_scope'}:
        raise RepairArgumentError('repair_assets requires registered asset_id, source_quote and appearance fields')
    source = (ctx.run_dir/'source.txt').read_text()
    quote = str(row.get('source_quote') or '').strip()
    if not quote or quote not in source or not str(row.get('reason') or '').strip():
        raise RepairArgumentError('repair_assets requires a reason and verbatim source_quote')
    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    target = next((c for c in plan.get('clips', []) if c['clip_id']==row.get('clip_id')), None)
    if not target:raise RepairArgumentError('repair_assets requires a current clip')
    ref = next((r for r in target.get('references', []) if r.get('asset_id')==row.get('asset_id')), None)
    if not ref or ref.get('role') not in {'character','location'}:
        raise RepairArgumentError('select this clip registered character or location asset')
    picture = Path(ref['path'])
    if not picture.is_absolute():picture=ctx.novel_dir/picture
    if not picture.is_file():raise RepairArgumentError('selected asset image is missing; use normal missing-card preparation')
    fields = row.get('fields', {})
    allowed = set(LOOK_FIELDS) if ref['role']=='character' else {'description'}
    if (not isinstance(fields,dict) or set(fields)-allowed
            or any(not isinstance(v,str) for v in fields.values())):
        raise RepairArgumentError('use existing appearance fields or location description')
    if row.get('fact_scope','chapter') not in {'chapter','permanent'}:
        raise RepairArgumentError('fact_scope is chapter or permanent')
    bible = read_json(ctx.novel_dir/'story_bible.json')
    if ref['role']=='character':
        actor = next((c for c in bible.get('characters',[]) if c['name']==ref['name']), None)
        if not actor:raise RepairArgumentError('resolve missing source actor identity before changing an existing card')
        design = {k:v for k,v in actor.items() if k not in {'name','voice_profile_id','episode_costumes'}}
        selected = next((p for p in read_json(ctx.novel_dir/'series_assets/phases.json',{})
            .get('characters',{}).get(ref['name'],[]) if p.get('asset_id')==ref['asset_id']), {})
        overrides = {k:v for k,v in selected.items() if k in LOOK_FIELDS}
        if 'base_costume' in overrides and 'wardrobe' not in overrides:
            overrides['wardrobe']=overrides['base_costume']
        design.update(overrides)
        design.update(fields)
        if 'base_costume' in fields and 'wardrobe' not in fields:design['wardrobe']=fields['base_costume']
    else:
        old = next((s.partition('：')[2] for s in bible.get('locations',[]) if s.partition('：')[0]==ref['name']), '')
        design = fields.get('description',old)
    affected = [c['clip_id'] for c in plan['clips'] if any(
        r.get('asset_id')==ref['asset_id'] and r.get('role')==ref['role'] for r in c.get('references',[]))]
    scope = affected if row.get('clip_ids') is None else row['clip_ids']
    if not isinstance(scope,list) or not scope or row['clip_id'] not in scope or set(scope)-set(affected):
        raise RepairArgumentError('include all current clips that use this actor or setting: '+', '.join(affected))
    registered=read_json(ctx.novel_dir/'series_assets/manifest.json',{})
    owner=next((r.get('name')for group in ('characters','locations')for r in registered.get(group,[])if r.get('asset_id')==ref['asset_id']),None)
    return {**copy.deepcopy(row),'registered_owner':owner,'fields':fields,'source_quote':quote,'source_text':source,
            'role':ref['role'],'name':ref['name'],'picture':str(picture),'design':design,
            'affected_clips':list(scope),'fact_scope':row.get('fact_scope','chapter')}


def diagnose(ctx, row):
    prompt = ('独立核对原文、当前设计与实际参考图，旧评审的指控不是事实。'
        'kind只能是binding（人物/地点引用或归属错）、design（设计文字与明确原文冲突）、'
        'image（实际图与源文支持的设计冲突）、false_positive（无实质矛盾）、uncertain（身份/事实无法确定）。'
        '原文未明确年龄、衣料、脸型等时允许现有美术设计，不因未提到判剧情错误。'
        '不把泛称合并成另一具名角色。年龄阶段、附体和换装不能当永久基础形态。'
        '核对此次字段与直接依赖，不提出额外剧情；不能凭Agent申请就默认缺陷成立。'
        '有明确事实依据的design/image须给逐字source_quote；binding_shots仅给有源依据的原镜修正，'
        '每项含index与现有入镜/说话人字段，不修改机位或事件；不能判断则uncertain。\n'
        +json.dumps({k:row[k] for k in ('source_text','source_quote','reason','role','name','fields','design','registered_owner')},ensure_ascii=False))
    answer=ask_json([{'type':'text','text':prompt},image_part(Path(row['picture']),1024)],
        obj({'kind':{'type':'string','enum':['binding','design','image','false_positive','uncertain']},
             'reason':{'type':'string'},'source_quote':{'type':'string'},
             'replacement_asset_id':{'type':'string'},
             'binding_shots':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['index'],'properties':{'index':{'type':'integer'},'in_frame':{'type':'array','items':{'type':'string'}},'speaker_map':{'type':'object','additionalProperties':{'type':'string'}},'listeners':{'type':'array','items':{'type':'string'}}}}}}),
        name='upstream_asset_diagnosis',max_tokens=1500,timeout=180,settings=judge_settings())
    quote=str(answer.get('source_quote') or '').strip()
    if answer['kind'] in {'design','image','binding'} and (not quote or quote not in row['source_text']):
        raise UnsupportedInput('asset diagnosis lacks a verbatim source fact')
    return {**row,**answer,'source_quote':quote or row['source_quote']}


def _receipt(ctx, checked):
    folder=ctx.episode_dir/'work/input_repairs';folder.mkdir(parents=True,exist_ok=True)
    from .input_assets import style_key
    signature={k:checked[k] for k in ('clip_id','asset_id','fields','source_quote','source_text','fact_scope','design')}
    signature['style']=style_key(ctx)
    with (folder/'upstream-assets.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        own=sorted(folder.glob(f"{checked['clip_id']}-*.json"),key=lambda p:p.stat().st_mtime_ns)
        for p in reversed(own):
            record=read_json(p,{})
            if record.get('operation')==OPERATION and record.get('signature')==signature:return p,record
        number=max((int(p.stem.rsplit('-',1)[1]) for p in own),default=0)+1
        p=folder/f"{checked['clip_id']}-{number}.json"
        record={'operation':OPERATION,'clip_id':checked['clip_id'],'status':'checking',
                'step':'source','signature':signature,'upstream_decision':{k:v for k,v in checked.items()
                    if k in {'clip_id','action','asset_id','fields','source_quote','reason','fact_scope'}},
                'resume_count':0}
        atomic_write_json(p,record)
        return p,record


def prepare(ctx, row):
    checked=validate(ctx,row);path,record=_receipt(ctx,checked)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);record=read_json(path,record)
        if record.get('status')=='prepared':return record['result']
        if record.get('status')=='rejected':raise UnsupportedInput(record['error'])
        if record.get('status')=='incomplete':
            if record.get('resume_count',0)>=1:raise UnsupportedInput('asset continuation used; preserve completed candidate')
            record['resume_count']=record.get('resume_count',0)+1
        try:
            diagnosis=record.get('diagnosis') or diagnose(ctx,checked)
            record.update(diagnosis=diagnosis,status='checking',step='source');atomic_write_json(path,record)
            outcome={**diagnosis,'asset':None,'ok':diagnosis['kind']!='uncertain','evidence_path':str(path),
                     'changed':[],'input_repaired':[]}
            if diagnosis['kind'] in {'image','design','binding'}:
                from .input_repair import source_check
                phases={'characters':{diagnosis['name']:[diagnosis['fields']]}} if diagnosis['role']=='character' else {}
                locations={diagnosis['name']:diagnosis['design']} if diagnosis['role']=='location' else {}
                addresses={i for c in read_json(ctx.episode_dir/'clip_plan.json')['clips']
                           if c['clip_id']in checked['affected_clips'] for i in c.get('shot_indexes',[])}
                scoped=[shot for i,shot in enumerate(read_json(ctx.episode_dir/'chapter_script.json')['shots'],1)
                        if shot.get('index',i) in addresses]
                record['source']=source_check(checked['source_text'],diagnosis['source_quote'],
                    diagnosis.get('binding_shots') or [],phases,locations,scoped)
                outcome['source']=record['source'];atomic_write_json(path,record)
            if diagnosis['kind'] in {'image','design'}:
                from .draft_assets import _asset
                record['step']='assets';atomic_write_json(path,record)
                asset=_asset(ctx,diagnosis['role'],diagnosis['name'],diagnosis['design'],
                    base_asset=checked['asset_id'],asset_root=ctx.source_novel_dir)
                outcome.update(asset=asset,image_review=asset['image_review'])
            if diagnosis['kind']=='binding' and diagnosis.get('binding_shots'):
                from .input_contracts import normalize_arguments
                outcome['input_decision']=normalize_arguments({'clip_id':row['clip_id'],'action':'input',
                    'clip_ids':checked['affected_clips'],'reason':diagnosis['reason'],
                    'source_quote':diagnosis['source_quote'],'shots':diagnosis['binding_shots']})
            record.update(status='prepared',step='complete',result=outcome);atomic_write_json(path,record)
            return outcome
        except Exception as error:
            record.update(status='rejected' if isinstance(error,UnsupportedInput) else 'incomplete',error=str(error))
            atomic_write_json(path,record);raise


def apply_to_snapshot(ctx, result):
    path=Path(result['evidence_path']);record=read_json(path,{})
    if record.get('operation')!=OPERATION or record.get('status')!='prepared':
        raise UnsupportedInput('asset candidate has no complete source/image receipt')
    result=copy.deepcopy(record['result']);asset=result.get('asset')
    if not asset:return result
    if result.get('source',{}).get('consistent') is not True or asset.get('image_review',{}).get('consistent') is not True:
        raise UnsupportedInput('asset candidate is not independently source/image checked')
    from .input_assets import local_phases
    phasefile=local_phases(ctx);phases=read_json(phasefile,{})
    chapter=chapter_of(ctx.episode_dir)
    group='characters' if result['role']=='character' else 'locations'
    filename='turnaround.jpeg' if group=='characters' else 'establishing.jpeg'
    target=phasefile.parent/group/asset['asset_id']/filename;target.parent.mkdir(parents=True,exist_ok=True)
    source=ctx.source_novel_dir/asset['path']
    if not target.exists():shutil.copy2(source,target)
    for suffix in ('.request.json','.task.json'):
        meta=source.with_name(source.name+suffix)
        if meta.is_file():shutil.copy2(meta,target.with_name(target.name+suffix))
    phase={'from':chapter,'to':chapter,'asset_id':asset['asset_id'],'label':result['reason'],
           **(result['design'] if group=='characters' else {'description':result['design'],'base_asset':result['asset_id']})}
    entries=phases.setdefault(group,{}).setdefault(result['name'],[])
    if not any(p.get('asset_id')==asset['asset_id'] and p.get('from')==chapter for p in entries):entries.insert(0,phase)
    atomic_write_json(phasefile,phases)
    if result['fact_scope']=='permanent' and group=='characters':
        from novel_manga.application.review.character_updates import reconcile_characters
        updated=reconcile_characters(ctx.novel_dir,[result['name']],source=result['source_text'],
                                     character_facts={result['name']:[{'quote':result['source_quote'],**result['fields']}]})
        if updated.get('rejected'):raise UnsupportedInput('permanent fact correction was rejected: '+str(updated['rejected']))
    decision={'clip_id':record['clip_id'],'action':'input','clip_ids':result['affected_clips'],
              'reason':result['reason'],'source_quote':result['source_quote']}
    if group=='characters':decision['phases']={'characters':{result['name']:{'reuse_asset_id':asset['asset_id']}}}
    else:decision['locations']={result['name']:{'reuse_asset_id':asset['asset_id']}}
    result.update(input_decision=decision,next='Use the verified input candidate; video permission and final acceptance remain independent.')
    record.update(result=result,adopted=True);atomic_write_json(path,record)
    return result


def failed_attempt(ctx, row):
    """An unchanged terminal candidate is evidence of an attempt, even without time grant."""
    if row.get('action')!='repair_assets':return False
    checked=validate(ctx,row)
    from .input_assets import style_key
    signature={k:checked[k] for k in ('clip_id','asset_id','fields','source_quote','source_text','fact_scope','design')}
    signature['style']=style_key(ctx)
    for path in (ctx.episode_dir/'work/input_repairs').glob(f"{row['clip_id']}-*.json"):
        record=read_json(path,{})
        if record.get('operation')==OPERATION and record.get('signature')==signature:
            if record.get('status')=='rejected':return True
            if record.get('status')=='prepared' and not (record.get('result')or{}).get('ok'):return True
            if record.get('status')=='incomplete' and record.get('resume_count',0)>=1:return True
    return False
