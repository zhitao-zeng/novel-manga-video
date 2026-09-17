"""dashboard_resources_thin responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from pathlib import Path
import fcntl
import json
import re
import shlex
import subprocess
import threading
import time
import urllib.request
import novel_manga.application.dashboard.config as dashboard_config
import novel_manga.application.dashboard.inventory as dashboard_inventory

def _proc_env(pid: str) -> dict:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    except OSError:
        return {}
    out = {}
    for item in raw.split("\0"):
        if "=" in item:
            key, _, value = item.partition("=")
            out[key] = value
    return out


def _range_bounds(spec: str) -> list[int]:
    """The chapters a --chapters argument covers (ranges and comma lists)."""
    numbers: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                numbers.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def _range_display(spec: str) -> tuple[str, str]:
    """A compact label for a --chapters spec.  A replan lane can carry hundreds
    of scattered chapter numbers; consecutive runs collapse ("576-578") and a
    long result truncates to its first segments plus a count - the full compact
    form goes to the tooltip so one wide cell can't wreck the whole table."""
    numbers = sorted(set(_range_bounds(spec)))
    if not numbers:
        return spec, spec
    parts = []
    start = prev = numbers[0]
    for number in numbers[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(f"{start}-{prev}" if prev > start else str(start))
        start = prev = number
    parts.append(f"{start}-{prev}" if prev > start else str(start))
    full = ", ".join(parts)
    if len(full) <= 60:
        return full, full
    tip = full if len(full) <= 600 else full[:600] + " …"
    return f"{', '.join(parts[:3])} … 共 {len(numbers)} 章", tip


def _lanes() -> list[dict]:
    """One row per running batch lane, described by its own command line and environment."""
    rows = []
    novel_keys = dashboard_config._lane_keys()
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in listing.splitlines():
        if "thin_batch.py" not in line or "--novel-dir" not in line or " grep " in line:
            continue
        pid, _, args = line.strip().partition(" ")
        if _entry_script(args) != 'thin_batch.py':
            continue
        novel_match, range_match, stage_match = dashboard_config.NOVEL_ARG.search(args), dashboard_config.RANGE.search(args), dashboard_config.STAGE.search(args)
        if not (novel_match and range_match):
            continue
        novel_id = Path(novel_match.group(1)).name
        chapters = _range_bounds(range_match.group(1))
        stage = stage_match.group(1) if stage_match else "?"
        if "--review-only" in args:
            stage = "review"
        env = _proc_env(pid)
        seconds = env.get("NOVEL_CLIP_SECONDS_MAX", "")
        mode = "15 秒档" if seconds.startswith("15") else "30 秒档"
        host = next((name for ip, name in dashboard_config.HOST_NAMES.items() if ip in env.get("QWEN38_LOCAL_BASE_URL", "")), "")
        model = dashboard_config.MODEL_NAMES.get(env.get("QWEN38_LOCAL_MODEL", ""), env.get("QWEN38_LOCAL_MODEL", ""))
        if stage in ("render", "review"):
            model = env.get("NOVEL_VIDEO_MODEL", "sd2.5") if stage == "render" else "本地 Qwen3.8"
            host = ""
        base = dashboard_config.ROOT / "outputs" / novel_id
        want = {"plan": "clip_plan.json", "review": "episode_review.json"}.get(stage)
        # Count what THIS run produced: a --replan lane rewrites chapters that already
        # had a plan, so counting files present would show it finished before it started.
        try:
            since = Path(f"/proc/{pid}").stat().st_mtime
        except OSError:
            since = 0.0
        done = covered = 0
        newest, newest_at = 0, 0.0
        for index in chapters:
            directory = base / f"{novel_id}_{index}"
            target = directory / want if want else directory / f"{novel_id}_{index}.mp4"
            if stage in {"render", "review", "all"}:
                keys = novel_keys.get(novel_id, [])
                h3_lane = (bool(keys) and all(k.get("base_url") for k in keys)) if stage == "review" else bool(env.get("NOVEL_LOCAL_H3_URL"))
                state = dashboard_inventory._episode_state(directory, h3_lane)
                if stage == "review":
                    if state["review"] != "reviewed":
                        continue
                elif state["status"] != "done":
                    continue
            try:
                stamp = target.stat().st_mtime
            except OSError:
                continue
            covered += 1
            if stamp >= since:
                done += 1
            if stamp > newest_at:
                newest, newest_at = index, stamp
        hours = max((time.time() - since) / 3600, 1 / 3600) if since else 0
        rate = round(done / hours, 1) if hours else None
        left = len(chapters) - covered
        short_range, full_range = _range_display(range_match.group(1))
        rows.append({
            "novel": dashboard_config.TITLES.get(novel_id, novel_id),
            "stage": {"plan": "规划", "render": "渲染", "review": "审查"}.get(stage, stage),
            "range": short_range, "range_full": full_range,
            "mode": mode, "model": (f"{model} · {host}" if host else model),
            "done": done, "covered": covered, "total": len(chapters), "current": newest,
            "rate": rate, "eta_hours": round(left / rate, 1) if rate else None,
            "age": round(time.time() - newest_at) if newest_at else None,
        })
    rows.sort(key=lambda r: (r["stage"], r["novel"], r["range"]))
    return rows


def _workers() -> list[dict]:
    """The single-episode processes the lanes fan out into: one clip runner per episode,
    one planner per chapter, one card builder per asset group."""
    rows = []
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,etimes=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, elapsed, args = parts[0], int(parts[1]), parts[2]
        entry = _entry_script(args)
        if entry == 'render_clips_thin.py':
            episode = dashboard_config.EPISODE_ARG.search(args)
            novel = dashboard_config.NOVEL_ARG.search(args)
            if not (episode and novel):
                continue
            novel_id = Path(novel.group(1)).name
            work = dashboard_config.ROOT / "outputs" / novel_id / episode.group(1) / "work" / "clips"
            try:
                plan = json.loads((work.parent.parent / "clip_plan.json").read_text(encoding="utf-8"))
                clips = [c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"]
                total = len(clips)
                ready = sum(any((work / cid).glob("attempt_*/clip.mp4")) for cid in clips)
            except (OSError, ValueError):
                total = ready = 0
            # Silence, not age, is the signal: on a shared H3 pool an episode waits an hour for slots and
            # is still fine; a runner whose log stopped moving is the one to look at.
            try:
                idle = int(time.time() - (work.parent.parent / "render.log").stat().st_mtime)
            except OSError:
                idle = elapsed
            rows.append({"kind": "渲染单集", "novel": dashboard_config.TITLES.get(novel_id, novel_id),
                         "what": episode.group(1).rsplit("_", 1)[-1] + " 集",
                         "detail": f"缓存 {ready}/{total} 段（未计质检）" if total else "", "elapsed": elapsed, "idle": idle})
        elif entry == 'plan_chapter_thin.py':
            index, novel = dashboard_config.INDEX_ARG.search(args), dashboard_config.NOVEL_ID_ARG.search(args)
            rows.append({"kind": "规划单章", "novel": dashboard_config.TITLES.get(novel.group(1) if novel else "", "?"),
                         "what": (index.group(1) + " 章") if index else "", "detail": "", "elapsed": elapsed})
        elif entry == 'build_cards_thin.py':
            assets, novel = dashboard_config.ASSETS_ARG.search(args), dashboard_config.NOVEL_ARG.search(args)
            names = assets.group(1).split(",") if assets else []
            kinds = "角色卡" if all(n.startswith("character") for n in names) else ("场景卡" if all(n.startswith("location") for n in names) else "卡片")
            rows.append({"kind": f"生图·{kinds}", "novel": dashboard_config.TITLES.get(Path(novel.group(1)).name if novel else "", "?"),
                         "what": ", ".join(n.rsplit("_", 1)[-1] for n in names[:4]), "detail": f"{len(names)} 张", "elapsed": elapsed})
        elif entry == 'prepare_h3_book.py' and '--episode' in shlex.split(args):
            episode, novel = dashboard_config.EPISODE_ARG.search(args), dashboard_config.NOVEL_ARG.search(args)
            rows.append({'kind':'开拍准备', 'novel':dashboard_config.TITLES.get(Path(novel.group(1)).name if novel else '', '?'),
                         'what':f'{episode.group(1)} 章' if episode else '', 'detail':'原文核对、局部修段或提示词准备', 'elapsed':elapsed})
        elif entry in {'repair_review_thin.py','verify_clips_thin.py','shared_audit_thin.py','prepare_recovery_thin.py','repair_clips_thin.py','source_recheck_thin.py'}:
            novel_id = _novel_from_args(args)
            episodes = re.search(r'--episodes\s+(\S+)', args)
            directory = re.search(r'--episode-dir\s+(\S+)', args)
            rows.append({'kind':'修复准备' if entry in {'prepare_recovery_thin.py','repair_clips_thin.py','source_recheck_thin.py'} else '片段审查',
                         'novel':dashboard_config.TITLES.get(novel_id, novel_id or '?'),
                         'what':episodes.group(1)+' 集' if episodes else (Path(directory.group(1)).name.rsplit('_',1)[-1]+' 集' if directory else ''),
                         'detail':'', 'elapsed':elapsed})
        elif entry == 'ms_upload_once.py':
            rows.append({"kind": "上传 ModelScope", "novel": "诸天万象录", "what": "", "detail": "", "elapsed": elapsed})
    rows.sort(key=lambda r: (r["kind"], -r["elapsed"]))
    return rows


def _ps_output() -> str:
    try:
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _entry_script(command: str) -> str:
    """Classify the actual Python program, not a wrapper's child arguments."""
    try:
        args = shlex.split(command)
    except ValueError:
        return ''
    if not args or not Path(args[0]).name.startswith('python'):
        return ''
    for arg in args[1:]:
        if arg in {'-c', '-m'}:
            return ''
        if not arg.startswith('-'):
            return Path(arg).name if arg.endswith('.py') else ''
    return ''


def _novel_from_args(command: str) -> str:
    try:
        args = shlex.split(command)
    except ValueError:
        return ''
    for flag in ['--novel-dir', '--novel-id', '--episode-dir', '--episode']:
        if flag in args and args.index(flag)+1 < len(args):
            name = Path(args[args.index(flag)+1]).name
            return name.rsplit('_',1)[0] if flag in {'--episode-dir','--episode'} else name
    return ''


def _processes() -> dict:
    out = _ps_output()
    if not out:
        return {}
    counts = dict.fromkeys(['runners','planners','preparations','repairs','cards','reviews','conductors','uploads'], 0)
    kinds = {'render_clips_thin.py':'runners', 'plan_chapter_thin.py':'planners',
             'build_cards_thin.py':'cards', 'repair_review_thin.py':'reviews', 'verify_clips_thin.py':'reviews',
             'shared_audit_thin.py':'reviews', 'second_review.py':'reviews', 'thin_review.py':'reviews',
             'prepare_recovery_thin.py':'repairs', 'repair_clips_thin.py':'repairs','source_recheck_thin.py':'repairs',
             'conductor_thin.py':'conductors','ms_upload_once.py':'uploads'}
    for line in out.splitlines():
        entry = _entry_script(line)
        kind = kinds.get(entry)
        if entry == 'prepare_h3_book.py':
            kind = 'preparations' if '--episode' in shlex.split(line) else 'conductors'
        elif entry == 'manage_repair_thin.py' and 'run' in shlex.split(line):
            kind = 'conductors'
        elif entry == 'thin_batch.py' and '--review-only' in shlex.split(line):
            kind = 'reviews'
        if kind:
            counts[kind] += 1
    return counts


def _pool_dir(novel_id: str, pool: str) -> Path:
    """Where this novel's key keeps its slots.  Since 2026-09-10 a key can own one directory
    shared by every novel it renders, named in the conductor config as inflight_dir."""
    for config in sorted((dashboard_config.ROOT / "configs").glob("conductor.*.json")):
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if Path(str(cfg.get("novel_dir", ""))).name != novel_id:
            continue
        for key in cfg.get("keys", []):
            if (key.get("pool") or "") == pool and key.get("inflight_dir"):
                return Path(key["inflight_dir"])
    return dashboard_config.ROOT / "outputs" / novel_id / (f".inflight-{pool}" if pool else ".inflight")


def _conductor_running(novel_id: str) -> bool:
    """Is a conductor live for this novel?  Started either from the shared pipeline file
    (--novel <id>) or from the older per-novel config.  A stopped novel leaves its limit
    files behind, which is why the rows are gated on this."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if "conductor_thin.py" not in cmd:
            continue
        if f"--novel {novel_id} " in cmd + " " or f"conductor.{novel_id}.json" in cmd:
            return True
    return False


def _held_by_novel(directory: Path) -> dict:
    """Which novel is holding each lock in a shared pool, by the runner that owns it."""
    inodes = {}
    for path in directory.glob("slot_*.lock"):
        try:
            inodes[path.stat().st_ino] = path
        except OSError:
            pass
    counts: dict[str, int] = {}
    try:
        lines = Path("/proc/locks").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return counts
    for line in lines:
        parts = line.split()
        if len(parts) < 6 or parts[1] != "FLOCK":
            continue
        try:
            pid, ino = int(parts[4]), int(parts[5].split(":")[-1])
        except ValueError:
            continue
        if ino not in inodes:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        novel_id = _novel_from_args(cmd)
        if novel_id:
            counts[novel_id] = counts.get(novel_id, 0) + 1
    return counts


def _inflight() -> list[dict]:
    """One row per key a novel actually renders with, from the conductor configs."""
    rows = []
    cache: dict[Path, dict] = {}
    for novel_id, keys in dashboard_config._lane_keys().items():
        if novel_id not in dashboard_config.TITLES or not _conductor_running(novel_id):
            continue
        for key in keys:
            directory = Path(key["inflight_dir"]) if key.get("inflight_dir") else _pool_dir(novel_id, key.get("pool") or "")
            if not (directory / "limit").is_file():
                continue
            if directory not in cache:
                try:
                    limit = int((directory / "limit").read_text().strip() or 0)
                except (OSError, ValueError):
                    limit = 0
                cache[directory] = {"limit": limit, "held": _held_by_novel(directory), "total": _held_slots(directory)}
            info = cache[directory]
            label = f"本地H3 · {key.get('name')}" if key.get("base_url") else (key.get("model") or key.get("name", ""))
            rows.append({"novel": dashboard_config.TITLES[novel_id], "pool": label, "local": bool(key.get("base_url")),
                         "limit": info["limit"], "slots": info["held"].get(novel_id, 0),
                         "shared": info["total"]})
    known = {(r["novel"], r["pool"]) for r in rows}
    rows.extend(r for r in _lane_pools() if (r["novel"], r["pool"]) not in known)
    return rows


def _lane_pools() -> list[dict]:
    """The pools running render lanes draw on, read from each lane's environment: a lane started by
    hand (2026-09-13: every 雾月 and 星海 lane) has no conductor to be found through."""
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in listing.splitlines():
        pid, _, args = line.strip().partition(" ")
        entry = _entry_script(args)
        if not (entry == 'render_clips_thin.py' or entry == 'thin_batch.py' and '--stage render' in args and '--review-only' not in args):
            continue
        novel_match = dashboard_config.NOVEL_ARG.search(args)
        env = _proc_env(pid)
        directory = env.get("NOVEL_INFLIGHT_DIR", "").strip()
        if not (novel_match and directory):
            continue
        novel_id = Path(novel_match.group(1)).name
        if novel_id not in dashboard_config.TITLES or (novel_id, directory) in seen:
            continue
        seen.add((novel_id, directory))
        pool = Path(directory)
        try:
            limit = int((pool / "limit").read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            limit = 0
        local = bool(env.get("NOVEL_LOCAL_H3_URL"))
        label = f"本地H3 · {env.get('NOVEL_INFLIGHT_POOL') or pool.name}" if local else (env.get("NOVEL_VIDEO_MODEL") or pool.name)
        rows.append({"novel": dashboard_config.TITLES[novel_id], "pool": label, "local": local, "limit": limit,
                     "slots": _held_by_novel(pool).get(novel_id, 0), "shared": _held_slots(pool)})
    return rows


_LOCAL_CACHE: dict = {"at": 0.0, "rows": []}


_LOCAL_LOCK = threading.Lock()


def where_label(host: str | None, gpus: list[int] | None) -> str:
    """Which machine and which cards - the only identity that tells two rows apart when a resident
    and a night lease hold the same hardware."""
    machine = dashboard_config.HOST_NAMES.get(str(host or ""), str(host or "?"))
    if not gpus:
        return machine
    ordered = sorted(int(g) for g in gpus)
    run = ordered == list(range(ordered[0], ordered[-1] + 1)) and len(ordered) > 2
    return "%s 卡%s" % (machine, ("%d-%d" % (ordered[0], ordered[-1])) if run else ",".join(str(g) for g in ordered))


def _local_targets(keys: list[dict]) -> list[tuple[str, str, str | None, list[int] | None]]:
    """(name, base URL, host, cards) of every local H3 instance behind these keys: a key names one
    instance, or the pool - the resident instances plus the night shift's active leases."""
    out = []
    for key in keys:
        base = str(key.get("base_url") or "").rstrip("/")
        if base == "pool" or base.startswith("pool:"):
            try:
                from novel_manga.providers.h3_pool import H3Pool
                out += [(m.name, m.url, m.host, m.gpus) for m in H3Pool(base[5:] or None).members()]
            except Exception:  # noqa: BLE001 - a broken pool file must not take the board down
                continue
        elif base:
            out.append((key.get("name", ""), base, None, None))
    return out


def _local_video(ttl: float = 120.0) -> list[dict]:
    """What the local H3 instances say about themselves.

    The service knows things our own files cannot: what it is rendering right now, what is
    queued behind it, and how long the last hour's jobs took.  Asked directly, with a short
    cache so the board's twenty-second refresh does not become a poll loop on that machine.
    """
    now = time.time()
    if now - _LOCAL_CACHE["at"] < ttl:
        return _LOCAL_CACHE["rows"]
    rows = []
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for novel_id, keys in dashboard_config._lane_keys().items():
        for name, base, host, gpus in _local_targets(keys):
            row = {"name": name, "where": where_label(host, gpus), "host": host or "",
                   "gpus": sorted(int(g) for g in (gpus or [])),
                   "novel": dashboard_config.TITLES.get(novel_id, novel_id),
                   "alive": False, "pending": 0, "done_hour": 0,
                   "seconds_hour": 0.0, "avg_take": None}
            try:
                with direct.open(f"{base}/health", timeout=3) as response:
                    row["alive"] = response.status == 200
            except (urllib.error.URLError, OSError, ValueError):
                rows.append(row)
                continue
            try:
                # No limit: the service caps it at 100 and ignores offset, so asking for the
                # last 100 jobs capped done_hour at 100 and made a 237/hour instance report 98.
                with direct.open(f"{base}/v1/videos?order=desc", timeout=20) as response:
                    jobs = json.loads(response.read()).get("data", [])
            except (urllib.error.URLError, OSError, ValueError):
                jobs = []
            took = []
            for job in jobs:
                # The service has no running state: whatever it is rendering this second is
                # still reported as queued with progress 0, so both count as waiting.
                status = str(job.get("status", "")).lower()
                if status in {"queued", "running", "in_progress", "processing"}:
                    row["pending"] += 1
                created, finished = job.get("created_at"), job.get("completed_at")
                if status == "completed" and created and finished and now - float(finished) < 3600:
                    row["done_hour"] += 1
                    try:
                        row["seconds_hour"] += float(job.get("seconds") or 0)
                    except (TypeError, ValueError):
                        pass
                    took.append(float(finished) - float(created))
            if took:
                row["avg_take"] = round(sum(took) / len(took), 1)
            rows.append(row)
    # by machine then first card, so a resident and a night lease on the same cards sit together
    rows.sort(key=lambda r: (str(r.get("host") or ""), (r.get("gpus") or [99])[0]))
    _LOCAL_CACHE.update({"at": now, "rows": rows})
    return rows


def _held_slots(directory: Path) -> int:
    """Slots a runner is really holding.  The lock files outlive the run that made
    them, so counting files showed a finished novel as fully in flight."""
    held = 0
    for path in directory.glob("slot_*.lock"):
        try:
            with path.open("a+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except OSError:
                    held += 1
        except OSError:
            pass
    return held


def _log_ts(line: str) -> float | None:
    """Epoch of a conductor log line ("MM-DD HH:MM:SS ..."); the year is this year,
    backed off one year if that lands in the future (a log spanning New Year)."""
    found = dashboard_config.LOG_TS.match(line)
    if not found:
        return None
    month, day, clock = int(found.group(1)), int(found.group(2)), found.group(3)
    try:
        stamp = time.mktime(time.strptime(f"{time.localtime().tm_year}-{month:02d}-{day:02d} {clock}", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    return stamp - 366 * 86400 if stamp > time.time() + 86400 else stamp


def _warnings() -> list[dict]:
    out = []
    cutoff = time.time() - dashboard_config.WARNING_WINDOW_SECONDS
    for novel in dashboard_config.NOVELS:
        if not novel["conductor"] or not novel["conductor"].is_file():
            continue
        if not _conductor_running(novel["id"]):
            continue  # a stopped novel's old lines are history, not something to act on
        for line in dashboard_inventory._read_tail(novel["conductor"], 400):
            # A lane that stops because every range finished is a normal ending, not a warning.
            if "tick:" in line or "all ranges finished" in line:
                continue
            hit = next((k for k in dashboard_config.WARNING_KINDS if k[0].search(line)), None)
            if hit:
                stamp = _log_ts(line)
                if stamp is None or stamp < cutoff:
                    continue
                out.append({"novel": novel["title"], "kind": hit[1], "level": hit[2],
                            "text": line.strip()[:150], "ts": stamp})
    out.sort(key=lambda row: row["ts"], reverse=True)
    return out[:8]


def _local_video_cached() -> list[dict]:
    """Remote instance statistics never hold up a page request."""
    with _LOCAL_LOCK:
        if time.time() - _LOCAL_CACHE['at'] > 120 and not _LOCAL_CACHE.get('building'):
            _LOCAL_CACHE['building'] = True
            def collect():
                try:
                    _local_video()
                finally:
                    _LOCAL_CACHE['building'] = False
            threading.Thread(target=collect, daemon=True, name='status-h3-resources').start()
    return _LOCAL_CACHE['rows']
