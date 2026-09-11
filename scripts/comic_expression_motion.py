#!/usr/bin/env python3
"""Generate three silent, bounded motion inserts for the v2 comic experiment.

Uses the project's existing video endpoint. Saves task IDs before polling so a
rendering restart never silently buys the same clip again. No production state.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import time

import httpx

from benchmark_seedance_speed import download, sanitized, task_data
from build_bible_thin import load_dotenv
from novel_manga.util import atomic_write_json


PROMPTS = {
    'orient': ('establish', '青年突然身处陌生卧室，短暂茫然，轻微环顾煤气灯，再低头看向床上的病人。病人全程闭嘴，目光盯着青年，右手一直握住青年的右腕。青年也全程闭嘴，没有台词。仅做眼神、呼吸和细微转头，不伸出另一只手，不挣脱。'),
    'language': ('pain', '语言知识涌入脑海。青年闭眼短促皱眉，左手按住左侧太阳穴，痛苦只持续一瞬，随后缓慢睁开眼，恢复清醒。嘴唇始终闭合，不说话，不尖叫。右臂仍向下伸出且手腕由病人握住，不抬起右手。无文字、符号或闪电。'),
    'tighten': ('grip', '全程仅有手腕特写，不出现任何面孔。病人的枯瘦右手缓慢收紧，攥住青年右手腕，青年手指紧张地动一下，轻轻试着向右抽手，但没有挣脱，动作幅度很小。病人始终攥着同一个手腕，不变成握手，不交叉手指，不出现额外手。'),
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--case', choices=PROMPTS, required=True)
    p.add_argument('--ratio', choices=['9:16','16:9'], default='9:16')
    args = p.parse_args()
    load_dotenv(args.env_file)
    target = args.output / 'motion' / args.case
    target.mkdir(parents=True, exist_ok=True)
    record = target / 'result.json'
    row = json.loads(record.read_text()) if record.is_file() else {'case': args.case}
    if row.get('status') in ('completed', 'rejected', 'failed', 'submission_unknown'):
        print(args.case, row['status'], flush=True)
        return
    if row.get('status') == 'submitting' and not row.get('task_id'):
        raise SystemExit('Ambiguous previous submission: inspect instead of resubmitting')
    asset, action = PROMPTS[args.case]
    framing='16:9横屏' if args.ratio=='16:9' else '9:16竖屏'
    prompt = ('以@图片1为首帧，保持人物身份、服装、画面构图、灯光和全部道具一致。固定机位，单个连续镜头，无切镜，无变焦。'+framing+'，精致3D动画。安静的维多利亚卧室。' + action + ' 不生成对白、字幕、分格或水印。动作克制自然，保持解剖与空间关系。')
    parameters = {'model':'sd2.5', 'ratio':args.ratio, 'resolution':'480p', 'duration':6, 'generate_audio':False, 'watermark':False, 'output_format':'mp4'}
    base = os.environ.get('PHANROUTER_BASE_URL', 'https://cloud.phanthy.com/phanrouter').rstrip('/')
    headers = {'Authorization':'Bearer '+os.environ['PHANROUTER_API_KEY']}
    try:
        with httpx.Client(timeout=90) as client:
            if not row.get('task_id'):
                image = args.output / 'images' / (asset+'.png')
                payload = {**parameters, 'content':[{'type':'text','text':prompt}, {'type':'image_url','role':'reference_image','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()}}]}
                atomic_write_json(target/'request.json', {**parameters,'image':str(image),'prompt':prompt})
                row.update(status='submitting', submitted_epoch=time.time())
                atomic_write_json(record,row)
                res = client.post(base+'/api/v3/contents/generations/tasks',headers=headers,json=payload)
                if res.status_code >= 400:
                    row.update(status='rejected',http_status=res.status_code,error=sanitized(res.text))
                    atomic_write_json(record,row)
                    print(args.case,'rejected',res.status_code,flush=True)
                    return
                data=task_data(res.json())
                row['task_id']=data.get('id') or data.get('task_id')
                if not row['task_id']:
                    raise ValueError('No task ID in accepted response')
                row['status']='submitted'
                atomic_write_json(record,row)
                print(args.case,'accepted',flush=True)
            deadline=time.time()+900
            while time.time()<deadline:
                res=client.get(base+'/api/v3/contents/generations/tasks/'+row['task_id'],headers=headers,timeout=30)
                res.raise_for_status()
                data=task_data(res.json()); status=str(data.get('status','')).lower()
                if status != row.get('provider_status'):
                    print(args.case,status,round(time.time()-row['submitted_epoch']),flush=True)
                row['provider_status']=status
                if data.get('usage'): row['usage']=data['usage']
                atomic_write_json(record,row)
                if status in ('completed','success','succeeded'):
                    row['ready_seconds']=time.time()-row['submitted_epoch']
                    url=data.get('url') or data.get('video_url') or (data.get('content') or {}).get('video_url')
                    download(url,target/'video.mp4')
                    row['status']='completed'
                    atomic_write_json(record,row)
                    print(args.case,'completed',round(row['ready_seconds']),flush=True)
                    return
                if status in ('failed','failure','cancelled','canceled'):
                    row.update(status='failed',error=sanitized(data.get('error') or status))
                    atomic_write_json(record,row)
                    return
                time.sleep(5)
            row['status']='poll_timeout'
    except Exception as e:
        row.update(status='interrupted' if row.get('task_id') else 'submission_unknown',error=sanitized(e))
    atomic_write_json(record,row)
    print(args.case,row['status'],flush=True)


if __name__ == '__main__':
    main()
