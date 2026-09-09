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


def _warnings() -> list[str]:
    out = []
    for novel in NOVELS:
        if not novel["conductor"] or not novel["conductor"].is_file():
            continue
        for line in _read_tail(novel["conductor"], 400):
            if re.search(r"429|Traceback|stopped|auth|FAILED|park", line) and "tick:" not in line:
                out.append(f"{novel['title']}: {line.strip()[:150]}")
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
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--text:#e6e8ee;--dim:#8b93a3;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--bar:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
h1{font-size:16px;margin:0;font-weight:600}.dim{color:var(--dim);font-size:12px}
main{padding:16px 20px;display:grid;gap:14px;max-width:1100px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.novel{display:grid;grid-template-columns:1fr;gap:8px}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.name{font-size:15px;font-weight:600}.num{font-variant-numeric:tabular-nums}
.track{height:8px;background:#22262f;border-radius:99px;overflow:hidden;display:flex}
.fill{background:var(--bar);height:100%}.fill.plan{background:#334155}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:4px 8px 4px 0;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500}tr:last-child td{border-bottom:0}
.tick{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);word-break:break-all}
.warn{color:var(--warn)}.bad{color:var(--bad)}.ok{color:var(--ok)}
.pills{display:flex;gap:8px;flex-wrap:wrap}.pill{background:#22262f;border-radius:99px;padding:2px 10px;font-size:12px}
</style></head><body>
<header><h1>小说成片进度</h1><div class="dim" id="stamp">加载中…</div></header>
<main>
  <div id="novels" class="novel"></div>
  <div class="card"><div class="dim" style="margin-bottom:6px">正在跑的任务</div><table id="lanes"></table></div>
  <div class="card"><div class="dim" style="margin-bottom:6px">具体在处理的东西</div><table id="workers"></table></div>
  <div class="card"><div class="dim" style="margin-bottom:6px">进程与在途</div><div class="pills" id="procs"></div>
    <table id="inflight" style="margin-top:10px"></table></div>
  <div class="card"><div class="dim" style="margin-bottom:6px">最近告警</div><div id="warnings" class="tick"></div></div>
</main>
<script>
const pct = (a,b) => b ? Math.min(100, a*100/b) : 0;
function novelCard(n){
  const eta = n.eta_hours == null ? "—" : (n.eta_hours < 1 ? Math.round(n.eta_hours*60)+" 分钟" : n.eta_hours+" 小时");
  const last = n.last_final ? new Date(n.last_final*1000).toLocaleTimeString("zh-CN") : "—";
  return `<div class="card">
    <div class="row"><span class="name">${n.title}</span>
      <span class="num">成片 <b>${n.done}</b> / 已规划 ${n.planned} / 全书 ${n.chapters}</span></div>
    <div class="track"><div class="fill" style="width:${pct(n.done,n.chapters)}%"></div>
      <div class="fill plan" style="width:${pct(n.planned-n.done,n.chapters)}%"></div></div>
    <div class="row dim"><span>已规划里 30 秒档 ${n.modes["30"]} 集（走 sd2.5） · 15 秒档 ${n.modes["15"]} 集（走 sd2.0）</span><span></span></div>
    <div class="row dim"><span>近一小时 ${n.per_hour} 集 · 近 15 分钟折合 ${n.recent_per_hour} 集/小时</span>
      <span>剩 ${n.left} 集，约 ${eta} · 最近一集 ${last}</span></div>
    ${n.tick ? `<div class="tick">${n.tick}</div>` : ""}</div>`;
}
async function tick(){
  try{
    const r = await fetch("status.json", {cache:"no-store"});
    const d = await r.json();
    document.getElementById("stamp").textContent = d.now + " · 每 20 秒刷新";
    document.getElementById("novels").innerHTML = d.novels.map(novelCard).join("");
    document.getElementById("lanes").innerHTML =
      "<tr><th>任务</th><th>小说</th><th>章节</th><th>档位</th><th>模型</th><th>本轮</th><th>速度</th><th>该段剩余</th><th>当前章</th><th>更新</th></tr>" +
      (d.lanes.length ? d.lanes.map(l=>`<tr><td>${l.stage}</td><td>${l.novel}</td><td class="num">${l.range}</td>
        <td>${l.mode}</td><td>${l.model||"—"}</td>
        <td class="num">${l.done}</td>
        <td class="num">${l.rate==null?"—":l.rate+" /小时"}</td>
        <td class="num">${l.total-l.covered}${l.eta_hours!=null?` · ${l.eta_hours<1?Math.round(l.eta_hours*60)+" 分":l.eta_hours+" 时"}`:""}</td>
        <td class="num">${l.current||"—"}</td>
        <td class="${l.age!=null&&l.age>1800?"warn":"dim"}">${l.age==null?"—":(l.age<90?l.age+" 秒前":Math.round(l.age/60)+" 分钟前")}</td></tr>`).join("")
        : "<tr><td class='dim'>没有在跑的任务</td></tr>");
    const mins = s => s<90 ? s+" 秒" : Math.round(s/60)+" 分钟";
    document.getElementById("workers").innerHTML = "<tr><th>类型</th><th>小说</th><th>对象</th><th>进度</th><th>已跑</th></tr>" +
      (d.workers.length ? d.workers.map(w=>`<tr><td>${w.kind}</td><td>${w.novel}</td><td class="num">${w.what}</td>
        <td class="dim">${w.detail}</td><td class="${w.elapsed>1800?"warn":"dim"}">${mins(w.elapsed)}</td></tr>`).join("")
        : "<tr><td class='dim'>暂时没有</td></tr>");
    document.getElementById("procs").innerHTML = Object.entries(d.processes)
      .map(([k,v])=>`<span class="pill">${({runners:"渲染",planners:"规划",cards:"角色卡",reviews:"审查",conductors:"调度器",uploads:"上传"})[k]||k} <b>${v}</b></span>`).join("");
    document.getElementById("inflight").innerHTML = "<tr><th>小说</th><th>通道</th><th>在途/上限</th></tr>" +
      d.inflight.map(i=>`<tr><td>${i.novel}</td><td>${i.pool}</td><td class="num">${i.slots} / ${i.limit}</td></tr>`).join("");
    document.getElementById("warnings").innerHTML = d.warnings.length ? d.warnings.map(w=>`<div>${w}</div>`).join("") : "<span class='ok'>无</span>";
  }catch(e){ document.getElementById("stamp").textContent = "读取失败：" + e; }
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
