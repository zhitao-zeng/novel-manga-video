#!/usr/bin/env python3
"""Prepare and render paired comic/hybrid previews from an issue-free existing episode.

This first pilot measures new speech and local composition. Approved source video
provides frozen scene images and key-video excerpts; their original generation
cost is reused, not claimed as zero-cost new generation.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-2400:])
    return result.stdout


def duration(path):
    return float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
                      'default=noprint_wrappers=1:nokey=1', str(path)]).strip())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def require_clean(review, media):
    selected = {c['clip_id']: c.get('selected') for c in media['clips']}
    if review.get('flags') or not selected or set(review['clips']) != set(selected):
        raise ValueError('Source must have a complete review with no issue flags')
    if not media['assembly']['thin_passed'] or media.get('failed_clips') or media.get('gate_failed_clips'):
        raise ValueError('Source must pass media and speech gates')
    for cid, row in review['clips'].items():
        good = row.get('severity') == 'pass'
        good &= all(row.get(k) is True for k in ('identity_ok', 'location_ok', 'time_of_day_ok', 'chat_text_ok'))
        good &= all(row.get(k) is False for k in ('text_or_watermark', 'visual_defects'))
        good &= bool(selected[cid] and selected[cid].get('passed') and row.get('video') == selected[cid]['video'])
        if not good:
            raise ValueError(f'Source clip {cid} has an issue or a stale review')


def shot_weight(shot):
    # Used only to locate a representative frame inside an existing clip.
    return 1 + sum(max(2.5, len(t.get('text', '')) / 4) for t in shot.get('turns', []))


def prepare(config, output):
    started = time.monotonic()
    source = Path(config['source_episode'])
    media = json.loads((source / 'thin_media_report.json').read_text())
    review = json.loads((source / 'episode_review.json').read_text())
    require_clean(review, media)
    script = json.loads((source / 'chapter_script.json').read_text())
    plan = json.loads((source / 'clip_plan.json').read_text())
    frozen = output / 'source'
    frozen.mkdir(parents=True, exist_ok=True)
    for name in ('chapter_script.json', 'clip_plan.json', 'episode_review.json', 'thin_media_report.json'):
        shutil.copy2(source / name, frozen / name)
    shots = script['shots']
    selected = {c['clip_id']: c['selected'] for c in media['clips']}
    windows = {}
    for clip in plan['clips']:
        if clip['kind'] != 'video':
            continue
        chosen = selected[clip['clip_id']]
        original = Path(chosen['video'])
        video = frozen / f"{clip['clip_id']}.mp4"
        shutil.copy2(original, video)
        indexes = clip['shot_indexes']
        weights = [shot_weight(shots[i-1]) for i in indexes]
        total = sum(weights)
        cursor = 0.0
        for index, weight in zip(indexes, weights):
            start = chosen['duration'] * cursor / total
            span = chosen['duration'] * weight / total
            windows[index] = (video, start, span, chosen['duration'])
            cursor += weight
    scenes = []
    for index, shot in enumerate(shots, 1):
        spoken = [t for t in shot['turns'] if t['delivery_mode'] in ('visible_dialogue', 'offscreen_dialogue')]
        if spoken:
            speakers = {t['speaker_name'] for t in spoken}
            if len(speakers) != 1:
                raise ValueError(f'Scene {index} needs to be split at speaker changes')
            speaker = spoken[0]['speaker_name']
            text = ''.join(t['text'] for t in spoken)
        else:
            speaker = '旁白'
            text = config.get('narration', {}).get(str(index))
            if not text:
                raise ValueError(f'Scene {index} requires a source-grounded narration line')
        voice = Path(config['voice_library']) / config['voices'][speaker]
        voice_snapshot = frozen / 'voices' / voice.name
        voice_snapshot.parent.mkdir(exist_ok=True)
        if not voice_snapshot.exists():
            shutil.copy2(voice, voice_snapshot)
        video, start, span, video_seconds = windows[index]
        frame = frozen / f'scene_{index:02d}.jpeg'
        run(['ffmpeg', '-y', '-v', 'error', '-ss', str(min(start+span/2, video_seconds-.2)),
             '-i', str(video), '-frames:v', '1', str(frame)])
        scenes.append({'id': index, 'speaker': speaker, 'text': text, 'voice_reference': str(voice_snapshot),
                       'source_segment_id': shot['segment_id'], 'source_quote': shot['source_quote'],
                       'event': shot['motion_prompt'], 'image': str(frame), 'video': str(video),
                       'video_start': start, 'video_span': span, 'video_seconds': video_seconds,
                       'use_key_video': index in config['hybrid_shots']})
    manifest = {**config, 'source_duration_seconds': media['assembly']['duration'],
                'source_render_seconds_historical': media.get('elapsed_seconds'), 'scenes': scenes,
                'source_review': {'flags': review['flags'], 'severities': {k:v['severity'] for k,v in review['clips'].items()}},
                'preparation_seconds': time.monotonic()-started,
                'cost_scope': 'Reused approved source frames and key clips; new TTS and local composition only. No fresh image/video API generation. Not a full novel-to-video throughput measurement.'}
    save(output / 'manifest.json', manifest)
    save(output / 'content_trace.json', {'source_episode': str(source), 'scenes': [{k:s[k] for k in ('id','speaker','text','source_segment_id','source_quote','event')} for s in scenes]})
    print(f"Prepared {len(scenes)} scenes from a fully passing source in {manifest['preparation_seconds']:.1f}s", flush=True)


def wrap(text, draw, font, width):
    lines, line = [], ''
    for char in text:
        if line and draw.textlength(line+char, font=font) > width:
            lines.append(line)
            line = ''
        line += char
    if line:
        lines.append(line)
    return lines


def page(manifest, scene, target):
    image = Image.open(scene['image']).convert('RGB')
    canvas = Image.new('RGB', (1080,1920), '#141b20')
    draw = ImageDraw.Draw(canvas)
    small = ImageFont.truetype(manifest['font'], 28)
    heading = ImageFont.truetype(manifest['font'], 52)
    body = ImageFont.truetype(manifest['font'], 51)
    draw.text((48,38), manifest['novel_title'], font=small, fill='#cdb792')
    draw.text((46,92), '第一话 · '+manifest['episode_title'], font=heading, fill='#f3e6d0')
    draw.line((48,177,1032,177), fill='#957c57', width=3)
    # A main panel and two reaction/detail panels retain a comic-page composition.
    canvas.paste(ImageOps.fit(image,(1000,650)),(40,215))
    w,h = image.size
    for x, box in ((40,(0,0,w*.57,h)),(554,(w*.43,0,w,h))):
        canvas.paste(ImageOps.fit(image.crop(box),(486,395)),(x,901))
        draw.rectangle((x,901,x+486,1296),outline='#d0bd9b',width=3)
    draw.rectangle((38,213,1042,867),outline='#d0bd9b',width=4)
    draw.rounded_rectangle((40,1340,1040,1835),radius=24,fill='#253139',outline='#6a685c',width=2)
    draw.text((76,1373), scene['speaker'],font=heading,fill='#dfbd7a')
    lines = wrap(scene['text'],draw,body,924)
    if len(lines)>5:
        raise ValueError(f"Scene {scene['id']} has too much caption text for one page")
    for i,line in enumerate(lines):draw.text((77,1450+i*67),line,font=body,fill='#f6f0e7')
    draw.text((48,1870),f"{scene['id']:02d} / {len(manifest['scenes']):02d}",font=small,fill='#a9a397')
    canvas.save(target)


def render_scene(scene, seconds, page_path, output, video=False):
    frames = round(seconds*25)
    command = ['ffmpeg','-y','-v','error','-filter_complex_threads','1','-loop','1','-framerate','25','-i',str(page_path)]
    if video:
        start = min(scene['video_start'],max(0,scene['video_seconds']-seconds))
        command += ['-ss',str(start),'-i',scene['video']]
        moving = f'[1:v]scale=1000:650:force_original_aspect_ratio=increase,crop=1000:650,setsar=1,fps=25,tpad=stop_mode=clone:stop_duration={seconds}[panel];'
    else:
        command += ['-i',scene['image']]
        moving = f"[1:v]scale=1200:780:force_original_aspect_ratio=increase,crop=1200:780,zoompan=z='1+0.035*on/{max(1,frames-1)}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s=1000x650:fps=25,setsar=1[panel];"
    filters = moving+'[0:v][panel]overlay=40:215:shortest=1,format=yuv420p[v]'
    command += ['-filter_complex',filters,'-map','[v]','-an','-t',str(seconds),'-r','25',
                '-c:v','libx264','-preset','veryfast','-crf','21','-threads','4',str(output)]
    run(command)


def scene_durations(manifest, audio):
    """Keep the paired cuts near the reference length without shortening speech."""
    scenes=manifest['scenes']
    base=[math.ceil(max(3.5,audio[s['id']]['audio_seconds']+.65)*25) for s in scenes]
    target=max(sum(base),round(manifest['source_duration_seconds']*25))
    extra=target-sum(base)
    weights=[max(.1,s['video_span']-frames/25) for s,frames in zip(scenes,base)]
    shares=[extra*w/sum(weights) for w in weights]
    allocated=[math.floor(v) for v in shares]
    order=sorted(range(len(scenes)),key=lambda i:shares[i]-allocated[i],reverse=True)
    for i in order[:extra-sum(allocated)]:allocated[i]+=1
    return {s['id']:(frames+addition)/25 for s,frames,addition in zip(scenes,base,allocated)}


def render(output):
    started=time.monotonic()
    manifest=json.loads((output/'manifest.json').read_text())
    speech=json.loads((output/'tts_report.json').read_text())
    audio={r['scene']:r for r in speech['rows']}
    if len(audio)!=len(manifest['scenes']):
        raise ValueError('Speech synthesis is incomplete')
    durations=scene_durations(manifest,audio)
    pages=output/'pages';pages.mkdir(exist_ok=True)
    segments=output/'segments';segments.mkdir(exist_ok=True)
    timeline=[];comic=[];hybrid=[];elapsed_by_mode={'comic':0.0,'hybrid_extra':0.0};cursor=0.0
    for scene in manifest['scenes']:
        sid=scene['id'];row=audio[sid]
        seconds=durations[sid]
        page_path=pages/f'{sid:02d}.png';page(manifest,scene,page_path)
        audio_out=segments/f'{sid:02d}.wav'
        run(['ffmpeg','-y','-v','error','-i',row['path'],'-af',f'adelay=250:all=1,apad,atrim=duration={seconds}',
             '-ar','48000','-ac','2','-c:a','pcm_s16le',str(audio_out)])
        path=segments/f'{sid:02d}_comic.mp4';before=time.monotonic();render_scene(scene,seconds,page_path,path)
        elapsed_by_mode['comic']+=time.monotonic()-before;comic.append(path)
        if scene['use_key_video']:
            key=segments/f'{sid:02d}_hybrid.mp4';before=time.monotonic();render_scene(scene,seconds,page_path,key,video=True)
            elapsed_by_mode['hybrid_extra']+=time.monotonic()-before;hybrid.append(key)
        else:hybrid.append(path)
        timeline.append({'scene':sid,'start':cursor,'duration':seconds,'audio':str(audio_out),'speaker':scene['speaker'],
                         'text':scene['text'],'hybrid_motion':scene['use_key_video']})
        cursor+=seconds
        print(f"scene {sid:02d}: {seconds:.2f}s, key-video={scene['use_key_video']}",flush=True)
    audio_list=output/'audio_concat.txt'
    audio_list.write_text(''.join("file '"+r['audio'].replace("'","'\\''")+"'\n" for r in timeline))
    master=output/'voice_master.wav'
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(audio_list),'-af','loudnorm=I=-16:TP=-1.5:LRA=11',
         '-ar','48000','-ac','2','-c:a','pcm_s16le',str(master)])
    variants={}
    for name,paths in [('comic',comic),('hybrid',hybrid)]:
        listing=output/f'{name}_concat.txt';listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in paths))
        target=output/f"{Path(manifest['source_episode']).name}_{name}.mp4"
        run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(listing),'-i',str(master),
             '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart','-shortest',str(target)])
        probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(target)]))
        v=next(s for s in probe['streams'] if s['codec_type']=='video');a=next(s for s in probe['streams'] if s['codec_type']=='audio')
        actual=float(probe['format']['duration'])
        passed=v['width']==1080 and v['height']==1920 and v['r_frame_rate']=='25/1' and v['codec_name']=='h264' and a['codec_name']=='aac' and abs(actual-cursor)<.15
        variants[name]={'video':str(target),'duration_seconds':actual,'format_passed':passed,'video_generated_seconds':0,
                        'reused_motion_seconds':sum(r['duration'] for r in timeline if r['hybrid_motion']) if name=='hybrid' else 0}
        save(output/f'{name}_media.json',probe)
    video_id=Path(manifest['source_episode']).name
    Image.open(pages/'01.png').convert('RGB').save(output/f'{video_id}_cover.jpeg',quality=92)
    Image.open(pages/f"{len(timeline):02d}.png").convert('RGB').save(output/f'{video_id}_ending.jpeg',quality=92)
    report={'status':'preview','source_review':manifest['source_review'],'variants':variants,'same_voice_master':str(master),
            'scenes':len(timeline),'timeline_seconds':cursor,'tts_seconds':speech['elapsed_seconds'],
            'tts_load_seconds':speech['load_seconds'],'local_render_seconds':time.monotonic()-started,
            'composition_seconds':elapsed_by_mode,'preparation_seconds':manifest['preparation_seconds'],
            'new_image_api_calls':0,'new_video_api_calls':0,'cost_scope':manifest['cost_scope'],
            'limitations':['Source scene images and key video are reused from a passing existing episode; no fresh-image/video production throughput claim.',
                           'New TTS and captions are shared by both versions; source video lip motion is not retimed to the new voice.',
                           'Format validation does not establish full semantic or visual quality.']}
    save(output/'timeline.json',timeline);save(output/'pilot_report.json',report)
    if not all(v['format_passed'] for v in variants.values()):raise ValueError('Output format check failed')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('prepare','render'))
    parser.add_argument('--config',type=Path,default=Path('configs/comic-pilot.wuyue1.json'))
    args=parser.parse_args();config=json.loads(args.config.read_text());output=Path(config['output']).resolve();output.mkdir(parents=True,exist_ok=True)
    prepare(config,output) if args.stage=='prepare' else render(output)


if __name__=='__main__':main()
