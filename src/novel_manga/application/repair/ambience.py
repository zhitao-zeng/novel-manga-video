"""Adopt a reviewed independent ambience file without changing any video request."""
from pathlib import Path
from novel_manga.util import run
import shutil
import wave
from novel_manga.util import atomic_write_json, read_json


def validate(entry):
    review=entry.get('review')or{}
    asr=review.get('ASR')or{}
    if (review.get('speech_free') is not True or not (review.get('method') or asr.get('backend'))
            or str(asr.get('hypothesis')or'').strip()):
        raise ValueError('ambience needs actual speech-free evidence and review method')
    path=Path(entry['path'])
    with wave.open(str(path),'rb') as stream:
        seconds=stream.getnframes()/stream.getframerate()
    if seconds<3:raise ValueError('ambience audio has fewer than three seconds')
    return path


def adopt(directory,clip,decision):
    catalog=read_json(directory/'repair_candidates.json',{})
    entry=next((a for a in catalog.get('audio',[]) if a['id']==decision.get('candidate_id')),None)
    if entry is None:raise ValueError('audio candidate is not registered')
    source=validate(entry)
    target_kind=decision.get('target') or ('interaction' if (directory/'interaction.json').is_file() else 'clip')
    if target_kind not in {'interaction','clip'}:raise ValueError('audio target must be interaction or clip')
    if target_kind=='interaction' and not (directory/'interaction.json').is_file():
        raise ValueError('interaction audio requires an existing interaction card')
    if target_kind=='clip':_silent_clip(directory,clip)
    policy=read_json(directory/'delivery_policy.json',{})
    target=directory/'work/ambience-assets'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()!=target.resolve():shutil.copy2(source,target)
    value={**entry,'path':str(target.resolve()),'source_path':str(source.resolve())}
    previous=policy.get('interaction_ambience') if target_kind=='interaction' else (policy.get('clip_ambience')or{}).get(clip['clip_id'])
    if previous==value:raise ValueError('ambience candidate already adopted')
    if target_kind=='interaction':policy['interaction_ambience']=value
    else:policy.setdefault('clip_ambience',{})[clip['clip_id']]=value
    atomic_write_json(directory/'delivery_policy.json',policy)
    return {'assembly_changed':True,'audio_asset':value,'changed':[]}


def apply_assembly(directory,report,*,context=None):
    """Replace only authored silent clips in a novel assembly; copy video unchanged."""
    import copy
    from novel_manga.review.storage import take_identity
    policy=read_json(directory/'delivery_policy.json',{}).get('clip_ambience')or{}
    if not policy:return report
    assembly=report.get('assembly')or{}
    if not assembly.get('final_video') or not Path(assembly['final_video']).is_file():return report
    previous=assembly.get('audio_repair')or{}
    source=Path(previous.get('source')or assembly.get('final_video',''))
    signature={'source':str(source),'source_take':take_identity(source),'policy':policy,
               'assets':{key:take_identity(validate(asset)) for key,asset in policy.items()},
               'timeline':assembly.get('timeline',[])}
    if previous.get('inputs')==signature and Path(assembly.get('final_video','')).is_file():
        if context is not None and not previous.get('qc_checked'):
            result=copy.deepcopy(report);_refresh_checks(context,result['assembly']);return result
        return report
    plan=read_json(directory/'clip_plan.json',{})
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    spans={s['unit_id']:s for s in assembly.get('timeline',[])}
    graph=[];command=['ffmpeg','-y','-v','error','-threads','4','-i',str(source)];base='0:a';labels=[]
    for number,(cid,asset) in enumerate(policy.items(),1):
        if cid not in clips or cid not in spans:raise ValueError('ambience target missing from assembly')
        _silent_clip(directory,clips[cid])
        start,end=float(spans[cid]['start']),float(spans[cid]['end'])
        if end<=start:raise ValueError('ambience target has no actual duration')
        path=validate(asset);command+=['-stream_loop','-1','-i',str(path)]
        graph.append(f"[{base}]volume=0:enable='between(t,{start},{end})'[base{number}]")
        base=f'base{number}'
        graph.append(f'[{number}:a]atrim=duration={end-start},asetpts=PTS-STARTPTS,aresample=48000,adelay={round(start*1000)}:all=1[room{number}]')
        labels.append(f'[room{number}]')
    graph.append(f'[{base}]'+''.join(labels)+f'amix=inputs={len(labels)+1}:duration=first:normalize=0[audio]')
    # Retain an immutable pre-edit movie for restart/cache and normal controlled publication.
    folder=directory/'repair_candidate';folder.mkdir(parents=True,exist_ok=True)
    version=len(list(folder.glob('audio-assembly-*.mp4')))+1
    if not previous:
        saved=folder/f'audio-source-{version:03d}.mp4';shutil.copy2(source,saved)
        source=saved;command[command.index('-i')+1]=str(source)
        signature.update(source=str(source),source_take=take_identity(source))
    output=folder/f'audio-assembly-{version:03d}.mp4'
    command+=['-filter_complex',';'.join(graph),'-map','0:v:0','-map','[audio]','-c:v','copy','-c:a','aac','-b:a','192k',str(output)]
    run(command)
    result=copy.deepcopy(report);result['assembly'].update(final_video=str(output),
        pending_publish=True,thin_passed=False,media_qc_passed=False,
        media_qc={'passed':False,'checks':{},'reason':'Changed audio requires current media checks'},
        audio_repair={'source':str(source),'inputs':signature,'video_reencoded':False,'qc_checked':False})
    if context is not None:_refresh_checks(context,result['assembly'])
    return result


def _refresh_checks(context,assembly):
    from novel_manga.media.quality import inspect_media
    from novel_manga.application.profiles import media_qc_ignores,MAX_HOLD_SECONDS
    output=Path(assembly['final_video'])
    required=any(c.get('spoken_text') or c.get('lines') for c in context.clip_plan.get('clips',[]))
    required|=any(t.get('text') and t.get('delivery_mode') in {'visible_dialogue','offscreen_dialogue','singing'}
                  for shot in context.script.get('shots',[]) for t in shot.get('turns',[]))
    qc=inspect_media(output,Path(assembly.get('cover','')),Path(assembly.get('ending','')),
        Path(assembly.get('ass','')),context.settings,output.with_suffix('.media-qc.json'),
        silent_outro_seconds=assembly.get('silent_outro_seconds',0),
        ignore_checks=tuple(media_qc_ignores(context.novel_dir,context.episode_dir)),
        subtitles_required=bool(required or assembly.get('subtitle_events')))
    freeze=float(qc.get('checks',{}).get('long_freeze',{}).get('detail',{}).get('max_freeze_seconds',0))
    other=[row.get('passed') for key,row in qc.get('checks',{}).items() if key!='long_freeze']
    assembly.update(media_qc=qc,media_qc_passed=qc.get('passed') is True,max_hold_seconds=freeze,
                    thin_passed=bool(other) and all(other) and freeze<=MAX_HOLD_SECONDS)
    assembly['audio_repair']['qc_checked']=True


def _silent_clip(directory,clip):
    if any(str(row.get('text')or'').strip() and row.get('delivery_mode')!='silent_action' for row in clip.get('lines',[])):
        raise ValueError('ambience cannot replace a clip with bound dialogue')
    script=read_json(directory/'chapter_script.json',{})
    shots=[s for i,s in enumerate(script.get('shots',[]),1) if s.get('index',i) in clip.get('shot_indexes',[])]
    if not shots or any(any(t.get('delivery_mode')!='silent_action' for t in s.get('turns',[])) for s in shots):
        raise ValueError('clip ambience requires source silent_action and no dialogue')
