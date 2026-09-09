"""A live progress page for the novel pipeline, served from the box that holds the data.

Everything on this page is derived from files the pipeline already writes - finished
episode videos, clip plans, conductor logs, planning-lane logs - so it keeps working
whether or not anyone is watching, and it never needs a session of mine to update it.

Run:  .venv/bin/python tmp/status_server.py [port]
Open: http://172.28.7.16:18900/   (or tunnel: ssh -N -L 18900:127.0.0.1:18900 gpu16)
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/mnt/disk1/zengzhitao/novel-manga-video")
TMP = Path("/mnt/disk1/zengzhitao/tmp")
CACHE_SECONDS = 20
PORT = 18900

NOVELS = [
    {"id": "zhutian-card", "title": "诸天万象录", "conductor": TMP / "conductor" / "conductor.log"},
    {"id": "xinghai", "title": "星海龙途", "conductor": TMP / "conductor-xinghai" / "conductor.log"},
    {"id": "wuyue", "title": "雾月秘典", "conductor": None},
]
# Which planning model a lane is using, by the endpoint in its environment.
MODEL_NAMES = {
    "Qwen3.8-Flash-Next": "Flash-Next", "DeepSeek-V4-Flash-Vision-Exp": "DeepSeek-V4",
    "Qwen3.8-27B-Project": "本地 Qwen3.8",
}
HOST_NAMES = {"172.28.4.81": "GPU81", "172.28.4.52": "GPU52", "127.0.0.1": "本机"}
TITLES = {"zhutian-card": "诸天万象录", "xinghai": "星海龙途", "wuyue": "雾月秘典"}
TICK = re.compile(r"tick:\s*(.+)")
RANGE = re.compile(r"--chapters\s+(\S+)")
NOVEL_ARG = re.compile(r"--novel-dir\s+(\S+)")
STAGE = re.compile(r"--stage\s+(\w+)")


def _read_tail(path: Path, lines: int = 400) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, lines * 400)
            handle.seek(size - block)
            return handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _episode_numbers(novel_id: str) -> tuple[list[float], int]:
    """Modification times of finished episode videos, and how many chapters have a plan."""
    finals: list[float] = []
    planned = 0
    base = ROOT / "outputs" / novel_id
    try:
        for entry in os.scandir(base):
            if not entry.is_dir() or not entry.name.startswith(f"{novel_id}_"):
                continue
            index = entry.name.rsplit("_", 1)[-1]
            if not index.isdigit():
                continue
            video = Path(entry.path) / f"{entry.name}.mp4"
            try:
                finals.append(video.stat().st_mtime)
            except OSError:
                pass
            if (Path(entry.path) / "clip_plan.json").is_file():
                planned += 1
    except OSError:
        pass
    return finals, planned


_MODE_CACHE: dict[str, tuple[float, str]] = {}


def _plan_modes(novel_id: str) -> dict:
    """How many of a novel's plans are cut for 15 s clips and how many for 30 s.

    The planner stamps its policy into every clip plan ("...-15s" for the short mode),
    which is the only place the mode survives; re-reading a plan only when it changes
    keeps this cheap for a two-thousand-episode novel.
    """
    counts = {"15": 0, "30": 0}
    base = ROOT / "outputs" / novel_id
    try:
        entries = list(os.scandir(base))
    except OSError:
        return counts
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith(f"{novel_id}_"):
            continue
        path = Path(entry.path) / "clip_plan.json"
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        cached = _MODE_CACHE.get(entry.path)
        if not cached or cached[0] != stamp:
            try:
                policy = json.loads(path.read_text(encoding="utf-8")).get("policy", "")
            except (OSError, ValueError):
                continue
            cached = (stamp, "15" if str(policy).endswith("-15s") else "30")
            _MODE_CACHE[entry.path] = cached
        counts[cached[1]] += 1
    return counts


def _chapters(novel_id: str) -> int:
    try:
        return len(json.loads((ROOT / "outputs" / novel_id / "novel.json").read_text(encoding="utf-8"))["chapters"])
    except (OSError, ValueError, KeyError):
        return 0


def _rate(finals: list[float], window: float) -> int:
    now = time.time()
    return sum(1 for t in finals if now - t <= window)


def _spark(finals: list[float]) -> list[int]:
    """Finished episodes per hour for the last 24 hours, oldest first."""
    now = time.time()
    start = now - 24 * 3600
    buckets = [0] * 24
    for t in finals:
        if t >= start:
            buckets[min(23, int((t - start) // 3600))] += 1
    return buckets


def _today(finals: list[float]) -> int:
    midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return sum(1 for t in finals if t >= midnight)


def _novel_status(novel: dict) -> dict:
    finals, planned = _episode_numbers(novel["id"])
    chapters = _chapters(novel["id"])
    per_hour = _rate(finals, 3600)
    recent = _rate(finals, 900) * 4  # last quarter hour, extrapolated
    left = max(0, planned - len(finals))
    speed = recent or per_hour
    tick = ""
    if novel["conductor"]:
        for line in reversed(_read_tail(novel["conductor"], 200)):
            found = TICK.search(line)
            if found:
                tick = found.group(1)
                break
    return {
        "id": novel["id"], "title": novel["title"], "chapters": chapters, "planned": planned,
        "done": len(finals), "per_hour": per_hour, "recent_per_hour": recent, "left": left,
        "eta_hours": round(left / speed, 1) if speed else None,
        "last_final": max(finals) if finals else None, "tick": tick,
        "modes": _plan_modes(novel["id"]),
        "spark": _spark(finals), "today": _today(finals),
    }


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


def _lanes() -> list[dict]:
    """One row per running batch lane, described by its own command line and environment."""
    rows = []
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in listing.splitlines():
        if "thin_batch.py" not in line or "--novel-dir" not in line or " grep " in line:
            continue
        pid, _, args = line.strip().partition(" ")
        novel_match, range_match, stage_match = NOVEL_ARG.search(args), RANGE.search(args), STAGE.search(args)
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
        host = next((name for ip, name in HOST_NAMES.items() if ip in env.get("QWEN38_LOCAL_BASE_URL", "")), "")
        model = MODEL_NAMES.get(env.get("QWEN38_LOCAL_MODEL", ""), env.get("QWEN38_LOCAL_MODEL", ""))
        if stage in ("render", "review"):
            model = env.get("NOVEL_VIDEO_MODEL", "sd2.5") if stage == "render" else "本地 Qwen3.8"
            host = ""
        base = ROOT / "outputs" / novel_id
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
        rows.append({
            "novel": TITLES.get(novel_id, novel_id),
            "stage": {"plan": "规划", "render": "渲染", "review": "审查"}.get(stage, stage),
            "range": range_match.group(1), "mode": mode, "model": (f"{model} · {host}" if host else model),
            "done": done, "covered": covered, "total": len(chapters), "current": newest,
            "rate": rate, "eta_hours": round(left / rate, 1) if rate else None,
            "age": round(time.time() - newest_at) if newest_at else None,
        })
    rows.sort(key=lambda r: (r["stage"], r["novel"], r["range"]))
    return rows


EPISODE_ARG = re.compile(r"--episode\s+(\S+)")
INDEX_ARG = re.compile(r"--episode-index\s+(\d+)")
ASSETS_ARG = re.compile(r"--assets\s+(\S+)")
NOVEL_ID_ARG = re.compile(r"--novel-id\s+(\S+)")


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
        if " grep " in args:
            continue
        if "render_clips_thin.py" in args:
            episode = EPISODE_ARG.search(args)
            novel = NOVEL_ARG.search(args)
            if not (episode and novel):
                continue
            novel_id = Path(novel.group(1)).name
            work = ROOT / "outputs" / novel_id / episode.group(1) / "work" / "clips"
            try:
                total = len([d for d in os.scandir(work) if d.is_dir()])
                ready = sum(1 for d in os.scandir(work) if any(Path(d.path).glob("attempt_*/clip.mp4")))
            except OSError:
                total = ready = 0
            rows.append({"kind": "渲染单集", "novel": TITLES.get(novel_id, novel_id),
                         "what": episode.group(1).rsplit("_", 1)[-1] + " 集",
                         "detail": f"{ready}/{total} 段" if total else "", "elapsed": elapsed})
        elif "plan_chapter_thin.py" in args:
            index, novel = INDEX_ARG.search(args), NOVEL_ID_ARG.search(args)
            rows.append({"kind": "规划单章", "novel": TITLES.get(novel.group(1) if novel else "", "?"),
                         "what": (index.group(1) + " 章") if index else "", "detail": "", "elapsed": elapsed})
        elif "build_cards_thin.py" in args:
            assets, novel = ASSETS_ARG.search(args), NOVEL_ARG.search(args)
            names = assets.group(1).split(",") if assets else []
            kinds = "角色卡" if all(n.startswith("character") for n in names) else ("场景卡" if all(n.startswith("location") for n in names) else "卡片")
            rows.append({"kind": f"生图·{kinds}", "novel": TITLES.get(Path(novel.group(1)).name if novel else "", "?"),
                         "what": ", ".join(n.rsplit("_", 1)[-1] for n in names[:4]), "detail": f"{len(names)} 张", "elapsed": elapsed})
        elif "ms_upload_once.py" in args:
            rows.append({"kind": "上传 ModelScope", "novel": "诸天万象录", "what": "", "detail": "", "elapsed": elapsed})
    rows.sort(key=lambda r: (r["kind"], -r["elapsed"]))
    return rows


def _ps_output() -> str:
    try:
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _processes() -> dict:
    out = _ps_output()
    if not out:
        return {}
    def count(pattern: str) -> int:
        return sum(1 for line in out.splitlines() if pattern in line and "grep" not in line)
    return {
        "runners": count("render_clips_thin.py --novel-dir"),
        "planners": count("plan_chapter_thin.py"),
        "cards": count("build_cards_thin.py"),
        "reviews": count("--review-only"),
        "conductors": count("conductor_thin.py --config"),
        "uploads": count("ms_upload_once.py"),
    }


def _inflight() -> list[dict]:
    rows = []
    for novel in NOVELS:
        for pool in ("", "sd20", "h3"):
            directory = ROOT / "outputs" / novel["id"] / (f".inflight-{pool}" if pool else ".inflight")
            limit_file = directory / "limit"
            if not limit_file.is_file():
                continue
            try:
                limit = int(limit_file.read_text().strip() or 0)
            except (OSError, ValueError):
                continue
            slots = _held_slots(directory)
            rows.append({"novel": novel["title"], "pool": pool or "sd2.5", "limit": limit, "slots": slots})
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


LOG_TS = re.compile(r"^(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})")
WARNING_KINDS = [
    (re.compile(r"(?<!\d)429(?!\d)"), "限流", "warn"),
    (re.compile(r"Traceback"), "异常", "bad"),
    (re.compile(r"auth", re.I), "鉴权", "bad"),
    (re.compile(r"FAILED"), "失败", "bad"),
    (re.compile(r"stopped"), "停止", "warn"),
    (re.compile(r"park"), "暂停", "warn"),
]


def _log_ts(line: str) -> float | None:
    """Epoch of a conductor log line ("MM-DD HH:MM:SS ..."); the year is this year,
    backed off one year if that lands in the future (a log spanning New Year)."""
    found = LOG_TS.match(line)
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
    for novel in NOVELS:
        if not novel["conductor"] or not novel["conductor"].is_file():
            continue
        for line in _read_tail(novel["conductor"], 400):
            # A lane that stops because every range finished is a normal ending, not a warning.
            if "tick:" in line or "all ranges finished" in line:
                continue
            hit = next((k for k in WARNING_KINDS if k[0].search(line)), None)
            if hit:
                out.append({"novel": novel["title"], "kind": hit[1], "level": hit[2],
                            "text": line.strip()[:150], "ts": _log_ts(line)})
    return out[-8:]


def snapshot() -> dict:
    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "novels": [_novel_status(n) for n in NOVELS],
        "lanes": _lanes(),
        "workers": _workers(),
        "processes": _processes(),
        "inflight": _inflight(),
        "warnings": _warnings(),
    }


_cache: dict = {"at": 0.0, "data": None}


def cached_snapshot() -> dict:
    if time.time() - _cache["at"] > CACHE_SECONDS or _cache["data"] is None:
        _cache["data"] = snapshot()
        _cache["at"] = time.time()
    return _cache["data"]


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小说成片进度</title><style>
:root{
  --bg:#f3f4f8; --surface:#ffffff; --surface-2:#f0f2f8; --line:#e2e5ee;
  --text:#1f2430; --dim:#667085; --faint:#98a0b3;
  --ok:#0e9f6e; --warn:#d97706; --bad:#e02424; --accent:#3b6fe0; --accent-2:#0ea5e9;
}
*{box-sizing:border-box}
html{color-scheme:light}
body{margin:0;background:
  radial-gradient(1200px 500px at 80% -10%, #d7e2f766, transparent),
  radial-gradient(900px 400px at 0% -10%, #e6dcf266, transparent),
  var(--bg);
  color:var(--text);font:14px/1.55 -apple-system,"SF Pro SC","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.num,.stat b,.mono{font-variant-numeric:tabular-nums}
header{position:sticky;top:0;z-index:10;backdrop-filter:blur(12px);
  background:#f3f4f8d9;border-bottom:1px solid var(--line);
  padding:14px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.02em}
.health{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;
  padding:3px 12px;border-radius:99px;border:1px solid var(--line);background:var(--surface)}
#stamp{margin-left:auto;color:var(--dim);font-size:12px}
#stamp.err{color:var(--bad)}
main{padding:20px 24px 40px;display:grid;gap:16px;max-width:1180px;margin:0 auto}
.card{background:var(--surface);
  border:1px solid var(--line);border-radius:14px;padding:16px 18px;
  box-shadow:0 1px 2px #1f24300a, 0 8px 24px #1f243008}
.label{font-size:11px;letter-spacing:.12em;color:var(--dim);text-transform:uppercase;margin-bottom:10px;font-weight:600}

/* status dot with glow */
.dot{display:inline-block;width:8px;height:8px;border-radius:99px;flex:none}
.dot.ok{background:var(--ok);box-shadow:0 0 6px #0e9f6e59}
.dot.warn{background:var(--warn);box-shadow:0 0 6px #d9770659}
.dot.bad{background:var(--bad);box-shadow:0 0 6px #e0242459}
.dot.idle{background:#b7bdc9;box-shadow:none}
.health.ok{color:var(--ok)}.health.warn{color:var(--warn)}.health.bad{color:var(--bad)}

/* hero stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--surface);border:1px solid var(--line);
  border-radius:14px;padding:14px 16px;box-shadow:0 1px 2px #1f24300a}
.stat .k{font-size:11.5px;color:var(--dim);letter-spacing:.06em;margin-bottom:4px}
.stat b{font-size:22px;font-weight:680;letter-spacing:-.01em}
.stat .u{font-size:12px;color:var(--dim);font-weight:500;margin-left:2px}
.stat .sub{font-size:11.5px;color:var(--dim);margin-top:2px}

/* novel cards */
.ncard{display:grid;gap:12px}
.nrow{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.nname{font-size:16px;font-weight:650;display:flex;align-items:center;gap:9px}
.pill{font-size:11.5px;font-weight:600;padding:2px 10px;border-radius:99px;border:1px solid var(--line);background:var(--surface-2);color:var(--dim)}
.pill.ok{color:var(--ok);border-color:#0e9f6e40}
.pill.warn{color:var(--warn);border-color:#d9770640}
.pill.bad{color:var(--bad);border-color:#e0242440}
.neta{font-size:13px;color:var(--dim)}
.neta b{color:var(--text);font-weight:650}
.nbody{display:grid;grid-template-columns:1fr 240px;gap:18px;align-items:end}
.track{height:7px;background:#e7eaf2;border-radius:99px;overflow:hidden;display:flex;margin:10px 0 8px}
.fill{background:linear-gradient(90deg,var(--accent),var(--accent-2));height:100%}
.fill.plan{background:#c6cddd;height:100%}
.nmeta{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--dim);font-size:12.5px}
.spark{display:flex;align-items:flex-end;gap:2px;height:34px}
.spark i{flex:1;background:linear-gradient(180deg,var(--accent),#3b6fe055);border-radius:2px 2px 0 0;min-height:2px;opacity:.55}
.spark i:last-child{opacity:1}
.spark-label{font-size:11px;color:var(--dim);text-align:right;margin-top:4px}
.tick{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);
  word-break:break-all;border-top:1px dashed var(--line);padding-top:8px}
details{border-top:1px dashed var(--line);padding-top:6px}
summary{cursor:pointer;color:var(--dim);font-size:12.5px;list-style:none;user-select:none}
summary::before{content:"▸ ";font-size:10px}
details[open] summary::before{content:"▾ "}
summary:hover{color:var(--text)}

/* attention */
.attn-item{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.attn-item:last-child{border-bottom:0}
.attn-item .when{color:var(--dim);font-size:12px;margin-left:auto;flex:none}
.attn-item .src{color:var(--dim);font-size:12px;flex:none}
.attn-raw{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);
  word-break:break-all;margin:2px 0 6px 18px}
.all-clear{color:var(--ok);font-size:13px}

/* tables */
.twrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.06em;text-align:left;
  padding:6px 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid #eef0f6;white-space:nowrap}
tbody tr{transition:background .15s}
tbody tr:hover{background:#1f243005}
tr:last-child td{border-bottom:0}
.dim{color:var(--dim)}.warn-t{color:var(--warn)}.ok-t{color:var(--ok)}
.pills{display:flex;gap:8px;flex-wrap:wrap}

@media (max-width:820px){
  main{padding:14px 12px 32px}
  header{padding:12px 14px}
  .nbody{grid-template-columns:1fr}
  table.resp thead{display:none}
  table.resp, table.resp tbody, table.resp tr, table.resp td{display:block;width:100%}
  table.resp tr{border:1px solid var(--line);border-radius:10px;margin-bottom:8px;padding:6px 12px}
  table.resp td{border-bottom:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px;white-space:normal}
  table.resp td::before{content:attr(data-l);color:var(--dim);font-size:12px;flex:none}
}
</style></head><body>
<header>
  <h1>小说成片进度</h1>
  <span class="health" id="health"><i class="dot idle"></i>读取中</span>
  <span id="stamp">加载中…</span>
</header>
<main>
  <div class="stats" id="stats"></div>
  <div id="novels" style="display:grid;gap:16px"></div>
  <div class="card" id="attention-card"><div class="label">需要关注</div><div id="attention"></div></div>
  <div class="card"><div class="label">运行明细 · 通道</div><div class="twrap"><table class="resp" id="lanes"></table></div></div>
  <div class="card"><div class="label">运行明细 · 单集任务</div><div class="twrap"><table class="resp" id="workers"></table></div></div>
  <div class="card"><div class="label">进程与在途</div><div class="pills" id="procs"></div>
    <div class="twrap" style="margin-top:12px"><table class="resp" id="inflight"></table></div></div>
</main>
<script>
const $ = id => document.getElementById(id);
const pct = (a,b) => b ? Math.min(100, a*100/b) : 0;
const fmtETA = h => h == null ? "—" : (h < 1 ? Math.round(h*60)+" 分钟" : h < 48 ? h+" 小时" : (h/24).toFixed(1)+" 天");
const fmtAgo = s => s == null ? "—" : (s < 90 ? s+" 秒前" : s < 5400 ? Math.round(s/60)+" 分钟前" : (s/3600).toFixed(1)+" 小时前");
const laneHealth = l => l.age == null ? "warn" : l.age < 600 ? "ok" : l.age < 1800 ? "warn" : "bad";
const workerHealth = w => w.elapsed < 900 ? "ok" : w.elapsed < 1800 ? "warn" : "bad";
const HEALTH_TEXT = {ok:"全部正常", warn:"有任务停滞", bad:"有异常"};
const WORST = {ok:0, warn:1, bad:2};

function spark(bars){
  const max = Math.max(...bars, 1);
  return `<div><div class="spark">` + bars.map((v,i) =>
    `<i style="height:${Math.max(6, v*100/max)}%" title="${24-1-i} 小时前: ${v} 集"></i>`).join("") +
    `</div><div class="spark-label">近 24 小时 · 共 ${bars.reduce((a,b)=>a+b,0)} 集</div></div>`;
}

function novelCard(d, n){
  const lanes = d.lanes.filter(l => l.novel === n.title);
  const workers = d.workers.filter(w => w.novel === n.title);
  const pools = d.inflight.filter(i => i.novel === n.title);
  const health = lanes.length ? lanes.map(laneHealth).reduce((a,b)=>WORST[a]>WORST[b]?a:b) : (n.done ? "ok" : "warn");
  const last = n.last_final ? new Date(n.last_final*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}) : "—";
  const detailRows = (lanes.length + workers.length)
    ? `<table style="margin-top:8px"><tbody>` +
      lanes.map(l=>`<tr><td class="dim">${l.stage}通道</td><td class="num">${l.range}</td><td>${l.mode}</td><td class="dim">${l.model||"—"}</td><td class="num">本轮 ${l.done} · 剩 ${l.total-l.covered}</td><td class="${l.age>1800?"warn-t":"dim"}">${fmtAgo(l.age)}</td></tr>`).join("") +
      workers.map(w=>`<tr><td class="dim">${w.kind}</td><td class="num">${w.what}</td><td colspan="2" class="dim">${w.detail}</td><td></td><td class="${w.elapsed>1800?"warn-t":"dim"}">已跑 ${fmtAgo(w.elapsed).replace("前","")}</td></tr>`).join("") +
      `</tbody></table>` : `<div class="dim" style="margin-top:8px;font-size:12.5px">这本书当前没有在跑的任务</div>`;
  return `<div class="card ncard">
    <div class="nrow">
      <span class="nname"><i class="dot ${health}"></i>${n.title}<span class="pill ${health}">${health==="ok"?"正常":health==="warn"?"放缓":"停滞"}</span></span>
      <span class="neta">剩 <b>${n.left}</b> 集 · 约 <b>${fmtETA(n.eta_hours)}</b> · 最近一集 ${last}</span>
    </div>
    <div class="nbody">
      <div>
        <div class="nmeta"><span>成片 <b class="num" style="color:var(--text)">${n.done}</b> / 已规划 ${n.planned} / 全书 ${n.chapters}</span>
          <span>今日 +${n.today} · 近一小时 ${n.per_hour} 集 · 近 15 分钟折合 ${n.recent_per_hour}/时</span></div>
        <div class="track"><div class="fill" style="width:${pct(n.done,n.chapters)}%"></div>
          <div class="fill plan" style="width:${pct(n.planned-n.done,n.chapters)}%"></div></div>
        <div class="nmeta"><span>30 秒档 ${n.modes["30"]} 集（sd2.5） · 15 秒档 ${n.modes["15"]} 集（sd2.0）</span>
          <span>${pools.map(p=>`${p.pool} ${p.slots}/${p.limit}`).join(" · ")||"无在途通道"}</span></div>
      </div>
      ${spark(n.spark)}
    </div>
    ${n.tick ? `<div class="tick">tick: ${n.tick}</div>` : ""}
    <details><summary>这本书的运行明细（${lanes.length + workers.length} 个在跑）</summary>${detailRows}</details>
  </div>`;
}

function attention(d){
  const items = [];
  for (const l of d.lanes) if (l.age != null && l.age > 1800)
    items.push({level: l.age > 3600 ? "bad" : "warn", ts: Date.now()/1000 - l.age,
      html: `${l.novel} · ${l.stage}通道 <span class="num">${l.range}</span> — ${fmtAgo(l.age)}无产出（最后在 ${l.current||"?"} 章）`});
  for (const w of d.workers) if (w.elapsed > 1800)
    items.push({level: "warn", ts: null, html: `${w.novel} · ${w.kind} ${w.what} 已运行 ${fmtAgo(w.elapsed).replace("前","")}`});
  for (const w of d.warnings)
    items.push({level: w.level, ts: w.ts, novel: w.novel,
      html: `${w.novel} · ${w.kind}`, raw: w.text});
  if (!items.length) return `<div class="all-clear">✓ 没有需要关注的情况</div>`;
  items.sort((a,b)=>WORST[b.level]-WORST[a.level] || (b.ts||0)-(a.ts||0));
  return items.map(i=>{
    const when = i.ts ? fmtAgo(Math.max(0, Date.now()/1000 - i.ts)) : "";
    return `<div class="attn-item"><i class="dot ${i.level}"></i><span>${i.html}</span><span class="when">${when}</span></div>` +
      (i.raw ? `<div class="attn-raw">${i.raw}</div>` : "");
  }).join("");
}

function laneRow(l){
  const h = laneHealth(l);
  return `<tr>
    <td data-l="状态"><i class="dot ${h}"></i></td>
    <td data-l="任务">${l.stage} · ${l.novel}</td>
    <td data-l="章节" class="num">${l.range}${l.current?` · 在 ${l.current}`:""}</td>
    <td data-l="档位">${l.mode} <span class="dim">${l.model||""}</span></td>
    <td data-l="进度" class="num">本轮 ${l.done} · 剩 ${l.total-l.covered}</td>
    <td data-l="速度" class="num">${l.rate==null?"—":l.rate+"/时"}${l.eta_hours!=null?` · ${fmtETA(l.eta_hours)}`:""}</td>
    <td data-l="更新" class="${h==="ok"?"dim":"warn-t"}">${fmtAgo(l.age)}</td></tr>`;
}

function tick(){
  fetch("status.json",{cache:"no-store"}).then(r=>r.json()).then(d=>{
    const totalLeft = d.novels.reduce((a,n)=>a+n.left,0);
    const speed = d.novels.reduce((a,n)=>a+(n.recent_per_hour||n.per_hour),0);
    const today = d.novels.reduce((a,n)=>a+n.today,0);
    const nowSec = Date.now()/1000;
    const states = [
      ...d.lanes.map(laneHealth), ...d.workers.map(workerHealth),
      // only fresh warnings say something about right now; a 429 from hours ago doesn't
      ...d.warnings.filter(w=>w.ts && nowSec - w.ts < 7200).map(w=>w.level),
      ...(d.lanes.length||d.workers.length ? [] : ["warn"]),
    ];
    const overall = states.reduce((a,b)=>WORST[a]>WORST[b]?a:b, "ok");
    $("health").className = "health " + overall;
    $("health").innerHTML = `<i class="dot ${overall}"></i>${HEALTH_TEXT[overall]}`;
    $("stats").innerHTML =
      `<div class="stat"><div class="k">今日成片</div><b>${today}</b><span class="u">集</span></div>
       <div class="stat"><div class="k">当前速度</div><b>${speed}</b><span class="u">集/时</span></div>
       <div class="stat"><div class="k">在跑任务</div><b>${d.lanes.length + d.workers.length}</b><span class="u">个</span>
         <div class="sub">${d.lanes.length} 条通道 · ${d.workers.length} 个单集</div></div>
       <div class="stat"><div class="k">全部剩余</div><b>${totalLeft}</b><span class="u">集</span>
         <div class="sub">按当前速度约 ${fmtETA(speed ? Math.round(totalLeft/speed*10)/10 : null)}</div></div>`;
    $("novels").innerHTML = d.novels.map(n=>novelCard(d,n)).join("");
    $("attention").innerHTML = attention(d);
    $("attention-card").style.display = "";
    $("lanes").innerHTML = `<thead><tr><th></th><th>任务</th><th>章节</th><th>档位</th><th>进度</th><th>速度</th><th>更新</th></tr></thead><tbody>` +
      (d.lanes.length ? d.lanes.map(laneRow).join("") : `<tr><td class="dim">没有在跑的通道</td></tr>`) + `</tbody>`;
    $("workers").innerHTML = `<thead><tr><th>类型</th><th>小说</th><th>对象</th><th>进度</th><th>已跑</th></tr></thead><tbody>` +
      (d.workers.length ? d.workers.map(w=>`<tr>
        <td data-l="类型">${w.kind}</td><td data-l="小说">${w.novel}</td><td data-l="对象" class="num">${w.what}</td>
        <td data-l="进度" class="dim">${w.detail}</td>
        <td data-l="已跑" class="${w.elapsed>1800?"warn-t":"dim"}">${fmtAgo(w.elapsed).replace("前","")}</td></tr>`).join("")
        : `<tr><td class="dim">暂时没有</td></tr>`) + `</tbody>`;
    $("procs").innerHTML = Object.entries(d.processes)
      .map(([k,v])=>`<span class="pill">${({runners:"渲染",planners:"规划",cards:"角色卡",reviews:"审查",conductors:"调度器",uploads:"上传"})[k]||k} <b class="num">${v}</b></span>`).join("");
    $("inflight").innerHTML = `<thead><tr><th>小说</th><th>通道</th><th>在途/上限</th></tr></thead><tbody>` +
      d.inflight.map(i=>`<tr><td data-l="小说">${i.novel}</td><td data-l="通道">${i.pool}</td>
        <td data-l="在途" class="num">${i.slots} / ${i.limit}</td></tr>`).join("") + `</tbody>`;
    const ageSec = Math.max(0, Math.round((Date.now() - new Date(d.now.replace(" ","T")))/1000));
    $("stamp").className = "";
    $("stamp").textContent = `数据 ${ageSec} 秒前 · 每 20 秒刷新`;
  }).catch(e=>{ $("stamp").textContent = "读取失败：" + e; $("stamp").className = "err"; });
}
tick(); setInterval(tick, 20000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.path.startswith("/status.json"):
            body = json.dumps(cached_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet: this runs for months
        pass


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
