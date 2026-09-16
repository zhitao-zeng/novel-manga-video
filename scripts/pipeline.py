#!/usr/bin/env python3
"""Manage production, preparation and repair through their existing controllers."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from novel_manga.batch_control import FLOWS, control, snapshot

PIPELINE = ROOT / 'configs/pipeline.json'


def load() -> dict:
    return json.loads(PIPELINE.read_text(encoding="utf-8"))

def chapters_of(spec: str) -> set[int]:
    out: set[int] = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out |= set(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out

def validate(pipeline: dict) -> list[str]:
    problems = []
    video = pipeline["resources"]["video_keys"]
    models = pipeline["resources"]["planning_models"]
    active = [n for n in pipeline["novels"] if n.get("active")]

    for name, key in video.items():
        users = [n["title"] for n in active if name in n.get("render_keys", [])]
        if len(users) > 1:
            problems.append(f"视频 key {name} 被 {len(users)} 本小说同时使用：{users}。"
                            "同一把 key 的并发池是共享的，两本一起渲会互相抢额度")
    for name, model in models.items():
        asked = sum(n.get("planning", {}).get(name, 0) for n in active)
        if asked > int(model.get("slots", 1)):
            problems.append(f"规划服务器 {name} 只有 {model['slots']} 个位子，但各小说合计要 {asked} 个"
                            + (f"（{model['note']}）" if model.get("note") else ""))
    for novel in active:
        for key_name in novel.get("render_keys", []):
            cap = int(video[key_name]["clip_cap"])
            if not any(int(r["plan_mode"]) == cap for r in novel["ranges"]):
                problems.append(f"{novel['title']} 拿了 {key_name}（{cap} 秒档），但它的区段里没有 {cap} 秒的，这把 key 会闲置")
        seen: dict[int, str] = {}
        for r in novel["ranges"]:
            for n in chapters_of(r["chapters"]):
                tag = f"{r['chapters']}@{r['plan_mode']}s"
                if n in seen and seen[n].endswith(f"@{r['plan_mode']}s"):
                    problems.append(f"{novel['title']} 的第 {n} 章同时属于 {seen[n]} 和 {tag}")
                    break
                seen[n] = tag
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'status', 'start', 'stop'))
    parser.add_argument('--novel', help='小说 ID；未指定时 status 展示所有小说，start/stop 操作 active 小说')
    parser.add_argument('--flow', choices=FLOWS, help='start/stop 默认 production；status 默认全部流程')
    parser.add_argument('--json', action='store_true', help='输出结构化状态或操作结果')
    parser.add_argument('--chapters', help='仅 prepare start：章节范围；默认复用已有准备范围或小说配置')
    parser.add_argument('--workers', type=int, choices=range(1, 13), help='仅 prepare/repair start：工作并发数')
    args = parser.parse_args(argv)
    config = load()
    if args.novel and args.novel not in {n['id'] for n in config['novels']}:
        parser.error(f'unknown novel: {args.novel}')
    flow = args.flow or 'production'
    if args.chapters and (args.action != 'start' or flow != 'prepare'):
        parser.error('--chapters 只用于 prepare start')
    if args.workers and (args.action != 'start' or flow not in {'prepare', 'repair'}):
        parser.error('--workers 只用于 prepare/repair start')
    if args.action == 'validate' or (args.action == 'start' and flow == 'production'):
        problems = validate(config)
        if args.action == 'validate' or problems:
            print(json.dumps({'valid': not problems, 'problems': problems}, ensure_ascii=False) if args.json
                  else '配置检查：' + ('通过' if not problems else '\n' + '\n'.join(problems)))
            return int(bool(problems))
    if args.action == 'status':
        report = snapshot(ROOT, config, novel=args.novel, flow=args.flow)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print('小说 / 流程 / 状态 / 在途进程 / 待办集 / 阻塞集 / 更新于')
            for novel in report['novels']:
                for name, row in novel['flows'].items():
                    pending = row['pending'] if row['pending'] is not None else '未统计'
                    print(f"{novel['title']} / {name} / {row['status']} / {row['in_flight']} / {pending} / {row['blocked']} / {row['updated_at'] or '无记录'}")
                    for reason in row['blocked_reasons'][:5]:
                        print(f'  {reason}')
        return 0
    results = []; failed = False
    for spec in config['novels']:
        if (args.novel and spec['id'] != args.novel) or (not args.novel and not spec.get('active')):
            continue
        try:
            result = control(ROOT, spec, flow, args.action, chapters=args.chapters, workers=args.workers)
        except (OSError, ValueError) as error:
            result = {'action': 'error', 'reason': str(error)}; failed = True
        results.append({'novel': spec['id'], 'flow': flow, **result})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
