#!/usr/bin/env python3
"""Render the v2 screenplay as matched sequential comic and hybrid previews.

Image choice is explicit and semantic. Speech determines each scene's duration;
brief written holds are for response/action, never matching the old episode's
length. Subtitle events and the audio master are shared between both variants.
"""
from __future__ import annotations

import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import re
import subprocess
import time
import wave

FPS, WIDTH, HEIGHT, RATE = 25, 1080, 1920, 48000


def run(command):
    r = subprocess.run([str(v) for v in command], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-3000:])
    return r.stdout


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',path]))


def ass_time(seconds):
    cs = round(seconds*100)
    return f'{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}'


def phrases(text, limit=17):
    # A subtitle follows a spoken clause, not an entire paragraph of exposition.
    parts = re.findall(r'[^，。？！；]+[，。？！；]?', text)
    result = []
    for part in parts:
        while len(part)>limit:
            result.append(part[:limit]);part=part[limit:]
        if not part:continue
        if result and len(result[-1]+part)<=limit:result[-1]+=part
        else:result.append(part)
    return [part.strip('，。；') for part in result]


def subtitles(path, scenes, total, width=WIDTH, height=HEIGHT):
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Speech,WenQuanYi Micro Hei,49,&H00F6F5F1,&H00FFFFFF,&H00231B16,&H80000000,-1,0,0,0,100,100,1,0,1,2.5,1,2,85,85,210,1
Style: Inner,WenQuanYi Micro Hei,49,&H00FFE8C4,&H00FFFFFF,&H00231B16,&H80000000,-1,0,0,0,100,100,1,0,1,2.5,1,2,85,85,210,1
Style: Mental,WenQuanYi Micro Hei,49,&H00F4D4DF,&H00FFFFFF,&H00231B16,&H80000000,-1,0,0,0,100,100,1,0,1,2.5,1,2,85,85,210,1
Style: Label,WenQuanYi Micro Hei,30,&H00D8D0C4,&H00FFFFFF,&H00231B16,&H80000000,0,0,0,0,100,100,2,0,1,1.5,0,2,85,85,290,1
Style: Title,WenQuanYi Micro Hei,34,&H00D8D0C4,&H00FFFFFF,&H00231B16,&H80000000,0,0,0,0,100,100,3,0,1,1.5,0,8,70,70,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    landscape=width>height
    if landscape:
        header=header.replace('PlayResX: 1080','PlayResX: '+str(width)).replace('PlayResY: 1920','PlayResY: '+str(height))
        header=header.replace('Hei,49,','Hei,44,').replace('Hei,30,','Hei,25,').replace('Hei,34,','Hei,28,')
        header=header.replace('8,70,70,100,1','8,70,70,45,1')
    events=[]
    def event(start,end,style,text):
        events.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}')
    event(.2,3.4,'Title',r'{\fad(250,350)}雾月秘典 · 将死之人')
    for s in scenes:
        start,end=s['speech_start'],s['speech_end']
        label = {'inner':'莱恩 · 心声','mental':'脑海中的女声','narration':'旁白'}.get(s['mode'],s['speaker'])
        style={'inner':'Inner','mental':'Mental'}.get(s['mode'],'Speech')
        chunks=[s['subtitle'].replace('后的','后的'+r'\N')] if s.get('subtitle') else phrases(s['text'])
        if landscape:label_y=865 if any(r'\N' in c for c in chunks) else 920
        else:label_y=1550 if any(r'\N' in c for c in chunks) else 1620
        event(start,end,'Label',r'{\an2\pos('+str(width//2)+','+str(label_y)+')}' + label)
        weights=[len(re.sub(r'\W','',x))+1 for x in chunks]
        cursor=start
        for text,weight in zip(chunks,weights):
            stop=cursor+(end-start)*weight/sum(weights)
            text_y=985 if landscape else 1700
            event(cursor,stop,style,r'{\an2\pos('+str(width//2)+','+str(text_y)+')}'+text)
            cursor=stop
    path.write_text(header+'\n'.join(events)+'\n')


def prepare(out):
    manifest=json.loads((out/'manifest.json').read_text())
    (out/'processed_audio').mkdir(exist_ok=True)
    began=time.monotonic(); scenes=[];chunks=[]; cursor=0.0
    for source in manifest['scenes']:
        s=dict(source); i=s['id']; dst=out/'processed_audio'/f'{i:02d}.wav'
        speed=.86 if s['mode']=='dialogue' and s['speaker']=='莱恩' else .91
        # Loudness-window normalization severely attenuates these 1–2s lines.
        # Normalize the completed chunk's peak instead, including its voice effect.
        filters=f'highpass=f=65,atempo={speed}'
        if s['mode']=='mental':filters+=',aecho=0.8:0.8:70|140:0.10|0.04'
        elif s['mode']=='inner':filters+=',aecho=0.9:0.9:45:0.035'
        run(['ffmpeg','-v','error','-y','-i',out/'audio'/f'scene_{i:02d}.wav','-af',filters,'-ar',RATE,'-ac',1,'-c:a','pcm_s16le',dst])
        with wave.open(str(dst),'rb') as w:
            samples=array('h');samples.frombytes(w.readframes(w.getnframes()))
        peak=max(abs(v) for v in samples)
        if not peak:raise ValueError(f'Scene {i} contains no audio')
        gain=(32767*10**(-3/20))/peak
        samples=array('h',(round(v*gain) for v in samples))
        with wave.open(str(dst),'wb') as w:
            w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
        lead=.7 if i==1 else (.45 if i==10 else .12)
        hold=s.get('hold',.22)
        raw=lead+len(samples)/RATE+hold
        frames=math.ceil(raw*FPS);duration=frames/FPS
        padded=array('h',[0])*round(duration*RATE)
        first=round(lead*RATE);padded[first:first+len(samples)]=samples
        chunks.append(padded.tobytes())
        s.update(start=cursor,end=cursor+duration,duration=duration,frames=frames,
                 speech_start=cursor+lead,speech_end=cursor+lead+len(samples)/RATE,lead=lead)
        cursor+=duration;scenes.append(s)
    with wave.open(str(out/'voice_dry.wav'),'wb') as w:
        w.setparams((1,2,RATE,0,'NONE','not compressed'))
        for chunk in chunks:w.writeframes(chunk)
    # Extremely quiet continuous room tone bridges edits. Same mixed master for A/B.
    run(['ffmpeg','-v','error','-y','-i',out/'voice_dry.wav','-f','lavfi','-i',f'anoisesrc=color=brown:amplitude=0.003:r={RATE}:d={cursor}',
         '-filter_complex','[1:a]highpass=f=90,lowpass=f=1100[room];[0:a][room]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.92:level=false[a]',
         '-map','[a]','-ac',2,'-c:a','pcm_s16le',out/'voice_master.wav'])
    save(out/'timeline.json',{'duration':cursor,'scenes':scenes})
    save(out/'asr_segments.json',[[s['speech_start'],s['speech_end']] for s in scenes])
    subtitles(out/'subtitles.ass',scenes,cursor)
    save(out/'audio_preparation.json',{'seconds':time.monotonic()-began,'duration':cursor,'original_duration_not_used':True})
    print(f'audio/subtitles prepared: {cursor:.2f}s, {len(scenes)} scenes',flush=True)


def visual_shots(scenes):
    shots=[]
    # Camera positions are tied to the intended subject within the authored image.
    camera={'establish':(1.0,1.035,.5,.30),'listen':(1.08,1.12,.62,.30),
            'room':(1.0,1.04,.47,.35),'pain':(1.02,1.06,.62,.32),
            'resolve':(1.05,1.08,.62,.30),'old':(1.18,1.23,.20,.38),
            'grip':(1.05,1.08,.50,.45)}
    for s in scenes:
        # No artificial live dialogue. Motion is silent reaction or offscreen hands.
        # The provider rejected the language image as a possible real person.
        # Keep that illustrated reaction; do not retry to bypass its image gate.
        motion={5:('orient',0),17:('tighten',0),21:('tighten',2.8)}.get(s['id'])
        shot={'first_scene':s['id'],'last_scene':s['id'],'image':s['image'],'frames':s['frames'],
              'start':s['start'],'duration':s['duration'],'camera':camera[s['image']],'motion':motion}
        # Adjacent lines in one continuous view do not restart their camera move.
        if shots and not motion and not shots[-1]['motion'] and shots[-1]['image']==shot['image']:
            shots[-1]['frames']+=shot['frames'];shots[-1]['duration']+=shot['duration'];shots[-1]['last_scene']=s['id']
        else:shots.append(shot)
    # End on the listener's wary reaction; the letter is still in the future.
    last=shots[-1]; reaction=35
    last['frames']-=reaction;last['duration']=last['frames']/FPS
    shots.append({'first_scene':22,'last_scene':22,'image':'listen','frames':reaction,'duration':reaction/FPS,
                  'start':last['start']+last['duration'],'camera':(1.10,1.10,.62,.30),'motion':None})
    return shots


def encode_shot(out, shot, index, variant, width=WIDTH, height=HEIGHT):
    target=out/'segments'/f'{index:02d}_{variant}.mp4'
    frames=shot['frames'];d=frames/FPS
    if variant=='hybrid' and shot['motion']:
        name,start=shot['motion']; source=out/'motion'/name/'video.mp4'
        # Normal-speed semantic excerpt, with no loop or artificial mouth motion.
        source_duration=float(probe(source)['format']['duration'])
        if start+d>source_duration+.08:raise ValueError(f'{name}: motion source too short ({start}+{d}>{source_duration})')
        inputs=['-ss',start,'-i',source]
        vf=f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={FPS}'
    else:
        inputs=['-i',out/'images'/(shot['image']+'.png')]
        z0,z1,cx,cy=shot['camera']
        zoom=f'{z0}+({z1-z0})*on/{max(1,frames-1)}'
        vf=(f'scale={width*2}:{height*2},zoompan=z=\'{zoom}\':x=\'(iw-iw/zoom)*{cx}\':y=\'(ih-ih/zoom)*{cy}\''
            f':d={frames}:s={width}x{height}:fps={FPS},setsar=1')
    run(['ffmpeg','-v','error','-y','-threads',2,*inputs,'-vf',vf,'-frames:v',frames,'-an',
         '-c:v','libx264','-preset','fast','-crf',20,'-pix_fmt','yuv420p','-threads',2,target])
    return target


def render(out,variant,reuse_segments=False,width=WIDTH,height=HEIGHT):
    started=time.monotonic();timeline=json.loads((out/'timeline.json').read_text());shots=visual_shots(timeline['scenes'])
    if width>height:
        # Wide keyframes already place both characters and the grip in frame.
        for s in shots:
            end=1.025 if s['image'] in ('listen','old','grip') else 1.0
            s['camera']=(1.0,end,s['camera'][2],.45)
    (out/'segments').mkdir(exist_ok=True)
    def work(item):
        i,s=item
        shared=out/'segments'/f'{i:02d}_comic.mp4'
        if variant=='hybrid' and not s['motion'] and shared.is_file() and int(probe(shared)['streams'][0]['nb_frames'])==s['frames']:return shared
        cached=out/'segments'/f'{i:02d}_{variant}.mp4'
        if reuse_segments and cached.is_file() and int(probe(cached)['streams'][0]['nb_frames'])==s['frames']:return cached
        return encode_shot(out,s,i,variant,width,height)
    with ThreadPoolExecutor(max_workers=3) as pool:paths=list(pool.map(work,enumerate(shots,1)))
    listing=out/(variant+'_concat.txt');listing.write_text(''.join("file '"+str(p.resolve())+"'\n" for p in paths))
    target=out/f'wuyue_1_{variant}_v2.mp4'
    # Subtitles are overlaid on a single full-screen scene, with no panel layout.
    run(['ffmpeg','-v','error','-y','-f','concat','-safe',0,'-i',listing,'-i',out/'voice_master.wav',
         '-map','0:v','-map','1:a','-vf',f"ass='{out/'subtitles.ass'}'",'-c:v','libx264','-preset','fast','-crf',20,
         '-threads',4,'-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar',RATE,'-t',timeline['duration'],
         '-movflags','+faststart',target])
    actual=probe(target);v=next(s for s in actual['streams'] if s['codec_type']=='video');a=next(s for s in actual['streams'] if s['codec_type']=='audio')
    passed=(v['width']==width and v['height']==height and v['codec_name']=='h264' and v['r_frame_rate']=='25/1'
            and a['codec_name']=='aac' and abs(float(actual['format']['duration'])-timeline['duration'])<.08)
    run(['ffmpeg','-v','error','-i',target,'-f','null','-'])
    for label,at in [('cover',1.0),('ending',timeline['duration']-.6)]:
        run(['ffmpeg','-v','error','-y','-ss',at,'-i',target,'-frames:v',1,'-q:v',2,out/f'{variant}_{label}.jpeg'])
    report={'variant':variant,'path':str(target.resolve()),'duration':timeline['duration'],'media_passed':passed,
            'render_seconds':time.monotonic()-started,'motion_seconds':sum(s['duration'] for s in shots if s['motion']) if variant=='hybrid' else 0,
            'shots':shots,'source_trace':'content_trace.json','shared_audio':'voice_master.wav','shared_subtitles':'subtitles.ass',
            'quality_status':'preview; requires narrative and viewer review','media':actual}
    save(out/f'{variant}_report.json',report)
    if not passed:raise ValueError('media format gate failed')
    print(variant,'rendered',timeline['duration'],'seconds; motion',report['motion_seconds'],'render wall',report['render_seconds'],flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','comic','hybrid']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-segments',action='store_true',help='Reassemble a frozen run after subtitle-only edits')
    p.add_argument('--landscape',action='store_true',help='1920x1080 output using separately authored landscape images')
    args=p.parse_args()
    if args.command=='prepare':prepare(args.output)
    else:
        width,height=(1920,1080) if args.landscape else (WIDTH,HEIGHT)
        if args.landscape:
            timeline=json.loads((args.output/'timeline.json').read_text())
            subtitles(args.output/'subtitles.ass',timeline['scenes'],timeline['duration'],width,height)
        render(args.output,args.command,args.reuse_segments,width,height)


if __name__=='__main__':main()
