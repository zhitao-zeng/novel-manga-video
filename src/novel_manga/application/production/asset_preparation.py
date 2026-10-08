"""Prepare the selected batch's reading/plan assets ahead of video production."""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
import subprocess
import sys
import time

from novel_manga.util import atomic_write_json, read_json, load_dotenv


def probe_endpoint(name):
    """Fail before paid images if their required model checks cannot run."""
    import httpx
    from novel_manga.llm.config import endpoint_settings
    settings=endpoint_settings(name)
    headers={'Authorization':'Bearer '+settings.key} if settings.key else {}
    response=httpx.get(settings.endpoints[0].rstrip('/')+'/models',headers=headers,trust_env=False,timeout=10)
    response.raise_for_status()


def queued_asset_paths(manifest, plan, original, local=None, *, state=None):
    """Only wait for exact shared cards owned by the existing batch job."""
    if (not state or state.get('status')!='running' or not state.get('pid')
            or not Path(f"/proc/{state['pid']}").exists()):return []
    known={(r['asset_id'],r['path']) for r in manifest
           if state.get('results',{}).get(r['asset_id'],{}).get('status') in (None,'pending')}
    missing=set()
    for clip in plan.get('clips',[]):
        for ref in clip.get('references',[]):
            rel=ref.get('path')
            if not rel or (ref.get('asset_id'),rel) not in known:
                continue
            path=Path(original)/rel
            alternatives=[path,*([Path(local)/rel] if local else [])]
            if not any(p.is_file() and p.stat().st_size>0 for p in alternatives):
                missing.add(str(path))
    return sorted(missing)


def required_assets(novel, chapters):
    bible=read_json(novel/'story_bible.json')
    aliases=read_json(novel/'bible_aliases.json',{})
    chars={c['name']:(f'character_{i:03d}',c) for i,c in enumerate(bible['characters'],1)}
    places={p.split('：',1)[0].strip():(f'location_{i:03d}',p) for i,p in enumerate(bible['locations'],1)}
    types=read_json(novel/'entity/types.json',{})
    wanted={}
    def add(kind,ident,name,chapter,design):
        suffix={'character':('characters','turnaround.jpeg'),'location':('locations','establishing.jpeg'),
                'prop':('props','detail.jpeg')}[kind]
        wanted.setdefault(ident,{'asset_id':ident,'role':kind,'name':name,'chapter':chapter,
            'design':design,'path':str(Path('series_assets')/suffix[0]/ident/suffix[1])})
    for chapter in sorted(chapters):
        reading=read_json(novel/f'lean/extract/ch-{chapter:03d}.json',{}).get('answer',{})
        for person in reading.get('people',[]):
            if person.get('presence') != '在场' and not person.get('speaks'):continue
            for form in (person.get('refers_to'),person.get('form')):
                name=aliases.get(form,form)
                if name in chars and types.get(name,{}).get('kind') != 'object':
                    ident,design=chars[name];add('character',ident,name,chapter,design)
        plan=read_json(novel/f'{novel.name}_{chapter}/clip_plan.json',{})
        for clip in plan.get('clips',[]):
            for ref in clip.get('references',[]):
                name=ref.get('name');role=ref.get('role')
                if role=='character' and name in chars and types.get(name,{}).get('kind') != 'object':
                    ident,design=chars[name];add(role,ident,name,chapter,design)
                elif role=='location' and name in places:
                    ident,design=places[name];add(role,ident,name,chapter,design)
                elif role=='prop' and ref.get('asset_id'):
                    add(role,ref['asset_id'],name,chapter,{})
                    # Preserve the actual selected prop view, rather than
                    # inventing a missing detail view for an already usable prop.
                    wanted[ref['asset_id']]['path']=ref['path']
    for place in read_json(novel/'reading_cast.json',{}).get('locations',[]):
        used=set(place.get('chapters',[])) & set(chapters)
        if used and place['name'] in places:
            ident,design=places[place['name']]
            add('location',ident,place['name'],min(used),design)
    return sorted(wanted.values(),key=lambda r:(r['chapter'],r['asset_id']))


def reuse_card(novel, row, caches):
    target=novel/row['path']
    if target.is_file():return 'existing'
    for cache in caches:
        source=cache/row['path'];spec=read_json(source.parent/'spec.json',{})
        if not source.is_file():continue
        if row['role']=='character':
            if any(spec.get(k)!=row['design'].get(k) for k in ('name','appearance','wardrobe')):continue
        elif row['role']=='location':
            if spec.get('name')!=row['design']:continue
        else:continue
        target.parent.mkdir(parents=True,exist_ok=True)
        for suffix in ('','.request.json'):
            src=source.with_suffix(source.suffix+suffix)
            dst=target.with_suffix(target.suffix+suffix)
            if src.is_file() and not dst.exists():shutil.copy2(src,dst)
        if not (target.parent/'spec.json').exists():shutil.copy2(source.parent/'spec.json',target.parent/'spec.json')
        return str(source)
    return None


def main():
    from novel_manga.application.rendering.h3 import episode_numbers
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir',type=Path,required=True)
    parser.add_argument('--chapters',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cache',type=Path,action='append',default=[])
    parser.add_argument('--character-workers',type=int,default=3)
    from novel_manga.llm.config import ENDPOINTS,using_endpoint
    parser.add_argument('--endpoint',required=True,choices=sorted(ENDPOINTS),help='model for asset design and review')
    parser.add_argument('--retry-failed',action='store_true',help='one recovery attempt; existing images are reviewed only')
    args=parser.parse_args()
    from novel_manga.application.configuration import project_root
    load_dotenv(project_root()/'.env')
    with using_endpoint(args.endpoint):
        try:
            probe_endpoint(args.endpoint)
        except Exception as error:
            atomic_write_json(args.output/'state.json',{
                **read_json(args.output/'state.json',{}),'at':time.time(),'pid':os.getpid(),
                'status':'blocked','endpoint':args.endpoint,'reason':f'model endpoint unavailable: {type(error).__name__}'})
            raise
        return prepare_batch(args)


def prepare_batch(args):
    from novel_manga.application.rendering.h3 import episode_numbers
    args.output.mkdir(parents=True,exist_ok=True)
    wanted=required_assets(args.novel_dir,episode_numbers(args.chapters));results={};pending=[]
    atomic_write_json(args.output/'manifest.json',wanted)
    previous=read_json(args.output/'state.json',{}).get('results',{})
    for row in wanted:
        prior=previous.get(row['asset_id'],{})
        resume=prior.get('status')=='pending'
        retry=prior.get('status')=='failed' and args.retry_failed and prior.get('recovery_attempts',0)<1
        recheck=prior.get('status')=='needs_review' and prior.get('review_recovery_attempts',0)<1
        if prior.get('status')=='failed' and not retry:
            results[row['asset_id']]=prior
            continue
        reused=reuse_card(args.novel_dir,row,args.cache)
        if reused=='existing' and not retry and not resume and not recheck:
            # A restart must not erase a known failed card review.
            results[row['asset_id']]=previous.get(row['asset_id'],{'status':'existing'})
        else:
            record={'status':'pending','reused_from':reused,
                'review_only':(retry or resume) and not recheck and not prior.get('fresh_review')
                              and reused=='existing' and row['role'] in {'character','location'},
                'fresh_review':recheck or bool(prior.get('fresh_review')),
                'review_recovery_attempts':prior.get('review_recovery_attempts',0)+int(recheck),
                'recovery_attempts':prior.get('recovery_attempts',0)+int(retry)}
            failure=prior if retry or recheck else prior.get('previous_failure')
            if failure:record['previous_failure']=failure
            # Reserve recovery once before dispatch. A restart resumes this
            # operation rather than forgetting a failed review or granting more.
            results[row['asset_id']]=record
            pending.append({**row,**record})
    def save():
        from collections import Counter
        counts=dict(Counter(r.get('status','unknown') for r in results.values()))
        left=sum(r.get('status')=='pending' for r in results.values())
        atomic_write_json(args.output/'state.json',{'at':time.time(),'total':len(wanted),
            'pid':os.getpid(),'status':'running' if left else
                'finished_with_issues' if counts.get('failed') or counts.get('needs_review') else 'finished',
            'endpoint':args.endpoint,'pending':left,'counts':counts,
            'results':results})
    save()
    script=Path(__file__).resolve().parents[4]/'scripts/build_cards_thin.py'
    def one(row):
        start=time.monotonic()
        log_path=args.output/(row['asset_id']+'.log')
        with log_path.open('a') as log:
            try:
                proc=subprocess.run([sys.executable,str(script),'--novel-dir',str(args.novel_dir),
                    '--assets',row['asset_id'],'--tier','fast',
                    '--review-only' if row['review_only'] else '--review',
                    *(['--fresh-review'] if row['fresh_review'] else [])],stdout=log,stderr=subprocess.STDOUT)
                code=proc.returncode
            except subprocess.TimeoutExpired:code=124
        report={}
        for line in reversed(log_path.read_text(errors='replace').splitlines()):
            try:
                value=json.loads(line)
            except ValueError:continue
            if value.get('asset_id')==row['asset_id']:
                report=value;break
        exists=(args.novel_dir/row['path']).is_file()
        status=('failed' if code or not exists else 'needs_review' if report.get('flags') else 'prepared')
        return {'status':status,'exit':code,'flags':report.get('flags',[]),
                'seconds':round(time.monotonic()-start,1),'reused_from':row['reused_from'],
                'image_exists':exists,'review_only':row['review_only'],
                'fresh_review':row['fresh_review'],'review_recovery_attempts':row['review_recovery_attempts'],
                'recovery_attempts':row['recovery_attempts'],
                **({'previous_failure':row['previous_failure']} if row.get('previous_failure') else {})}
    # One local GPU request at a time; API characters can proceed independently.
    with ThreadPoolExecutor(max_workers=args.character_workers) as remote, ThreadPoolExecutor(max_workers=1) as local:
        jobs={(local if row['role'] in {'location','prop'} and not row['review_only'] else remote).submit(one,row):row for row in pending}
        for future in as_completed(jobs):
            row=jobs[future];results[row['asset_id']]=future.result();save()
    return 0 if all(v['status'] not in {'failed','needs_review'} for v in results.values()) else 2
