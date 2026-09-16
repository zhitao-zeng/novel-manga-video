#!/usr/bin/env python3
"""Small paired test of actual framing changes after grounded dialogue binding.

No production media is modified. A frozen source and a fixed seed/backend are
shared by both arms; each arm permits one H3 submission. Speech remains observe.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

from benchmark_repair_cause import (ROOT, ROOT_FILES, EPISODE_FILES, copy_if_present, clone_case,
                                    render_arm, evaluation_passed)
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from review_store_thin import current_takes
from novel_manga.util import load_dotenv

EXTRA_ROOT = ['review_normal.txt']
EXTRA_EPISODE = ['identity_context.json','source_speaker_contract.json','repair_history/history.json']


def clone(frozen, dest, case):
    novel, episode = clone_case(frozen, dest, case)
    for name in EXTRA_ROOT:
        copy_if_present(frozen/name, novel/name)
    for name in EXTRA_EPISODE:
        copy_if_present(frozen/episode.name/name, episode/name)
    # History informs both repairers. The experimental request budget is owned
    # by submission receipts, not the production episode's exhausted budget.
    h=read(episode/'repair_history/history.json',{})
    for trial in h.get('trials',[]):
        trial['managed']=False
    if h:
        atomic_write_json(episode/'repair_history/history.json',h)
    from identity_flow_thin import resolve_chapter
    resolve_chapter(episode)
    return novel,episode


def freeze(novel, output, specs):
    if (output/'manifest.json').exists():
        return read(output/'manifest.json')
    from novel_manga.repair.scheduling import active_episodes
    from identity_store_thin import load_catalog
    from novel_manga.review.storage import take_identity
    busy=active_episodes(read(novel/'repair_manager/state.json',{'jobs':[]}))
    frozen=output/'frozen'/novel.name
    for name in ROOT_FILES+EXTRA_ROOT:
        copy_if_present(novel/name,frozen/name)
    for p in (novel/'entity').glob('*.json'):
        copy_if_present(p,frozen/'entity'/p.name)
    for name in ['manifest.json','phases.json']:
        copy_if_present(novel/'series_assets'/name,frozen/'series_assets'/name)
    for p in (novel/'series_assets/voices').glob('*'):
        if p.is_file():
            copy_if_present(p,frozen/p.relative_to(novel))
    catalog=load_catalog(novel)
    cases=[]
    for spec in specs:
        ep,cid=spec.split(':');ep=int(ep)
        if ep in busy:
            raise ValueError(f'{ep} is owned by an active production job; freeze after it finishes')
        d=novel/f'{novel.name}_{ep}';target=frozen/d.name
        plan=read(d/'clip_plan.json',{});review=read(d/'episode_review.json',{});takes=current_takes(d,plan,review)
        clip=next(c for c in plan['clips'] if c['clip_id']==cid)
        row=review['clips'][cid]
        if row.get('verify',{}).get('verdict')!='obvious' or cid not in takes:
            raise ValueError(f'{spec} is not a current reviewed failure')
        for name in EPISODE_FILES+EXTRA_EPISODE:
            copy_if_present(d/name,target/name)
        atomic_write_json(target/'clip_plan.json',{**plan,'clips':[clip]})
        video=Path(takes[cid]['video'])
        copy_if_present(video,target/'failed.mp4')
        if take_identity(video)!=takes[cid]['take']:
            raise ValueError('production take changed while freezing')
        passage='\n'.join(s['text'] for s in read(d/'segments.json',[]))
        names={n for s in read(d/'chapter_script.json',{}).get('shots',[]) for n in s.get('characters',[])}
        for entity in catalog.candidates(passage,names):
            aid=entity.get('asset_id')
            if aid:
                for p in (novel/'series_assets/characters').glob(f'{aid}*/turnaround.jpeg'):
                    copy_if_present(p,frozen/p.relative_to(novel))
        for c in plan['clips']:
            for ref in c.get('references',[]):
                if ref.get('path'):
                    copy_if_present(novel/ref['path'],frozen/ref['path'])
        cases.append({'id':f'{novel.name}_{ep}_{cid}','episode':ep,'clip_id':cid,
                      'original_seconds':clip['request_seconds'],'seed':202609160+len(cases),
                      'source_video':str(video),'source_take':takes[cid]['take']})
    if not 1<=len(cases)<=4 or 2*sum(c['original_seconds'] for c in cases)>120:
        raise ValueError('pilot exceeds four pairs or 120 requested video seconds')
    manifest={'created_at':time.strftime('%F %T'),'source_novel':str(novel),'frozen_novel':str(frozen),
              'cases':cases,'max_submissions':2*len(cases),'max_parallel':2,'speech_gate':'observe',
              'backend':'GPU052-A / minimax-h3-ref2va-turbo','evidence_level':'probe',
              'contract':'A=current reframe; B=changed cast/shot scale with established action owners preserved; same source, dialogue, seconds, seed and backend; no automatic production publication'}
    atomic_write_json(output/'manifest.json',manifest)
    return manifest


def evaluate(output, case, arm, video):
    from verify_clips_thin import Verifier
    frozen=Path(read(output/'manifest.json')['frozen_novel'])
    dest=output/'evaluation'/case['id']/arm
    if (dest/'verdict.json').exists():
        return read(dest/'verdict.json')
    novel,ep=clone(frozen,dest,case)
    atomic_write_json(ep/'episode_review.json',{'clips':{case['clip_id']:{'video':str(video)}}})
    v=Verifier(novel,dest/'raw.jsonl','blind_local',1,max_tokens=1600)
    result=v.verify((case['episode'],case['clip_id'],'','paired'))
    atomic_write_json(dest/'verdict.json',result)
    return result


def prepare(output, case, arm):
    from repair_flow_thin import repair_episode,framing_signature
    from dialogue_binding import apply_confirmed_speakers
    from build_h3_prompts import convert
    from h3_request_checks import request_issues
    from thin_profile import h3_prompt_outdated
    frozen=Path(read(output/'manifest.json')['frozen_novel']);dest=output/'runs'/case['id']/arm
    if (dest/'prepared.json').exists():
        return read(dest/'prepared.json')
    novel,ep=clone(frozen,dest,case);script=read(ep/'chapter_script.json',{});plan=read(ep/'clip_plan.json',{})
    apply_confirmed_speakers(ep,script['shots'])
    atomic_write_json(ep/'chapter_script.json',script)
    cid=case['clip_id'];clip=plan['clips'][0];indexes=set(clip['shot_indexes'])
    stages=[s for i,s in enumerate(script['shots'],1) if s.get('index',i) in indexes]
    speech=lambda ss:[(t.get('speaker_name'),t['delivery_mode'],t['text']) for s in ss for t in s['turns']
                      if t['delivery_mode'] in {'visible_dialogue','offscreen_dialogue'}]
    before_speech=speech(stages);before_framing=framing_signature(stages)
    started=time.monotonic()
    result=repair_episode(novel,case['episode'],False,use_history=True,reframe=True,
                          source_issues={cid:read(ep/'episode_review.json')['clips'][cid].get('feedback') or
                                             read(ep/'episode_review.json')['clips'][cid].get('story_issue','按原文修正画面')},
                          require_structure=arm=='B',return_proposal=True)
    proposal=result.get('proposal')
    if not proposal:
        raise ValueError('preparation failed: '+result.get('why','no proposal'))
    revised=[s for i,s in enumerate(proposal['script']['shots'],1) if s.get('index',i) in indexes]
    if speech(revised)!=before_speech:
        raise ValueError('arm changed frozen dialogue words, owner or delivery mode')
    if arm=='B' and framing_signature(revised)==before_framing:
        raise ValueError('B has no actual cast/shot-scale change')
    entry=next(c for c in proposal['plan']['clips'] if c['clip_id']==cid)
    if entry.get('segment_ids')!=clip.get('segment_ids') or entry.get('seconds_estimate',0)>case['original_seconds']:
        raise ValueError('arm changed source coverage or exceeded shared duration')
    entry['request_seconds']=case['original_seconds']
    note=proposal['notes'].get(cid,'');convert(entry,note=note)
    if h3_prompt_outdated(entry,note) or request_issues(entry):
        raise ValueError('final request failed binding or translation checks')
    for ref in entry.get('references',[]):
        if ref['path'].endswith('expressions.jpeg') or not (novel/ref['path']).is_file():
            raise ValueError('arm requires an unavailable frozen asset')
    atomic_write_json(ep/'chapter_script.json',proposal['script'])
    atomic_write_json(ep/'clip_plan.json',{**proposal['plan'],'clips':[entry]})
    atomic_write_json(ep/'review_feedback.json',proposal['notes'])
    result={'case':case,'arm':arm,'novel':str(novel),'episode':str(ep),
            'request_seconds':entry['request_seconds'],'prepare_seconds':round(time.monotonic()-started,3),
            'before_framing':before_framing,'after_framing':framing_signature(revised)}
    atomic_write_json(dest/'prepared.json',result)
    return result


def run(output):
    # The production admission poll sleeps three seconds between attempts. A
    # small experiment can miss every brief vacancy while existing workers
    # reacquire slots. Keep the same lock files and limit, with a bounded poll.
    import render_flow_thin
    from novel_manga.providers.h3_pool import H3Pool, PoolUnavailable
    def pause(started, timeout, message):
        if timeout is not None and time.monotonic()-started >= timeout:
            raise PoolUnavailable(message)
        time.sleep(.1)
    H3Pool._pause=staticmethod(pause)
    def acquire(novel_dir, limit):
        directory=Path(os.environ['NOVEL_INFLIGHT_DIR'])
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            cap=int((directory/'limit').read_text())
            for i in range(cap-1,-1,-1):
                handle=(directory/f'slot_{i:02d}.lock').open('a')
                try:
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    return handle
                except BlockingIOError:
                    handle.close()
            time.sleep(.1)
        raise TimeoutError('pilot shared admission queue exceeded ten minutes')
    render_flow_thin.acquire_inflight_slot=acquire
    manifest=read(output/'manifest.json');frozen=Path(manifest['frozen_novel']);results=read(output/'results.json',[])
    for case in manifest['cases']:
        original=evaluate(output,case,'original',frozen/f"{frozen.name}_{case['episode']}"/'failed.mp4')
        if 'error' in original or evaluation_passed(original):
            print(case['id'],'excluded: original failure did not reproduce',flush=True)
            atomic_write_json(output/'excluded'/f"{case['id']}.json",original)
            continue
        prepared=[]
        for arm in ['A','B']:
            try:
                prepared.append(prepare(output,case,arm))
            except Exception as error:
                atomic_write_json(output/'preparation-errors'/f"{case['id']}-{arm}.json",{'error':str(error)})
                print(case['id'],arm,'PREPARATION FAILED',str(error),flush=True)
        if len(prepared)!=2:
            continue  # an invalid pair cannot consume half of its video budget
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(render_arm,output,p) for p in prepared]
            for p,future in zip(prepared,futures):
                try:
                    rendered=future.result();v=evaluate(output,case,p['arm'],rendered['video'])
                    row={**rendered,'verdict':v,'passed':evaluation_passed(v)}
                except Exception as error:
                    row={**p,'error':str(error),'passed':False}
                results=[r for r in results if (r['case']['id'],r['arm'])!=(case['id'],p['arm'])]+[row]
                atomic_write_json(output/'results.json',results)
                print(case['id'],p['arm'],'PASS' if row['passed'] else 'FAIL',row.get('error') or row['verdict'].get('evidence',''),flush=True)
    paired={c['id'] for c in manifest['cases'] if all(any(r['case']['id']==c['id'] and r['arm']==arm
            and not r.get('error') and not r.get('verdict',{}).get('error') for r in results) for arm in ['A','B'])}
    summary={arm:{'valid_paired_cases':sum(r['arm']==arm and r['case']['id'] in paired for r in results),
                  'passed':sum(r['passed'] for r in results if r['arm']==arm and r['case']['id'] in paired),
                  'paired_requested_seconds':sum(r['request_seconds'] for r in results if r['arm']==arm and r['case']['id'] in paired),
                  'execution_errors':sum(bool(r.get('error') or r.get('verdict',{}).get('error')) for r in results if r['arm']==arm),
                  'paired_prepare_seconds':round(sum(r['prepare_seconds'] for r in results if r['arm']==arm and r['case']['id'] in paired),2)} for arm in ['A','B']}
    summary['coverage']={'frozen_cases':len(manifest['cases']),'valid_pairs':len(paired),
                         'preparation_failures':len(list((output/'preparation-errors').glob('*.json'))),
                         'excluded_originals':len(list((output/'excluded').glob('*.json'))),
                         'actual_submissions':len(list(output.glob('runs/*/*/submission.json')))}
    atomic_write_json(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','run'])
    parser.add_argument('--novel-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',default='')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    os.environ.update(QWEN38_LOCAL_BASE_URL=','.join(f'http://127.0.0.1:{p}/v1' for p in range(18120,18125)),
                      QWEN38_LOCAL_MODEL='Qwen3.8-27B-Project',QWEN38_LOCAL_API_KEY_VAR='H3_PROMPT_NO_KEY',
                      QWEN38_LOCAL_MIN_MAX_TOKENS='4096',NOVEL_INFLIGHT_POOL='h3pool',
                      NOVEL_INFLIGHT_DIR='/mnt/disk1/zengzhitao/tmp/inflight/h3pool',NOVEL_CLIP_SECONDS_MAX='15')
    if args.action=='freeze':
        print(json.dumps(freeze(args.novel_dir.resolve(),args.output.resolve(),args.cases.split(',')),ensure_ascii=False))
    else:
        run(args.output.resolve())


if __name__=='__main__':
    main()
