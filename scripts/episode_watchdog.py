"""挂起看门狗:巡检各 run 目录的 execution.json,把疑似挂起的集追加到告警流水。

判定:最近的 execution.json/agent-run.json 超过 --stale-minutes(默认 20),
当前运行的 pid 已死(读 /proc/<pid> 不存在)。已停止或完成的运行不告警。
只读运行记录和最终候选回执,只追加 <runtime>/watchdog-alerts.jsonl。
--once 跑一次退出,默认每 120 秒循环。
"""
import argparse
import datetime
import json
import time
from pathlib import Path

ALERTS_FILE = 'watchdog-alerts.jsonl'
STOPPED = {'finished', 'finished_with_issues', 'blocked', 'paused', 'deferred'}


def _read_record(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def _pid_dead(pid):
    return isinstance(pid, int) and pid > 0 and not Path(f'/proc/{pid}').exists()


def scan(runtime, stale_minutes=20, *, now=None):
    """Return new alerts and append them to <runtime>/watchdog-alerts.jsonl."""
    runtime = Path(runtime)
    now = time.time() if now is None else now
    alerts = []
    for record_path in sorted(runtime.glob('*/execution.json')):
        try:
            record = json.loads(record_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        state_path = record_path.parent/'agent-run.json'
        state = _read_record(state_path)
        status = state.get('status')
        if status in STOPPED or (not status and record.get('phase') in STOPPED):
            continue
        changed = record_path.stat().st_mtime
        if state:
            changed = max(changed, state_path.stat().st_mtime)
        elif not status:
            final = record_path.parent/'final-candidate/manifest.json'
            if _read_record(final).get('passed') is True and final.stat().st_mtime >= changed:
                continue
        idle_minutes = (now - changed) / 60
        pid = state.get('pid', record.get('pid'))
        if idle_minutes <= stale_minutes or not _pid_dead(pid):
            continue
        episode = record.get('episode')
        alerts.append({'at': datetime.datetime.now().isoformat(),
                       'episode': Path(episode).name if episode else record_path.parent.name,
                       'run': record_path.parent.name,
                       'phase': state.get('phase', record.get('phase')),
                       'pid': pid,
                       'idle_minutes': round(idle_minutes, 1)})
    if alerts:
        with (runtime / ALERTS_FILE).open('a', encoding='utf-8') as stream:
            for alert in alerts:
                stream.write(json.dumps(alert, ensure_ascii=False) + '\n')
    return alerts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path, help='运行目录,其下为各 run 目录')
    parser.add_argument('--stale-minutes', type=float, default=20)
    parser.add_argument('--once', action='store_true', help='跑一次退出;默认每 120 秒循环')
    args = parser.parse_args(argv)
    while True:
        for alert in scan(args.runtime, args.stale_minutes):
            print(json.dumps(alert, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        time.sleep(120)


if __name__ == '__main__':
    raise SystemExit(main())
