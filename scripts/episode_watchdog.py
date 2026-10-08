"""挂起看门狗:巡检各 run 目录的 execution.json,把疑似挂起的集追加到告警流水。

判定:execution.json 的 mtime 超过 --stale-minutes(默认 20)且记录的 pid 已死
(读 /proc/<pid> 不存在)。只读 execution.json,只写 <runtime>/watchdog-alerts.jsonl
(追加,不覆盖)。--once 跑一次退出,默认每 120 秒循环。
"""
import argparse
import datetime
import json
import time
from pathlib import Path

ALERTS_FILE = 'watchdog-alerts.jsonl'


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
        idle_minutes = (now - record_path.stat().st_mtime) / 60
        pid = record.get('pid')
        if idle_minutes <= stale_minutes or not _pid_dead(pid):
            continue
        episode = record.get('episode')
        alerts.append({'at': datetime.datetime.now().isoformat(),
                       'episode': Path(episode).name if episode else record_path.parent.name,
                       'run': record_path.parent.name,
                       'phase': record.get('phase'),
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
