"""Observe assembled movies and return current failures to the existing repair feedback."""
from __future__ import annotations
import copy
import json
import os
import subprocess
import time
from pathlib import Path

from novel_manga.llm import client as model
from novel_manga.llm.config import ENDPOINTS, endpoint_settings
from novel_manga.media.speech_repair import merge_note
from novel_manga.review.contracts import MAX_IMAGES
from novel_manga.util import atomic_write_json, read_json

from .movie_context import POLICY, context


def current(directory, media, final):
    from . import independent
    return bool(isinstance(final,dict) and final.get('movie') and final.get('policy')==POLICY
                and final.get('context')==context(directory,media,final.get('movie',''))
                and independent.current(directory,final))


def apply_feedback(directory, review, final):
    """Keep native verdicts; final failures have their own evidence and feedback channel."""
    review=copy.deepcopy(review)
    for cid,note in review.pop('final_cut_feedback',{}).items():
        old=review.get('native_feedback_before_final',{}).get(cid,'')
        if review.get('feedback',{}).get(cid)==merge_note(old,note):
            if old:review['feedback'][cid]=old
            else:review['feedback'].pop(cid,None)
    review.pop('native_feedback_before_final',None)
    for row in review.get('clips',{}).values():row.pop('final_cut',None)
    review['native_feedback_before_final']=copy.deepcopy(review.get('feedback',{}))
    review['final_cut_feedback']={}
    if not final:return review
    review['final_cut_review']={k:final.get(k) for k in ('movie','context','complete','passed','errors')}
    if final.get('complete') is not True:return review
    grouped={}
    for issue in final.get('issues',[]):
        for cid in issue['clip_ids']:grouped.setdefault(cid,[]).append(issue)
    for cid,issues in grouped.items():
        if cid not in review.get('clips',{}):continue
        note='全片终审：'+'；'.join(f"{i['problem']}（依据：{i['source_basis']}；观察：{i['evidence']}）" for i in issues)
        review['final_cut_feedback'][cid]=note
        review.setdefault('feedback',{})[cid]=merge_note(review['feedback'].get(cid,''),note)
        frame_paths={p for i in issues for p in i.get('frame_paths',[])}
        review['clips'][cid]['final_cut']={
            'movie':final['movie'],'movie_identity':final.get('context',{}).get('movie'),
            'issues':issues,'frames':[f for f in final.get('frames',[]) if f['path'] in frame_paths],
            'scope':'delivered-movie evidence; global seconds; preserve current native take'}
    return review


def caption_cues(path):
    if not path or not Path(path).is_file():return []
    def seconds(text):
        hours,minutes,last=text.split(':');return int(hours)*3600+int(minutes)*60+float(last)
    result=[]
    for line in Path(path).read_text().splitlines():
        if not line.startswith('Dialogue:'):continue
        row=line.split(',',9)
        if len(row)==10:result.append({'start':seconds(row[1]),'end':seconds(row[2]),
                                      'text':row[9].replace('\\N','\n')})
    return result


def validate_group(answer, selected):
    if (not isinstance(answer.get('passed'),bool) or not isinstance(answer.get('observations'),list)
            or not answer['observations'] or not isinstance(answer.get('issues'),list)
            or answer['passed']!= (not answer['issues'])):
        raise ValueError('final reviewer omitted observations or returned conflicting passage')
    allowed={r['clip_id'] for r in selected}
    for issue in answer['issues']:
        if (not issue.get('clip_ids') or not set(issue['clip_ids'])<=allowed
                or not issue.get('frame_numbers') or not issue.get('problem')
                or not issue.get('evidence') or not issue.get('source_basis')):
            raise ValueError('final reviewer did not bind the issue to visible clips/source evidence')
        if any(not isinstance(n,int) or isinstance(n,bool) or not 1<=n<=len(selected)
               for n in issue['frame_numbers']):
            raise ValueError('final reviewer cited an unavailable frame')
        cited={selected[n-1]['clip_id'] for n in issue['frame_numbers']}
        if not set(issue['clip_ids'])<=cited:
            raise ValueError('final reviewer clip has no cited visible evidence')


def ambience_speech_issues(media, receipt, frames):
    """Sentence ASR proves speech overlap; it does not prove subjective sound quality."""
    source=(receipt or {}).get('ambience_source')
    if not source:return []
    spans={r['unit_id']:r for r in (media.get('assembly') or {}).get('timeline',[])}
    row=next((r for r in media.get('clips',[]) if r['clip_id']==source['clip_id']),None)
    span=spans.get(source['clip_id'])
    if not row or not span:return []
    overlaps=[]
    for chunk in (row.get('selected') or {}).get('chunks',[]):
        text=chunk.get('hypothesis') or chunk.get('raw_hypothesis') or ''
        start=float(span['start'])+float(chunk['start']);end=float(span['start'])+float(chunk['end'])
        overlap=min(end,source['end'])-max(start,source['start'])
        if text.strip() and overlap>0:
            overlaps.append({'text':text,'start':start,'end':end,'overlap':round(overlap,3)})
    if not overlaps:return []
    cid=source['clip_id']
    return [{'clip_ids':[cid],'problem':'互动卡环境音来源窗口包含口播',
             'source_basis':'互动卡指定 silent_action 镜头的三秒环境音；该窗口应无说话',
             'evidence':json.dumps({'ambience_source':source,'ASR_overlap':overlaps},ensure_ascii=False),
             'frame_paths':[f['path'] for f in frames if f['clip_id']==cid],
             'audio_evidence':'existing selected-take sentence ASR timestamps; no subjective listening'}]


def review_assembly(runner, media, *, receipt=None):
    """Review the actual assembly, or the delivered movie supplied by its exporter."""
    ctx=runner.context;directory=ctx.episode_dir
    source=(receipt or {}).get('final_video') or (media.get('assembly') or {}).get('final_video')
    if not source:return None  # first-pass production has not assembled a movie
    movie=Path(source);root=directory/'work/final_cut_review';root.mkdir(parents=True,exist_ok=True)
    (root/'owner').write_text('existing reviewed pipeline: final movie observations; no manual shot repair\n')
    before=context(directory,media,movie)
    cached=read_json(directory/'final_cut_review.json',{})
    from . import independent
    if (cached.get('context')==before and cached.get('policy')==POLICY
            and cached.get('automatic_review',cached).get('complete') is True):
        cached=independent.combine(directory,cached)
        atomic_write_json(directory/'final_cut_review.json',cached)
        return cached
    target=root/f'review-{len(list(root.glob("review-*")))+1:03d}';target.mkdir()
    final={'policy':POLICY,'movie':str(movie),'context':before,'complete':False,'passed':False,
           'frames':[],'groups':[],'responses':[],'issues':[],'errors':[],
           'scope':'Actual movie visual/caption continuity; ASR evidence where supplied, no subjective audio listening'}
    def save():
        atomic_write_json(directory/'final_cut_review.json',final)
        atomic_write_json(target/'review.json',final)
    if not movie.is_file():
        final['errors']=['assembled movie unavailable'];save();return final
    try:
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(movie)],text=True))
        duration=float(info['format']['duration'])
        clips={c['clip_id']:c for c in ctx.clip_plan['clips'] if c.get('kind')=='video'}
        spans=[r for r in (media.get('assembly') or {}).get('timeline',[]) if r.get('unit_id') in clips]
        if set(r['unit_id'] for r in spans)!=set(clips):raise ValueError('assembly does not cover all planned video clips')
        def frame(cid,second,label):
            second=min(max(0,second),duration-.08);path=target/f'frame-{len(final["frames"]):03d}.jpg'
            subprocess.run(['ffmpeg','-y','-v','error','-ss',str(second),'-i',str(movie),'-frames:v','1',
                            '-vf','scale=432:768:force_original_aspect_ratio=decrease',str(path)],check=True)
            final['frames'].append({'clip_id':cid,'seconds':round(second,3),'label':label,'path':str(path),
                                    'shot_indexes':clips[cid].get('shot_indexes',[])})
        for span in spans:
            start,end=float(span['start']),float(span['end'])
            for label,t in [('start',start+.15),('middle',(start+end)/2),('end',end-.20)]:frame(span['unit_id'],t,label)
        interaction=(receipt or {}).get('interaction') or {}
        if interaction.get('ambience_shot_index'):
            cid=next((cid for cid,c in clips.items() if interaction['ambience_shot_index'] in c.get('shot_indexes',[])),None)
            if cid:frame(cid,duration-1.5,'interaction card; source clip supplies its ambience')
        final['issues'].extend(ambience_speech_issues(media,receipt,final['frames']))
        cues=caption_cues((Path(movie).parent/'subtitles.ass') if receipt else (media.get('assembly') or {}).get('ass'))
        for row in final['frames']:
            row['expected_captions']=[c['text'] for c in cues if c['start']<=row['seconds']<c['end']]
            row['approved_labels']=[e['text'] for e in (receipt or {}).get('evidence_events',[])
                                    if e['start']<=row['seconds']<e['end']]
        bible=ctx.bible.model_dump(mode='json') if hasattr(ctx.bible,'model_dump') else {}
        source_context={'bible':bible,'script':ctx.script,'layout':before['layout'],'interaction':interaction,
                        'allowed_overlays':(receipt or {}).get('evidence_events',[]),
                        'caption_cues':cues,'postprocessing':(receipt or {}).get('packaging',{}),
                        'caption_origin':'approved ASS added during assembly/postproduction; global movie time',
                        'delivery_policy':(receipt or {}).get('delivery_policy',{})}
        endpoint=os.environ.get('SECOND_REVIEW_JUDGE','').strip()
        settings=endpoint_settings(endpoint) if endpoint in ENDPOINTS else None
        for group,start in enumerate(range(0,len(final['frames']),MAX_IMAGES-2),1):
            selected=final['frames'][start:start+MAX_IMAGES];ids=sorted({r['clip_id'] for r in selected})
            current_requests=[{k:clips[cid].get(k) for k in ('clip_id','shot_indexes','references')}
                              for cid in ids]
            schema=model.obj({'passed':{'type':'boolean'},'observations':{'type':'array','minItems':1,'items':{'type':'string'}},
                'issues':{'type':'array','items':model.obj({
                    'clip_ids':{'type':'array','minItems':1,'items':{'type':'string','enum':ids}},
                    'frame_numbers':{'type':'array','minItems':1,'items':{'type':'integer','minimum':1,'maximum':len(selected)}},
                    'problem':{'type':'string'},'evidence':{'type':'string'},'source_basis':{'type':'string'}})}})
            parts=[]
            for n,row in enumerate(selected,1):
                parts.extend([{'type':'text','text':f"frame {n}: {row['clip_id']} global {row['seconds']}s {row['label']}; source shots {row['shot_indexes']}; expected approved captions {row['expected_captions']}; approved labels {row['approved_labels']}"},
                              model.image_part(Path(row['path']),768)])
            parts.append({'type':'text','text':
                '审核真正组装后的影片，逐张写实际观察，再对照批准源文、台词、人物与机位约束判定。只指认给出的帧/clip，不猜未显示的画面。'
                '重点看目标与画面是否自相矛盾、动作因果、同一空间/服装/道具/摄影机的跨镜连续性、字幕和互动结尾。'
                '原稿若明确单人单手机第一人称，就不能从外部拍到正在录像的同一手机；若没有该限制，合理第三人称镜头允许。'
                '大空间、倒影、正常摇晃、剪近景、湿衣/光照轻微色差、遮挡及作者明确的异象不自动算错。'
                '身体左右、画面左右、自拍镜像要分清；俯拍不自动等于垂直90度。衣鞋款式、凭空设备、出口数量变化要有可见证据。'
                '批准字幕/标记/水印允许，不能当作ghost text；但同一句出现原生和正式两层应报告。'
                '这是后期成片，生成阶段不要字幕/水印的约束不适用于已批准的后期层。单层吻合expected_captions的字幕完全合法，'
                '不能仅因成片有字就推断是原生生成字幕；只有可见重复层、未批准额外文字或与cue实际不符才报告。'
                '字幕按当前口播分段，不能因为某抽帧在字幕间隙或没显示整句话就判漏句；提供了完整cue，先核对它。'
                '引用可见frame_numbers和实际clip_ids，source_basis写原文/明确源约束，不把推断写成事实。拿不准的问题不要虚构。'
                'issues.clip_ids只列实际违反批准源文、需要修正的坏镜头。正确对照镜头可放frame_numbers作比较，不能一并派发重拍。'
                '这是抽帧，不得声称听过音轨。生成请求可能本身有错，批准源文/明确设定是叙事依据，不能把错误翻译当成允许偏差。'
                '来源：'+json.dumps(source_context,ensure_ascii=False)+'\nclip与参考映射（生成请求不作为成片验收依据）：'+json.dumps(current_requests,ensure_ascii=False)})
            deadline=time.monotonic()+300
            for revision in range(2):
                answer=model.ask_json(parts,schema,name='final_movie_continuity',max_tokens=2600,
                                      timeout=max(1,deadline-time.monotonic()),settings=settings)
                response={'group':group,'revision':revision+1,'answer':answer}
                final['responses'].append(response)
                try:
                    validate_group(answer,selected)
                    break
                except ValueError as error:
                    response['protocol_error']=str(error);save()
                    if revision==1 or time.monotonic()>=deadline:raise
                    parts.append({'type':'text','text':'上一份完整回复：'+json.dumps(answer,ensure_ascii=False)+
                        '\n格式校验失败：'+str(error)+'。保留有依据的实际观察与问题，纠正响应协议。'
                        'observations须非空；passed须严格等于issues为空；所有clip_ids必须来自本组并有对应所引用帧。'
                        '若真有问题请passed=false并写issues，不得为了通过协议删除问题或凭空补证据。'})
            for issue in answer['issues']:
                issue['frame_paths']=[selected[n-1]['path'] for n in issue['frame_numbers']]
            final['groups'].append(answer);final['issues'].extend(answer['issues']);save()
        if before!=context(directory,media,movie):raise ValueError('movie or episode inputs changed during final review')
        final.update(complete=True,passed=not final['issues'])
    except Exception as error:
        final['errors'].append(type(error).__name__+': '+str(error)[:250])
    final=independent.combine(directory,final)
    save();return final
