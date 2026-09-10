#!/usr/bin/env python
"""One entry point for the whole pipeline: check it, start it, look at it, stop it.

    scripts/pipeline.py validate     what the file says, and whether it can be true at once
    scripts/pipeline.py status       what is actually running against what the file says
    scripts/pipeline.py start        start every active novel that is not already running
    scripts/pipeline.py stop         stop the conductors (render lanes finish their episodes)

Video keys and planning servers are shared, so the checks that matter are the ones a single
novel's config could never make: two novels rendering with one key, more planning slots
promised than a borrowed box allows, or two novels rendering the same chapters.  Nothing here
touches a running lane: stopping a conductor leaves its lane to finish, and starting one again
adopts it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "configs" / "pipeline.json"
PY = str(ROOT / ".venv" / "bin" / "python")


def load() -> dict:
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


def procs() -> list[tuple[int, str]]:
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ").strip()
        except OSError:
            continue
        if cmd:
            out.append((int(entry.name), cmd))
    return out


def conductor_pid(novel_id: str) -> int | None:
    for pid, cmd in procs():
        if "conductor_thin.py" in cmd and (f"--novel {novel_id}" in cmd or f"conductor.{novel_id}.json" in cmd):
            return pid
    return None


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


def start(pipeline: dict, only: str | None) -> int:
    problems = validate(pipeline)
    if problems:
        print("配置有冲突，没有启动任何东西：")
        for p in problems:
            print("  -", p)
        return 1
    started = []
    for novel in pipeline["novels"]:
        if not novel.get("active") or (only and novel["id"] != only):
            continue
        if conductor_pid(novel["id"]):
            print(f"{novel['title']}: 调度器已在运行，跳过")
            continue
        tmp = Path(novel.get("tmp_dir", f"/mnt/disk1/zengzhitao/tmp/conductor-{novel['id']}"))
        tmp.mkdir(parents=True, exist_ok=True)
        log = tmp.with_suffix(".stdout.log")
        command = [PY, str(ROOT / "scripts" / "conductor_thin.py"), "--pipeline", str(PIPELINE), "--novel", novel["id"]]
        with log.open("ab") as handle:
            handle.write(f"\n===== {time.strftime('%F %T')} {' '.join(command)}\n".encode())
            subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             env={**os.environ, "PYTHONPATH": "src:scripts"})
        started.append(novel["title"])
        time.sleep(2)
    print("已启动：" + ("、".join(started) if started else "无（都在运行）"))
    return 0


def stop(pipeline: dict, only: str | None) -> int:
    for novel in pipeline["novels"]:
        if only and novel["id"] != only:
            continue
        pid = conductor_pid(novel["id"])
        if pid:
            os.kill(pid, 15)
            print(f"{novel['title']}: 已停调度器 {pid}（渲染车道会把手上的集跑完）")
        else:
            print(f"{novel['title']}: 没有在跑")
    return 0


def status(pipeline: dict) -> int:
    problems = validate(pipeline)
    print("配置检查：" + ("通过" if not problems else "有问题"))
    for p in problems:
        print("  -", p)
    running = procs()
    print(f"\n{'小说':<12}{'调度器':>8}{'渲染车道':>10}{'规划块':>8}{'渲染 key':>12}{'规划服务器':>28}")
    for novel in pipeline["novels"]:
        if not novel.get("active"):
            continue
        pid = conductor_pid(novel["id"])
        lanes = sum(1 for _, c in running if "thin_batch.py" in c and f"outputs/{novel['id']} " in c + " "
                    and "--stage render" in c and "--review-only" not in c)
        blocks = sum(1 for _, c in running if "thin_batch.py" in c and f"outputs/{novel['id']} " in c + " "
                     and "--stage plan" in c)
        print(f"{novel['title']:<12}{(pid or '停'):>8}{lanes:>10}{blocks:>8}"
              f"{','.join(novel.get('render_keys', [])) or '-':>12}"
              f"{','.join(f'{k}x{v}' for k, v in novel.get('planning', {}).items()) or '-':>28}")
    video = pipeline["resources"]["video_keys"]
    print("\n并发池")
    for name, key in video.items():
        directory = Path(key["inflight_dir"])
        try:
            limit = (directory / "limit").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        held = 0
        inodes = {}
        for path in directory.glob("slot_*.lock"):
            try:
                inodes[path.stat().st_ino] = 1
            except OSError:
                pass
        try:
            for line in Path("/proc/locks").read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 6 and parts[1] == "FLOCK" and int(parts[5].split(":")[-1]) in inodes:
                    held += 1
        except OSError:
            pass
        print(f"  {name} ({key['model']}): 在飞 {held}/{limit}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("validate", "status", "start", "stop"))
    parser.add_argument("--novel", help="只对这一本小说操作")
    args = parser.parse_args()
    pipeline = load()
    if args.action == "validate":
        problems = validate(pipeline)
        print("配置检查：通过" if not problems else "配置检查：有问题")
        for p in problems:
            print("  -", p)
        return 1 if problems else 0
    if args.action == "status":
        return status(pipeline)
    if args.action == "start":
        return start(pipeline, args.novel)
    return stop(pipeline, args.novel)


if __name__ == "__main__":
    sys.exit(main())
