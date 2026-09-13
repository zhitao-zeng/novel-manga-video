"""A live progress page for the novel pipeline, served from the box that holds the data.

Everything on this page is derived from files the pipeline already writes - finished
episode videos, clip plans, conductor logs, planning-lane logs - so it keeps working
whether or not anyone is watching, and it never needs a session of mine to update it.

Run:  PYTHONPATH=src:scripts .venv/bin/python scripts/status_server.py [port]
Open: http://172.28.7.16:18900/   (or tunnel: ssh -N -L 18900:127.0.0.1:18900 gpu16)
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from thin_runs import RENDER_RUNS_PER_PLAN, episode_status, gate_failures, render_runs
from review_progress_thin import viewer_progress

ROOT = Path(__file__).resolve().parents[1]
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


_EPISODE_CACHE: dict[tuple[str, bool], tuple[tuple, dict]] = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _episode_state(directory: Path, h3_lane: bool) -> dict:
    """Read the same completion/gate state as the production lane; cache until an input changes.

    A video left by an earlier plan or a failed quality check is still watchable, but is not a completed episode.
    Review status is separate: a model error is unjudged work, not a verdict about video quality.
    """
    names = ("clip_plan.json", "thin_media_report.json", "review_feedback.json", f"{directory.name}.mp4",
             ".render_runs", "episode_review.json")
    stamps = tuple(_mtime(directory / name) for name in names)
    key = (str(directory), h3_lane)
    hit = _EPISODE_CACHE.get(key)
    if hit and hit[0] == stamps:
        return hit[1]
    unreadable = False
    try:
        status = episode_status(directory, h3_lane)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        status, unreadable = "pending", True
    try:
        report = json.loads((directory / "thin_media_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    runs = render_runs(directory)
    uncertain = status != "done" and any("SubmissionUncertain" in str(row.get("error", ""))
                                         for row in report.get("clips", []))
    blocked = bool(stamps[0]) and status != "done" and (
        runs >= RENDER_RUNS_PER_PLAN or uncertain or unreadable
        or (status == "done_with_warnings" and not (h3_lane and gate_failures(directory))))
    review_state = "not_ready"
    if status in {"done", "done_with_warnings"} and stamps[3]:
        review_state = "pending"
        if stamps[5] >= stamps[3]:
            try:
                reviews = json.loads((directory / "episode_review.json").read_text(encoding="utf-8")).get("clips", {})
                plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
                expected = {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"}
                if any(c.get("severity") == "review_error" for c in reviews.values()):
                    review_state = "error"
                elif expected and all(reviews.get(cid, {}).get("severity") in {"pass", "minor", "fail"} for cid in expected):
                    review_state = "reviewed"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
    value = {"status": status, "planned": bool(stamps[0]), "final_mtime": stamps[3], "blocked": blocked,
             "uncertain": uncertain, "unreadable": unreadable, "review": review_state, "runs": runs}
    _EPISODE_CACHE[key] = (stamps, value)
    return value


def _episode_inventory(novel_id: str) -> dict:
    """Counts and qualified-final mtimes, shared by the live page and analytics board."""
    result = {"finals": [], "planned": 0, "files": 0, "states": {}, "blocked": 0, "uncertain": 0,
              "review_pending": 0, "review_errors": 0, "attention": []}
    keys = _lane_keys().get(novel_id, [])
    h3_lane = bool(keys) and all(k.get("base_url") for k in keys)
    base = ROOT / "outputs" / novel_id
    try:
        entries = sorted((e for e in os.scandir(base) if e.is_dir() and e.name.startswith(f"{novel_id}_")
                          and e.name.rsplit("_", 1)[-1].isdigit()), key=lambda e: int(e.name.rsplit("_", 1)[-1]))
    except OSError:
        return result
    for entry in entries:
        state = _episode_state(Path(entry.path), h3_lane)
        status = state["status"]
        result["planned"] += state["planned"]
        result["files"] += bool(state["final_mtime"])
        result["states"][status] = result["states"].get(status, 0) + 1
        if status == "done":
            result["finals"].append(state["final_mtime"])
        result["blocked"] += state["blocked"]
        result["uncertain"] += state["uncertain"]
        result["review_pending"] += state["review"] == "pending"
        result["review_errors"] += state["review"] == "error"
        reason = ("提交结果不明，需核账" if state["uncertain"] else "状态文件无法读取" if state["unreadable"]
                  else "重试次数用尽，需处理" if state["blocked"] and state["runs"] >= RENDER_RUNS_PER_PLAN
                  else "质检未通过，需处理" if state["blocked"]
                  else "质检未通过，等待重拍" if status == "done_with_warnings"
                  else "计划或修正已更新，等待重做" if status == "stale"
                  else "片段生成失败" if status == "clips_failed"
                  else "旧视频待重新验证" if state["final_mtime"] and status in {"pending", "no_plan"}
                  else "审查失败，仍未审完" if state["review"] == "error" else "")
        if reason:
            result["attention"].append({"chapter": int(entry.name.rsplit("_", 1)[-1]), "reason": reason})
    return result


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


_DELIVERY_CACHE: dict[str, tuple[float, dict | None]] = {}


def _delivery(novel_id: str) -> dict | None:
    """The novel-level verdict scripts/delivery_gate_thin.py writes: how many episodes pass the technical and
    review gates, and what stops the rest.  Read from disk, not recomputed here: the script needs the chapter
    text for its shadow gate, and a review batch is what changes the answer (thin_batch runs it after one)."""
    path = ROOT / "outputs" / novel_id / "delivery.json"
    stamp = _mtime(path)
    if not stamp:
        return None
    hit = _DELIVERY_CACHE.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        gates = data.get("gates", {})
        value = {
            "deliverable": data.get("deliverable", 0), "total": data.get("total", 0),
            "generated_at": data.get("generated_at", ""),
            "tech_blocked": gates.get("tech", {}).get("blocked", 0),
            "review_blocked": gates.get("review", {}).get("blocked", 0),
            "must_fix_clips": gates.get("review", {}).get("must_fix_clips", 0),
            "script_flagged": gates.get("script", {}).get("flagged", 0),
            "script_would_block": gates.get("script", {}).get("would_block", 0),
        }
    except (OSError, ValueError, AttributeError, TypeError):
        value = None
    _DELIVERY_CACHE[str(path)] = (stamp, value)
    return value


def _novel_status(novel: dict) -> dict:
    inventory = _episode_inventory(novel["id"])
    finals, planned = inventory["finals"], inventory["planned"]
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
        "eta_hours": round(left / speed, 1) if speed and not inventory["blocked"] else None,
        "last_final": max(finals) if finals else None, "tick": tick,
        "modes": _plan_modes(novel["id"]),
        "spark": _spark(finals), "today": _today(finals),
        "delivery": _delivery(novel["id"]),
        **{k: v for k, v in inventory.items() if k not in {"finals", "planned"}},
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
    novel_keys = _lane_keys()
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
            if stage in {"render", "review", "all"}:
                keys = novel_keys.get(novel_id, [])
                h3_lane = (bool(keys) and all(k.get("base_url") for k in keys)) if stage == "review" else bool(env.get("NOVEL_LOCAL_H3_URL"))
                state = _episode_state(directory, h3_lane)
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
            "novel": TITLES.get(novel_id, novel_id),
            "stage": {"plan": "规划", "render": "渲染", "review": "审查"}.get(stage, stage),
            "range": short_range, "range_full": full_range,
            "mode": mode, "model": (f"{model} · {host}" if host else model),
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
            rows.append({"kind": "渲染单集", "novel": TITLES.get(novel_id, novel_id),
                         "what": episode.group(1).rsplit("_", 1)[-1] + " 集",
                         "detail": f"缓存 {ready}/{total} 段（未计质检）" if total else "", "elapsed": elapsed, "idle": idle})
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
        "conductors": count("conductor_thin.py --config") + count("conductor_thin.py --pipeline"),
        "uploads": count("ms_upload_once.py"),
    }


def _pool_dir(novel_id: str, pool: str) -> Path:
    """Where this novel's key keeps its slots.  Since 2026-09-10 a key can own one directory
    shared by every novel it renders, named in the conductor config as inflight_dir."""
    for config in sorted((ROOT / "configs").glob("conductor.*.json")):
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if Path(str(cfg.get("novel_dir", ""))).name != novel_id:
            continue
        for key in cfg.get("keys", []):
            if (key.get("pool") or "") == pool and key.get("inflight_dir"):
                return Path(key["inflight_dir"])
    return ROOT / "outputs" / novel_id / (f".inflight-{pool}" if pool else ".inflight")


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
        match = re.search(r"--episode (\w+?)_\d+", cmd)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def _lane_keys() -> dict:
    """Which video keys each novel renders with: from the shared pipeline file if it is there,
    otherwise from the per-novel conductor configs."""
    out: dict[str, list] = {}
    pipeline = ROOT / "configs" / "pipeline.json"
    if pipeline.is_file():
        try:
            cfg = json.loads(pipeline.read_text(encoding="utf-8"))
            video = cfg.get("resources", {}).get("video_keys", {})
            for novel in cfg.get("novels", []):
                names = [n for n in novel.get("render_keys", []) if n in video]
                if names:
                    out[novel["id"]] = [{"name": n, **video[n]} for n in names]
            return out
        except (OSError, ValueError):
            pass
    for config in sorted((ROOT / "configs").glob("conductor.*.json")):
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        novel_id = Path(str(cfg.get("novel_dir", ""))).name
        if cfg.get("keys"):
            out[novel_id] = cfg["keys"]
    return out


def _inflight() -> list[dict]:
    """One row per key a novel actually renders with, from the conductor configs."""
    rows = []
    cache: dict[Path, dict] = {}
    for novel_id, keys in _lane_keys().items():
        if novel_id not in TITLES or not _conductor_running(novel_id):
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
            rows.append({"novel": TITLES[novel_id], "pool": label, "local": bool(key.get("base_url")),
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
        if "thin_batch.py" not in line or "--stage render" not in line or "--review-only" in line or " grep " in line:
            continue
        pid, _, args = line.strip().partition(" ")
        novel_match = NOVEL_ARG.search(args)
        env = _proc_env(pid)
        directory = env.get("NOVEL_INFLIGHT_DIR", "").strip()
        if not (novel_match and directory):
            continue
        novel_id = Path(novel_match.group(1)).name
        if novel_id not in TITLES or (novel_id, directory) in seen:
            continue
        seen.add((novel_id, directory))
        pool = Path(directory)
        try:
            limit = int((pool / "limit").read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            limit = 0
        local = bool(env.get("NOVEL_LOCAL_H3_URL"))
        label = f"本地H3 · {env.get('NOVEL_INFLIGHT_POOL') or pool.name}" if local else (env.get("NOVEL_VIDEO_MODEL") or pool.name)
        rows.append({"novel": TITLES[novel_id], "pool": label, "local": local, "limit": limit,
                     "slots": _held_by_novel(pool).get(novel_id, 0), "shared": _held_slots(pool)})
    return rows


_LOCAL_CACHE: dict = {"at": 0.0, "rows": []}


HOST_NAMES = {"local": "gpu16"}  # the night shift's id for this box; everyone calls it gpu16


def where_label(host: str | None, gpus: list[int] | None) -> str:
    """Which machine and which cards - the only identity that tells two rows apart when a resident
    and a night lease hold the same hardware."""
    machine = HOST_NAMES.get(str(host or ""), str(host or "?"))
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
    for novel_id, keys in _lane_keys().items():
        for name, base, host, gpus in _local_targets(keys):
            row = {"name": name, "where": where_label(host, gpus), "host": host or "",
                   "gpus": sorted(int(g) for g in (gpus or [])),
                   "novel": TITLES.get(novel_id, novel_id),
                   "alive": False, "pending": 0, "done_hour": 0,
                   "seconds_hour": 0.0, "avg_take": None}
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=3) as response:
                    row["alive"] = response.status == 200
            except (urllib.error.URLError, OSError, ValueError):
                rows.append(row)
                continue
            try:
                # No limit: the service caps it at 100 and ignores offset, so asking for the
                # last 100 jobs capped done_hour at 100 and made a 237/hour instance report 98.
                with urllib.request.urlopen(f"{base}/v1/videos?order=desc", timeout=20) as response:
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


WARNING_WINDOW_SECONDS = 6 * 3600


def _warnings() -> list[dict]:
    out = []
    cutoff = time.time() - WARNING_WINDOW_SECONDS
    for novel in NOVELS:
        if not novel["conductor"] or not novel["conductor"].is_file():
            continue
        if not _conductor_running(novel["id"]):
            continue  # a stopped novel's old lines are history, not something to act on
        for line in _read_tail(novel["conductor"], 400):
            # A lane that stops because every range finished is a normal ending, not a warning.
            if "tick:" in line or "all ranges finished" in line:
                continue
            hit = next((k for k in WARNING_KINDS if k[0].search(line)), None)
            if hit:
                stamp = _log_ts(line)
                if stamp is None or stamp < cutoff:
                    continue
                out.append({"novel": novel["title"], "kind": hit[1], "level": hit[2],
                            "text": line.strip()[:150], "ts": stamp})
    out.sort(key=lambda row: row["ts"], reverse=True)
    return out[:8]


def snapshot() -> dict:
    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "novels": [_novel_status(n) for n in NOVELS],
        "lanes": _lanes(),
        "workers": _workers(),
        "processes": _processes(),
        "inflight": _inflight(),
        "local": _local_video(),
        "warnings": _warnings(),
    }


# ---- analytics board -----------------------------------------------------
# Everything below is still derived from files the pipeline already writes:
# review verdicts from episode_review.json, attempt counts from
# thin_media_report.json, the video model from render.log's header, daily
# throughput from finished-video mtimes.  A full pass over a two-thousand-
# episode novel reads ~100 MB once; per-file mtime caching makes later
# passes touch only what changed, and the board rebuilds in a background
# thread every few minutes so page loads never wait on it.

REVIEW_CATS = [
    ("identity_ok", False, "身份"), ("location_ok", False, "场景"),
    ("time_of_day_ok", False, "时段"), ("text_or_watermark", True, "水印/文字"),
    ("chat_text_ok", False, "聊天文字"), ("visual_defects", True, "画面缺陷"),
]
VIDEO_MODEL = re.compile(r'"video_model":\s*"([^"]+)"')
BOARD_SECONDS = 300
_FILE_CACHE: dict[str, tuple[float, object]] = {}


def _cached(path: Path, parser):
    """Parse a per-episode file, re-parsing only when its mtime changed."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    hit = _FILE_CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    data = parser(path)
    _FILE_CACHE[key] = (stamp, data)
    return data


def _parse_review(path: Path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    clips = report.get("clips", {})
    sev: dict[str, int] = {}
    cats: dict[str, int] = {}
    for clip in clips.values():
        severity = str(clip.get("severity", "?"))
        sev[severity] = sev.get(severity, 0) + 1
        for key, bad, name in REVIEW_CATS:
            if clip.get(key) == bad:
                cats[name] = cats.get(name, 0) + 1
    # A clip's severity answers "does this differ from the card at all", which counts a
    # side character's sleeve.  The pipeline only reshoots what fix_tier graded must_fix,
    # and thin_review.py records exactly those in the episode-level feedback map, so its
    # size is the number of clips actually queued to shoot again.  Reading the map rather
    # than the per-clip tier (only stored since 2026-09-12) makes the redo rate available
    # for every review ever written: 雾月 610 clips, against 5529 graded non-pass.
    feedback = report.get("feedback")
    must_fix = len(feedback) if isinstance(feedback, dict) else 0
    return {"sev": sev, "cats": cats, "must_fix": must_fix}


def _parse_media(path: Path):
    try:
        clips = json.loads(path.read_text(encoding="utf-8")).get("clips", [])
    except (OSError, ValueError):
        return None
    return {"clips": len(clips), "attempts": sum(len(c.get("attempts", [])) for c in clips)}


def _parse_render_model(path: Path):
    """The video model an episode was rendered with, from the settings header
    render_clips_thin.py prints into render.log."""
    # A repair run appends a new header. The first header could name Seedance even after the episode moved to H3.
    found = VIDEO_MODEL.findall("\n".join(_read_tail(path, 2000)))
    return found[-1] if found else ""


def _board_novel(novel: dict) -> dict:
    nid = novel["id"]
    inventory = _episode_inventory(nid)
    finals, planned = inventory["finals"], inventory["planned"]
    keys = _lane_keys().get(nid, [])
    h3_lane = bool(keys) and all(k.get("base_url") for k in keys)
    chapters = _chapters(nid)
    done = len(finals)
    now = time.time()
    by_day: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    for t in finals:
        day = time.strftime("%Y-%m-%d", time.localtime(t))
        by_day[day] = by_day.get(day, 0) + 1
        hour = time.strftime("%Y-%m-%dT%H:00", time.localtime(t))
        by_hour[hour] = by_hour.get(hour, 0) + 1
    week_ago = now - 7 * 86400
    rate7 = round(sum(1 for t in finals if t >= week_ago) / 7, 1)
    # A book's whole run is a day or two, so the working estimate is the
    # last six hours; the 7-day rate only matters for long-running books.
    rate_h = round(sum(1 for t in finals if t >= now - 6 * 3600) / 6, 1)
    left = max(0, planned - done)
    projected = None
    projected_ts = None
    if left and not inventory["blocked"]:
        if rate7:
            projected = time.strftime("%Y-%m-%d", time.localtime(now + left / rate7 * 86400))
        if rate_h:
            projected_ts = time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + left / rate_h * 3600))
    sev_all: dict[str, int] = {}
    cats: dict[str, int] = {}
    recent_pass = recent_total = 0
    must_all = recent_must = 0
    lanes: dict[str, dict] = {}
    base = ROOT / "outputs" / nid
    try:
        entries = [e for e in os.scandir(base)
                   if e.is_dir() and e.name.startswith(f"{nid}_") and e.name.rsplit("_", 1)[-1].isdigit()]
    except OSError:
        entries = []
    for entry in entries:
        directory = Path(entry.path)
        state = _episode_state(directory, h3_lane)
        review = _cached(directory / "episode_review.json", _parse_review)
        if state["review"] == "not_ready" or _mtime(directory / "episode_review.json") < state["final_mtime"]:
            review = None  # verdicts on an earlier cut do not describe the current episode
        media = _cached(directory / "thin_media_report.json", _parse_media)
        model = _cached(directory / "render.log", _parse_render_model)
        if not (review or media):
            continue
        lane = lanes.setdefault(model or "未知",
                                {"episodes": 0, "clips": 0, "attempts": 0, "sev": {}, "must_fix": 0})
        lane["episodes"] += 1
        if media:
            lane["clips"] += media["clips"]
            lane["attempts"] += media["attempts"]
        if not review:
            continue
        must_all += review.get("must_fix", 0)
        lane["must_fix"] += review.get("must_fix", 0)
        for key, value in review["sev"].items():
            sev_all[key] = sev_all.get(key, 0) + value
            lane["sev"][key] = lane["sev"].get(key, 0) + value
        for key, value in review["cats"].items():
            cats[key] = cats.get(key, 0) + value
        try:
            reviewed_at = (directory / "episode_review.json").stat().st_mtime
        except OSError:
            reviewed_at = 0
        if reviewed_at >= week_ago:
            recent_total += sum(review["sev"].get(k, 0) for k in ("pass", "minor", "fail"))
            recent_pass += review["sev"].get("pass", 0)
            recent_must += review.get("must_fix", 0)
    total = sum(sev_all.get(k, 0) for k in ("pass", "minor", "fail"))
    lane_rows = []
    for name, lane in sorted(lanes.items()):
        reviewed = sum(lane["sev"].get(k, 0) for k in ("pass", "minor", "fail"))
        lane_rows.append({
            "model": name, "episodes": lane["episodes"], "clips": lane["clips"],
            "avg_attempts": round(lane["attempts"] / lane["clips"], 2) if lane["clips"] else None,
            "pass_rate": round(100 * lane["sev"].get("pass", 0) / reviewed, 1) if reviewed else None,
            "redo_rate": round(100 * lane["must_fix"] / reviewed, 1) if reviewed else None,
            "must_fix": lane["must_fix"],
            "reviewed": reviewed,
            "review_errors": lane["sev"].get("review_error", 0),
        })
    return {
        "id": nid, "title": novel["title"], "done": done, "planned": planned, "chapters": chapters,
        "daily": sorted(by_day.items()), "hourly": sorted(by_hour.items()),
        "rate7": rate7, "projected": projected, "rate_h": rate_h, "projected_ts": projected_ts,
        "delivery": _delivery(nid),
        **{k: v for k, v in inventory.items() if k not in {"finals", "planned"}},
        "quality": {
            "clips": total, "sev": sev_all,
            "review_errors": sev_all.get("review_error", 0),
            "pass_rate": round(100 * sev_all.get("pass", 0) / total, 1) if total else None,
            "recent_rate": round(100 * recent_pass / recent_total, 1) if recent_total else None,
            "must_fix": must_all,
            "redo_rate": round(100 * must_all / total, 1) if total else None,
            "recent_redo": round(100 * recent_must / recent_total, 1) if recent_total else None,
            "cats": sorted(cats.items(), key=lambda kv: -kv[1]),
        },
        "viewer_review": viewer_progress(base, h3_lane),
        "lanes": lane_rows,
    }


def _build_board() -> None:
    try:
        data = {"now": time.strftime("%Y-%m-%d %H:%M:%S"), "novels": [_board_novel(n) for n in NOVELS]}
        _board_cache.update(at=time.time(), data=data)
    finally:
        _board_cache["building"] = False


_board_cache: dict = {"at": 0.0, "data": None, "building": False}


def board_snapshot() -> dict:
    stale = _board_cache["data"] is None or time.time() - _board_cache["at"] > BOARD_SECONDS
    if stale and not _board_cache["building"]:
        _board_cache["building"] = True
        threading.Thread(target=_build_board, daemon=True).start()
    if _board_cache["data"] is None:
        return {"building": True}
    return _board_cache["data"]


_cache: dict = {"at": 0.0, "data": None}


def cached_snapshot() -> dict:
    if time.time() - _cache["at"] > CACHE_SECONDS or _cache["data"] is None:
        _cache["data"] = snapshot()
        _cache["at"] = time.time()
    return _cache["data"]


STYLE = """
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
.num,.stat b{font-variant-numeric:tabular-nums}
header{position:sticky;top:0;z-index:10;backdrop-filter:blur(12px);
  background:#f3f4f8d9;border-bottom:1px solid var(--line);
  padding:14px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.02em}
nav{display:flex;gap:2px;background:var(--surface-2);border-radius:9px;padding:2px}
nav a{color:var(--dim);text-decoration:none;font-size:12.5px;padding:3px 12px;border-radius:7px;font-weight:600}
nav a.on{color:var(--text);background:var(--surface);box-shadow:0 1px 2px #1f243012}
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
.attn-raw{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);
  word-break:break-all;margin:2px 0 6px 18px}
.all-clear{color:var(--ok);font-size:13px}

/* tables */
.twrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.06em;text-align:left;
  padding:6px 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid #eef0f6;white-space:nowrap}
td.rng{max-width:280px;overflow:hidden;text-overflow:ellipsis;cursor:default}
tbody tr{transition:background .15s}
tbody tr:hover{background:#1f243005}
tr:last-child td{border-bottom:0}
.dim{color:var(--dim)}.warn-t{color:var(--warn)}.ok-t{color:var(--ok)}
.pills{display:flex;gap:8px;flex-wrap:wrap}

/* tooltip + charts */
#tip{display:none;position:fixed;z-index:50;pointer-events:none;background:#1f2430;color:#f2f4f8;
  font-size:12px;padding:4px 10px;border-radius:8px;box-shadow:0 4px 16px #1f243040;white-space:nowrap}
#tip.long{white-space:normal;max-width:70vw;word-break:break-all}
.sparksvg{width:100%;display:block;overflow:visible}
.sparksvg .sb{fill:url(#sbg)}
.sparksvg .sb:hover{stroke:var(--accent);stroke-width:1.2}
.sparksvg .cur{fill:var(--accent-2)}
.sparksvg .avg{stroke:var(--dim);stroke-width:1;stroke-dasharray:3 3;opacity:.55}
.burnsvg{width:100%;height:auto;display:block;overflow:visible}
.burnsvg .area{fill:url(#areag)}
.burnsvg .bline{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}
.burnsvg .scope{stroke:var(--faint);stroke-width:1;stroke-dasharray:5 4}
.burnsvg .scope.p{stroke:var(--warn)}
.burnsvg .proj{stroke:var(--accent);stroke-width:1.6;stroke-dasharray:2 3;opacity:.8}
.burnsvg .projdot{fill:var(--accent)}
.burnsvg .slab,.burnsvg .plab{font-size:10px;fill:var(--dim)}
.burnsvg .plab{fill:var(--accent);font-weight:600}
.burnsvg .xlab{font-size:10px;fill:var(--faint)}
.donutwrap{display:flex;align-items:center;gap:14px}
.ring{fill:none;stroke-width:10}
.ring.base{stroke:var(--surface-2)}
.ring.p{stroke:var(--ok)}.ring.m{stroke:var(--warn)}.ring.f{stroke:var(--bad)}
.dnum{font-size:15px;font-weight:700;fill:var(--text)}
.dlab{font-size:9px;fill:var(--dim)}
.dleg{display:grid;gap:4px;font-size:12px;color:var(--dim)}
.dleg .dot{margin-right:6px}
.catrow{display:flex;align-items:center;gap:10px;padding:3px 0;font-size:12.5px}
.catname{width:64px;color:var(--dim);flex:none}
.catbar{flex:1;height:6px;background:var(--surface-2);border-radius:99px;overflow:hidden}
.catbar i{display:block;height:100%;background:linear-gradient(90deg,var(--warn),var(--bad));border-radius:99px}
.catn{width:34px;text-align:right;color:var(--dim)}
.board2{display:grid;grid-template-columns:340px 1fr;gap:22px;margin-top:6px}

@media (max-width:820px){
  main{padding:14px 12px 32px}
  header{padding:12px 14px}
  .nbody{grid-template-columns:1fr}
  .board2{grid-template-columns:1fr}
  table.resp thead{display:none}
  table.resp, table.resp tbody, table.resp tr, table.resp td{display:block;width:100%}
  table.resp tr{border:1px solid var(--line);border-radius:10px;margin-bottom:8px;padding:6px 12px}
  table.resp td{border-bottom:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px;white-space:normal}
  table.resp td::before{content:attr(data-l);color:var(--dim);font-size:12px;flex:none}
}
"""

DEFS = """<svg width="0" height="0" style="position:absolute"><defs>
<linearGradient id="sbg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#3b6fe0"/><stop offset="1" stop-color="#3b6fe0" stop-opacity=".3"/>
</linearGradient>
<linearGradient id="areag" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#3b6fe0" stop-opacity=".22"/><stop offset="1" stop-color="#3b6fe0" stop-opacity="0"/>
</linearGradient></defs></svg>"""

TIP_JS = """const tipEl=document.getElementById("tip");
document.addEventListener("mousemove",e=>{
  const t=e.target.closest&&e.target.closest("[data-tip]");
  if(!t){tipEl.style.display="none";return;}
  tipEl.textContent=t.dataset.tip;
  tipEl.className=t.dataset.tip.length>80?"long":"";
  tipEl.style.display="block";
  const w=tipEl.offsetWidth,h=tipEl.offsetHeight;
  let x=e.clientX+12,y=e.clientY-h-10;
  if(x+w>innerWidth-8)x=Math.max(8,e.clientX-w-12);
  if(y<8)y=e.clientY+14;
  tipEl.style.left=x+"px";tipEl.style.top=y+"px";
});"""

STATE_JS = """function deliverySummary(n){
  const d = n.delivery;
  if (!d) return `<div class="nmeta"><span class="dim">交付门槛未计算（审查批次后由 delivery_gate_thin.py 写 delivery.json）</span></div>`;
  return `<div class="nmeta"><span>可交付 <b class="num ${d.deliverable===d.total?'ok-t':'warn-t'}">${d.deliverable}</b> / ${d.total} · 技术挡 ${d.tech_blocked} · 审查挡 ${d.review_blocked}（${d.must_fix_clips} 段须重拍）</span>
    <span class="dim">剧本影子门 ${d.script_flagged} 集（不阻断）· 算于 ${d.generated_at}</span></div>`;
}
function stateSummary(n){
  const s = n.states || {};
  return deliverySummary(n) + `<div class="nmeta"><span>质检合格 <b class="num ok-t">${n.done}</b> · 视频文件 ${n.files} · 已规划 ${n.planned}</span>
    <span>未过质检 ${s.done_with_warnings||0} · 待重做 ${s.stale||0} · 生成失败 ${s.clips_failed||0}</span></div>
    <div class="nmeta"><span>待审查 ${n.review_pending} · 审查失败 ${n.review_errors}</span>
    <span class="${n.blocked?'warn-t':'dim'}">需处理 ${n.blocked} 集${n.uncertain ? `（提交结果不明 ${n.uncertain} 集）` : ''}</span></div>`;
}
function episodeAttention(n){
  const rows = n.attention || [];
  if (!rows.length) return '';
  const groups = new Map();
  for (const r of rows){
    if (!groups.has(r.reason)) groups.set(r.reason, []);
    groups.get(r.reason).push(r.chapter);
  }
  return `<details><summary>待处理章节（${rows.length} 集）</summary>` +
    [...groups].map(([reason, chapters])=>`<div class="dim" style="margin-top:8px;overflow-wrap:anywhere">${reason} · ${chapters.length} 集：<span class="num">${chapters.join('、')}</span></div>`).join('') + '</details>';
}
"""


def _page(active: str, header_extra: str, body: str) -> str:
    nav = ""
    for name, href in (("实时", "/"), ("看板", "/board")):
        nav += '<a href="%s" class="%s">%s</a>' % (href, "on" if name == active else "", name)
    return ("<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>小说成片进度</title><style>" + STYLE + "</style></head><body>"
            "<header><h1>小说成片进度</h1><nav>" + nav + "</nav>" + header_extra + "</header>"
            "<script>" + STATE_JS + "</script><main>" + body + "</main>" + DEFS + "<div id=\"tip\"></div>"
            "<script>" + TIP_JS + "</script></body></html>")


LIVE_BODY = """
<div class="stats" id="stats"></div>
<div id="novels" style="display:grid;gap:16px"></div>
<div class="card" id="attention-card"><div class="label">需要关注</div><div id="attention"></div></div>
<div class="card"><div class="label">运行明细 · 通道</div><div class="twrap"><table class="resp" id="lanes"></table></div></div>
<div class="card"><div class="label">运行明细 · 单集任务</div><div class="twrap"><table class="resp" id="workers"></table></div></div>
<div class="card" id="local-card" style="display:none"><div class="label">本地 H3 · 不花钱的算力</div>
  <div class="twrap"><table class="resp" id="local"></table></div></div>
<div class="card"><div class="label">进程与在途</div><div class="pills" id="procs"></div>
  <div class="twrap" style="margin-top:12px"><table class="resp" id="inflight"></table></div></div>
<script>
const $ = id => document.getElementById(id);
const pct = (a,b) => b ? Math.min(100, a*100/b) : 0;
const fmtETA = h => h == null ? "—" : (h < 1 ? Math.round(h*60)+" 分钟" : h < 48 ? h+" 小时" : (h/24).toFixed(1)+" 天");
const fmtAgo = s => s == null ? "—" : (s < 90 ? s+" 秒前" : s < 5400 ? Math.round(s/60)+" 分钟前" : (s/3600).toFixed(1)+" 小时前");
const laneHealth = l => l.age == null ? "warn" : l.age < 1200 ? "ok" : l.age < 3600 ? "warn" : "bad";
const workerHealth = w => { const s = w.idle != null ? w.idle : w.elapsed; return s < 1200 ? "ok" : s < 2400 ? "warn" : "bad"; };
const HEALTH_TEXT = {ok:"运行正常", warn:"有待处理或等待中的任务", bad:"有异常"};
const WORST = {ok:0, warn:1, bad:2};

function spark(bars){
  const W=260, H=46, n=bars.length, gap=1.6;
  const max=Math.max(...bars,1);
  const bw=(W-gap*(n-1))/n;
  const total=bars.reduce((a,b)=>a+b,0), avg=total/n;
  const hour0=new Date(); hour0.setMinutes(0,0,0);
  const rects=bars.map((v,i)=>{
    const h=Math.max(2, v/max*(H-6));
    const s=new Date(hour0.getTime()-(n-1-i)*3600000);
    const when=i===n-1 ? "当前小时" : `${s.getMonth()+1}-${s.getDate()} ${s.getHours()}:00–${s.getHours()+1}:00`;
    return `<rect data-tip="${when} · ${v} 集" x="${(i*(bw+gap)).toFixed(2)}" y="${(H-h).toFixed(2)}" width="${bw.toFixed(2)}" height="${h.toFixed(2)}" rx="1.6" class="sb${i===n-1?" cur":""}"${v?"":' style="opacity:.18"'}></rect>`;
  }).join("");
  const avgY=(H-Math.max(2, avg/max*(H-6))).toFixed(2);
  return `<div><svg class="sparksvg" style="height:46px" viewBox="0 0 ${W} ${H}">` +
    (total?`<line class="avg" x1="0" x2="${W}" y1="${avgY}" y2="${avgY}"></line>`:"") + rects +
    `</svg><div class="spark-label">近 24 小时 · 共 ${total} 集 · 均值 ${avg.toFixed(1)}/时</div></div>`;
}

function novelCard(d, n){
  const lanes = d.lanes.filter(l => l.novel === n.title);
  const workers = d.workers.filter(w => w.novel === n.title);
  const pools = d.inflight.filter(i => i.novel === n.title);
  const health = n.review_errors ? "bad" : (n.blocked || n.attention.length) ? "warn"
    : lanes.length ? lanes.map(laneHealth).reduce((a,b)=>WORST[a]>WORST[b]?a:b) : (n.done ? "ok" : "warn");
  const last = n.last_final ? new Date(n.last_final*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}) : "—";
  const detailRows = (lanes.length + workers.length)
    ? `<table style="margin-top:8px"><tbody>` +
      lanes.map(l=>`<tr><td class="dim">${l.stage}通道</td><td class="num rng" data-tip="${l.range_full}">${l.range}</td><td>${l.mode}</td><td class="dim">${l.model||"—"}</td><td class="num">本轮 ${l.done} · 剩 ${l.total-l.covered}</td><td class="${l.age>1800?"warn-t":"dim"}">${fmtAgo(l.age)}</td></tr>`).join("") +
      workers.map(w=>`<tr><td class="dim">${w.kind}</td><td class="num">${w.what}</td><td colspan="2" class="dim">${w.detail}</td><td></td><td class="${w.elapsed>1800?"warn-t":"dim"}">已跑 ${fmtAgo(w.elapsed).replace("前","")}</td></tr>`).join("") +
      `</tbody></table>` : `<div class="dim" style="margin-top:8px;font-size:12.5px">这本书当前没有在跑的任务</div>`;
  return `<div class="card ncard">
    <div class="nrow">
      <span class="nname"><i class="dot ${health}"></i>${n.title}<span class="pill ${health}">${health==="ok"?"正常":health==="warn"?"待关注":"需处理"}</span></span>
      <span class="neta">待合格 <b>${n.left}</b> 集 · ${n.blocked ? "有待处理章节，暂无总完成时间" : `约 <b>${fmtETA(n.eta_hours)}</b>`} · 最近合格 ${last}</span>
    </div>
    <div class="nbody">
      <div>
        ${stateSummary(n)}
        <div class="nmeta"><span>全书 ${n.chapters} 章</span>
          <span>今日合格 ${n.today} · 近一小时 ${n.per_hour} 集 · 近 15 分钟折合 ${n.recent_per_hour}/时</span></div>
        <div class="track"><div class="fill" style="width:${pct(n.done,n.chapters)}%"></div>
          <div class="fill plan" style="width:${pct(n.planned-n.done,n.chapters)}%"></div></div>
        <div class="nmeta"><span>30 秒片段计划 ${n.modes["30"]} 集 · 15 秒片段计划 ${n.modes["15"]} 集</span>
          <span>${pools.map(p=>`${p.pool} ${p.slots}/${p.limit}`).join(" · ")||"无在途通道"}</span></div>
      </div>
      ${spark(n.spark)}
    </div>
    ${n.tick ? `<div class="tick">tick: ${n.tick}</div>` : ""}
    ${episodeAttention(n)}
    <details><summary>这本书的运行明细（${lanes.length + workers.length} 个在跑）</summary>${detailRows}</details>
  </div>`;
}

function attention(d){
  const items = [];
  for (const n of d.novels) if (n.attention.length)
    items.push({level: n.review_errors ? "bad" : "warn", ts: null,
      html: `${n.title} · ${n.attention.length} 集待处理，其中 ${n.blocked} 集需人工处理 · 展开小说卡片查看章节与原因`});
  for (const l of d.lanes) if (l.age != null && l.age > 3600)
    items.push({level: l.age > 7200 ? "bad" : "warn", ts: Date.now()/1000 - l.age,
      html: `${l.novel} · ${l.stage}通道 <span class="num">${l.range}</span> — ${fmtAgo(l.age)}无产出（最后在 ${l.current||"?"} 章）`});
  for (const w of d.workers) if ((w.idle != null ? w.idle : w.elapsed) > 1800)
    items.push({level: (w.idle != null ? w.idle : w.elapsed) > 3600 ? "bad" : "warn", ts: null,
      html: `${w.novel} · ${w.kind} ${w.what} 已 ${fmtAgo(w.idle != null ? w.idle : w.elapsed).replace("前","")}无产出（开跑 ${fmtAgo(w.elapsed).replace("前","")}）`});
  for (const w of d.warnings)
    items.push({level: w.level, ts: w.ts, html: `${w.novel} · ${w.kind}`, raw: w.text});
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
    <td data-l="章节" class="num rng" data-tip="${l.range_full}">${l.range}${l.current?` · 在 ${l.current}`:""}</td>
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
    const blocked = d.novels.reduce((a,n)=>a+n.blocked,0);
    const nowSec = Date.now()/1000;
    const states = [
      ...d.lanes.map(laneHealth), ...d.workers.map(workerHealth),
      ...d.novels.filter(n=>n.attention.length).map(n=>n.review_errors ? "bad" : "warn"),
      // only fresh warnings say something about right now; a 429 from hours ago doesn't
      ...d.warnings.filter(w=>w.ts && nowSec - w.ts < 7200).map(w=>w.level),
      ...(d.lanes.length||d.workers.length ? [] : ["warn"]),
    ];
    const overall = states.reduce((a,b)=>WORST[a]>WORST[b]?a:b, "ok");
    $("health").className = "health " + overall;
    $("health").innerHTML = `<i class="dot ${overall}"></i>${HEALTH_TEXT[overall]}`;
    $("stats").innerHTML =
      `<div class="stat"><div class="k">今日合格成片</div><b>${today}</b><span class="u">集</span></div>
       <div class="stat"><div class="k">近期合格成片速度</div><b>${speed}</b><span class="u">集/时</span></div>
       <div class="stat"><div class="k">在跑任务</div><b>${d.lanes.length + d.workers.length}</b><span class="u">个</span>
         <div class="sub">${d.lanes.length} 条通道 · ${d.workers.length} 个单集</div></div>
       <div class="stat"><div class="k">已规划待合格</div><b>${totalLeft}</b><span class="u">集</span>
         <div class="sub">${blocked ? `${blocked} 集需处理，暂无总完成时间` : `按近期速度约 ${fmtETA(speed ? Math.round(totalLeft/speed*10)/10 : null)}`}</div></div>`;
    $("novels").innerHTML = d.novels.map(n=>novelCard(d,n)).join("");
    $("attention").innerHTML = attention(d);
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
    const L = d.local || [];
    $("local-card").style.display = L.length ? "" : "none";
    if (L.length) {
      const secs = L.reduce((a,l)=>a+l.seconds_hour,0), made = L.reduce((a,l)=>a+l.done_hour,0);
      $("local").innerHTML = `<thead><tr><th>机器与显卡</th><th>实例</th><th>小说</th><th>状态</th><th>待处理</th>` +
        `<th>近一小时片段</th><th>近一小时视频秒数</th><th>平均每段耗时</th></tr></thead><tbody>` +
        L.map(l=>`<tr><td data-l="机器与显卡"><b>${l.where || "—"}</b></td>
          <td data-l="实例" class="dim">${l.name}</td><td data-l="小说">${l.novel}</td>
          <td data-l="状态" class="${l.alive?"ok-t":"err"}">${l.alive?"在线":"离线"}</td>
          <td data-l="待处理" class="num">${l.pending}</td>
          <td data-l="片段" class="num">${l.done_hour}</td>
          <td data-l="视频秒数" class="num">${Math.round(l.seconds_hour)}</td>
          <td data-l="耗时" class="num">${l.avg_take==null?"—":l.avg_take+" 秒"}</td></tr>`).join("") +
        `<tr><td class="dim">合计</td><td class="dim"></td><td class="dim"></td><td class="dim"></td><td class="dim"></td>` +
        `<td class="num"><b>${made}</b></td><td class="num"><b>${Math.round(secs)}</b></td><td class="dim"></td></tr>` +
        `</tbody>`;
    }
    const ageSec = Math.max(0, Math.round((Date.now() - new Date(d.now.replace(" ","T")))/1000));
    $("stamp").className = "";
    $("stamp").textContent = `数据 ${ageSec} 秒前 · 每 20 秒刷新`;
  }).catch(e=>{ $("stamp").textContent = "读取失败：" + e; $("stamp").className = "err"; });
}
tick(); setInterval(tick, 20000);
</script>"""

BOARD_BODY = """
<div id="board" style="display:grid;gap:16px"></div>
<script>
const DAY = 86400000;
const mdT = t => { const d = new Date(t); return (d.getMonth()+1)+"/"+d.getDate(); };
const mdHm = t => { const d = new Date(t); return (d.getMonth()+1)+"/"+d.getDate()+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0"); };

function barsSVG(items, W, H){
  const n = items.length || 1, gap = Math.min(3, (W/n)*0.25), bw = Math.max(1, (W-gap*(n-1))/n);
  const max = Math.max(...items.map(i=>i.v), 1);
  const rects = items.map((it,i)=>{
    const h = Math.max(2, it.v/max*(H-6));
    return `<rect data-tip="${it.tip}" x="${(i*(bw+gap)).toFixed(2)}" y="${(H-h).toFixed(2)}" width="${bw.toFixed(2)}" height="${h.toFixed(2)}" rx="1.6" class="sb"${it.v?"":' style="opacity:.18"'}></rect>`;
  }).join("");
  return `<svg class="sparksvg" style="height:${H}px" viewBox="0 0 ${W} ${H}">${rects}</svg>`;
}

function burnup(n, series, projT, stepMs, fmt){
  if (!series.length) return `<div class="dim" style="padding:8px 0">还没有成片数据</div>`;
  const W=760, H=210, pl=8, pr=64, pt=16, pb=26;
  const first = series[0][0], last = series[series.length-1][0];
  const xEnd = Math.max(last + stepMs, projT || 0);
  const X = t => pl + (t-first)/((xEnd-first)||1)*(W-pl-pr);
  const yMax = Math.max(n.chapters||0, n.planned||0, series[series.length-1][1], 1) * 1.06;
  const Y = v => pt + (1 - v/yMax)*(H-pt-pb);
  const line = series.map((p,i) => (i?"L":"M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ");
  const area = line + ` L${X(last).toFixed(1)},${Y(0).toFixed(1)} L${X(first).toFixed(1)},${Y(0).toFixed(1)} Z`;
  let proj = "";
  if (projT){
    const done = series[series.length-1][1];
    proj = `<line class="proj" x1="${X(last).toFixed(1)}" y1="${Y(done).toFixed(1)}" x2="${X(projT).toFixed(1)}" y2="${Y(n.planned).toFixed(1)}"></line>
      <circle class="projdot" cx="${X(projT).toFixed(1)}" cy="${Y(n.planned).toFixed(1)}" r="3"></circle>
      <text class="plab" x="${X(projT).toFixed(1)}" y="${(Y(n.planned)-8).toFixed(1)}" text-anchor="middle">预计 ${fmt(projT)}</text>`;
  }
  const scope = (v,lab,cls) => v ? `<line class="${cls}" x1="${pl}" x2="${W-pr}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"></line>
    <text class="slab" x="${W-pr+6}" y="${(Y(v)+3).toFixed(1)}">${lab} ${v}</text>` : "";
  const ticks = [...new Set([first, series[Math.floor(series.length/2)][0], last, ...(projT?[projT]:[])])]
    .map(t => `<text class="xlab" x="${X(t).toFixed(1)}" y="${H-8}" text-anchor="middle">${fmt(t)}</text>`).join("");
  return `<svg class="burnsvg" viewBox="0 0 ${W} ${H}">
    ${scope(n.chapters,"全书","scope")}${n.planned && n.planned !== n.chapters ? scope(n.planned,"已规划","scope p") : ""}
    <path class="area" d="${area}"></path><path class="bline" d="${line}"></path>${proj}${ticks}</svg>`;
}

function donut(sev){
  const p = sev.pass||0, m = sev.minor||0, f = sev.fail||0, t = (p+m+f)||1;
  const C = 2*Math.PI*34;
  let off = 0;
  const seg = (v,cls) => {
    if (!v) return "";
    const len = v/t*C;
    const s = `<circle class="ring ${cls}" cx="42" cy="42" r="34" transform="rotate(-90 42 42)" stroke-dasharray="${len.toFixed(1)} ${(C-len).toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}"></circle>`;
    off += len; return s;
  };
  return `<div class="donutwrap"><svg width="84" height="84" viewBox="0 0 84 84">
    <circle class="ring base" cx="42" cy="42" r="34"></circle>${seg(p,"p")}${seg(m,"m")}${seg(f,"f")}
    <text class="dnum" x="42" y="40" text-anchor="middle">${p+m+f ? (100*p/t).toFixed(1)+"%" : "—"}</text>
    <text class="dlab" x="42" y="54" text-anchor="middle">通过</text></svg>
    <div class="dleg"><span><i class="dot ok"></i>通过 ${p}</span><span><i class="dot warn"></i>轻微问题 ${m}</span><span><i class="dot bad"></i>不通过 ${f}</span><span>审查失败 ${sev.review_error||0}（未计入通过率）</span></div></div>`;
}

function redoPanel(q){
  if (!q.clips) return `<div class="dim">还没有当前版本的审查数据</div>`;
  const r = q.redo_rate;
  const cls = r == null ? "dim" : r >= 15 ? "err" : r >= 8 ? "warn-t" : "ok-t";
  const recent = q.recent_redo == null ? "" : ` · 近 7 天 ${q.recent_redo}%`;
  return `<div style="margin:10px 0 4px"><b class="${cls}" style="font-size:30px">${r == null ? "—" : r+"%"}</b>
    <span class="dim" style="margin-left:8px">${q.must_fix} / ${q.clips} 段${recent}</span></div>
    <div class="dim" style="font-size:12px;margin-bottom:10px">其余 ${q.clips - q.must_fix} 段无需重拍${q.review_errors ? `；另有 ${q.review_errors} 段审查失败，未计入` : ""}。</div>`;
}

function catRow(name, v, maxV){
  return `<div class="catrow"><span class="catname">${name}</span><span class="catbar"><i style="width:${(v/maxV*100).toFixed(1)}%"></i></span><span class="num catn">${v}</span></div>`;
}

function laneTable(lanes){
  return `<table><thead><tr><th>最近运行模型</th><th>集数</th><th>片段数</th><th>平均尝试</th><th>需重拍比例</th></tr></thead><tbody>` +
    lanes.map(l=>`<tr><td>${l.model}</td><td class="num">${l.episodes}</td><td class="num">${l.clips}</td>
      <td class="num">${l.avg_attempts == null ? "—" : l.avg_attempts}</td>
      <td class="num">${l.redo_rate == null ? "—" : l.redo_rate+"%"} <span class="dim">(${l.must_fix} / ${l.reviewed} 段已审 · 严格比对通过 ${l.pass_rate == null ? "—" : l.pass_rate+"%"}${l.review_errors ? ` · ${l.review_errors} 段审查失败` : ""})</span></td></tr>`).join("") +
    `</tbody></table>`;
}

function viewerReview(v){
  if (!v) return `<div class="card"><div class="label">明显画面错误 · 复审与修复验收</div><div class="dim">尚无这套独立复审记录。上面的设定一致性通过率不能当作修复后的剩余问题比例。</div></div>`;
  const c = v.counts, r = v.repair_counts;
  const esc = text => String(text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const status = {confirmed:'仍确认有问题', clear:'复审未发现明显错误', one_vote:'单方存疑', needs_review:'待当前版本复审'};
  const remaining = v.rows.filter(row=>row.status!=='clear');
  const groups = ['confirmed','one_vote','needs_review'].map(key=>{
    const rows = remaining.filter(row=>row.status===key);
    if (!rows.length) return '';
    return `<details><summary>${status[key]}（${rows.length} 段）</summary>` + rows.map(row=>
      `<div class="dim" style="margin-top:8px;white-space:normal"><b>第 ${row.chapter} 集 · ${row.clip}</b> · ${esc(row.kind)}<br>${esc(row.status==='needs_review' ? row.reason : row.observation)}</div>`).join('') + '</details>';
  }).join('');
  return `<div class="card" style="box-shadow:none"><div class="label">明显画面错误 · 复审与修复验收</div>
    <div class="nmeta"><span>当前仍确认 <b class="warn-t">${c.confirmed||0}</b> 段 · 单方存疑 ${c.one_vote||0} 段 · 待复审 ${c.needs_review||0} 段</span>
    <span>跟踪范围内复审未发现明显错误 ${c.clear||0} 段</span></div>
    <div class="dim" style="font-size:12px;margin:8px 0">跟踪 ${v.tracked} 段历史候选和修复验收片段，不是全书错误率。当前结论核对了成片所选片段、视频更新时间和拆分编号；没有复审不算通过。</div>
    <div class="nmeta"><span>已保存修复验收 ${v.repair_checks} 段：当前有效的 ${r.clear||0} 段未发现明显错误、${r.confirmed||0} 段仍确认、${r.one_vote||0} 段存疑；${r.needs_review||0} 段需重验</span></div>
    <div class="dim" style="font-size:12px;margin:8px 0">历史清单 ${v.baseline_at||'—'}：双方确认 ${v.baseline_confirmed} 段、单方存疑 ${v.baseline_one_vote} 段。设定一致性与明显错误复审的标准不同，比例不能直接比较。</div>
    ${groups}</div>`;
}

function boardCard(n){
  const q = n.quality;
  // a book's whole run is a day or two, so short runs switch to hourly granularity
  const hourlyMode = n.daily.length <= 3;
  const src = hourlyMode ? n.hourly : n.daily;
  let cum = 0;
  const series = src.map(([k,c]) => { cum += c; return [Date.parse(k), cum]; });
  const projT = hourlyMode ? (n.projected_ts ? Date.parse(n.projected_ts) : null)
                           : (n.projected ? Date.parse(n.projected+"T00:00:00") : null);
  const fmt = hourlyMode ? mdHm : mdT;
  const stepMs = hourlyMode ? 3600000 : DAY;
  const bars = src.map(([k,c]) => ({v:c, tip: fmt(Date.parse(k)) + " · " + c + " 集"}));
  const etaTxt = n.blocked ? `${n.blocked} 集需处理，暂无总完成时间`
    : n.done > 0 && n.done >= n.planned ? (n.review_pending || n.review_errors ? "已规划部分质检合格，仍有审查待完成" : "已规划部分质检合格，审查已完成")
    : hourlyMode
    ? (n.projected_ts ? `按近 6 小时 <b>${n.rate_h}</b> 集/时，已规划部分预计 <b>${mdHm(Date.parse(n.projected_ts))}</b> 完成` : "暂无投影")
    : (n.projected ? `按近 7 天 <b>${n.rate7}</b> 集/天，已规划部分预计 <b>${mdT(Date.parse(n.projected+"T00:00:00"))}</b> 完成` : "暂无投影");
  const recent = q.recent_rate == null ? "" :
    ` · 近 7 天 <b class="${q.pass_rate != null && q.recent_rate >= q.pass_rate ? "ok-t" : "warn-t"}">${q.recent_rate}%</b>`;
  const cats = q.cats.length ? q.cats.map(([k,v]) => catRow(k, v, q.cats[0][1])).join("")
    : `<div class="dim">没有被判失败的类别</div>`;
  return `<div class="card ncard">
    <div class="nrow"><span class="nname">${n.title}</span>
      <span class="neta">${etaTxt}</span></div>
    ${stateSummary(n)}
    <div class="dim" style="font-size:12px">产量与速度只计当前质检合格的成片，按最近合成时间统计；重做后日期会更新。审查统计只使用当前版本的审查结果。</div>
    ${burnup(n, series, projT, stepMs, fmt)}
    ${bars.length ? barsSVG(bars, 760, 64) : ""}
    ${viewerReview(n.viewer_review)}
    <div class="board2">
      <div><div class="label">需要重拍的片段</div>
        <div class="dim" style="font-size:12px;margin-bottom:8px">只算观众看得出来的问题：肢体结构错误、主角画成别人、该在场的角色不见了。服装、发色、光线时段这类与设定卡的出入不计入，它们在下面的明细里。</div>
        ${redoPanel(q)}
        <details><summary>设定一致性明细（严格比对，多数不必重拍）</summary>
          <div class="dim" style="font-size:12px;margin:8px 0">逐段对照人物卡、地点和时段，含服装、发型等细节差异。这里的“不通过”只表示与卡片有出入，不代表成片有明显问题。</div>
          ${q.clips || q.review_errors ? donut(q.sev) : `<div class="dim">还没有当前版本的审查数据</div>`}
          <div class="dim" style="margin:8px 0 10px;font-size:12.5px">严格比对通过率 ${q.pass_rate == null ? "—" : q.pass_rate+"%"}（${q.clips} 段）${recent}</div>
          ${cats}
        </details></div>
      <div><div class="label">按最近运行模型汇总</div>
        ${n.lanes.length ? laneTable(n.lanes) : `<div class="dim">暂无</div>`}</div>
    </div>${episodeAttention(n)}</div>`;
}

function load(){
  fetch("board.json", {cache:"no-store"}).then(r=>r.json()).then(d=>{
    if (d.building){
      document.getElementById("board").innerHTML = `<div class="card dim">首次统计要扫一遍每集的审查和渲染报告，十几秒到一分钟，好了会自动出来…</div>`;
      setTimeout(load, 3000); return;
    }
    document.getElementById("board").innerHTML = d.novels.map(boardCard).join("");
    document.getElementById("stamp").textContent = `统计于 ${d.now} · 每 5 分钟重算`;
    setTimeout(load, 300000);
  }).catch(e=>{ const s=document.getElementById("stamp"); s.textContent="读取失败："+e; s.className="err"; setTimeout(load, 20000); });
}
load();
</script>"""

PAGE = _page("实时",
             '<span class="health" id="health"><i class="dot idle"></i>读取中</span><span id="stamp">加载中…</span>',
             LIVE_BODY)
PAGE_BOARD = _page("看板", '<span id="stamp"></span>', BOARD_BODY)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.path.startswith("/status.json"):
            body = json.dumps(cached_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path.startswith("/board.json"):
            body = json.dumps(board_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif self.path == "/board":
            body = PAGE_BOARD.encode("utf-8")
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
    _board_cache["building"] = True
    threading.Thread(target=_build_board, daemon=True).start()  # warm the board cache
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
