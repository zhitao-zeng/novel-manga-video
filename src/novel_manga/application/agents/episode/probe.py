"""First-take cost probe: one minimal real generation per novel per day.

A paid lane (sd2.5/sd2.0 behind PhanRouter) is billed for the resolution the
service actually renders, which is not guaranteed to be the tier the request
named.  Before an episode's first paid submission of the day, the probe sends
one 4 s fixed-content clip at the profile's declared resolution, ffprobes what
came back and appends the verdict to <novel>/probe-log.jsonl; render() refuses
to film when the two disagree, because every clip of the episode would be
billed at the wrong tier.

The local H3 lane bills nothing, so its probe is a throughput baseline
instead: one 5 s clip, wall-clock timed, recorded in the same log.

NOVEL_PROBE=0 disables probing. Errors remain advisory and count as an attempted
check for this book/date/submission configuration; known resolution mismatches
remain blocking. Actual providers invoke the check only before a new task.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from novel_manga.application.configuration import environment, project_root
from novel_manga.planning.tier_presets import effective_profile
from novel_manga.util import read_json, run

PAID_VIDEO_MODELS = {"sd2.5", "sd2.0"}
PROBE_PROMPT = "昏暗房间,一盏钨丝灯闪烁,一座座钟滴答作响,横屏16:9"
PROBE_SECONDS = 4
H3_PROBE_SECONDS = 5
LOG_NAME = "probe-log.jsonl"
PHANROUTER_DEFAULT_BASE = "https://cloud.phanthy.com/phanrouter"
VIDEO_TASKS_PATH = "/api/v3/contents/generations/tasks"
POLL_SECONDS = 5.0
# A tier names its short edge, and the service may render a few lines over
# (sd2.0 answers 480p with 864x496); a wholesale upgrade is the mismatch.
SHORT_EDGE_TOLERANCE = 0.12


def _today() -> str:
    return datetime.date.today().isoformat()


def _log_path(ctx) -> Path:
    return Path(getattr(ctx, 'source_novel_dir', None) or ctx.novel_dir) / LOG_NAME


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _cached_probe(path: Path, key: dict) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        try:
            row = json.loads(line)
            if all(row.get(name) == value for name, value in key.items()):
                found = row
        except ValueError:
            continue
    return found


def _video_model(env) -> str:
    return (env.get("NOVEL_VIDEO_MODEL") or env.get("PHANROUTER_VIDEO_MODEL") or "sd2.5").strip()


def _lane(env) -> str | None:
    if (env.get("NOVEL_LOCAL_H3_URL") or "").strip():
        return "local_h3"
    return "paid" if _video_model(env) in PAID_VIDEO_MODELS else None


def _request_tier(ctx) -> tuple[str, str]:
    """The resolution tier and ratio this novel's profile declares."""
    profile = getattr(ctx, 'profile', None) or read_json(ctx.novel_dir / "profile.json", {}) or {}
    resolution = effective_profile(profile)["resolution"]
    ratio = str(profile.get("frame") or "9:16")
    return resolution, ratio if ratio in {"9:16", "16:9"} else "9:16"


def _resolution_match(requested: str, actual: str) -> bool:
    try:
        tier = int(str(requested).strip().lower().removesuffix("p"))
        width, height = (int(part) for part in str(actual).strip().lower().split("x"))
    except ValueError:
        return False
    return abs(min(width, height) - tier) <= tier * SHORT_EDGE_TOLERANCE


def _actual_resolution(path: Path) -> str:
    result = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    return result.stdout.strip()


def _submit_paid(env, output: Path, *, model: str, ratio: str, resolution: str,
                 seconds: int, timeout: float) -> Path:
    key_var = (env.get("PHANROUTER_VIDEO_KEY_VAR") or "").strip() or "PHANROUTER_API_KEY"
    key = env.get(key_var) or ""
    if not key:
        raise ValueError(f"{key_var} not set")
    base = (env.get("PHANROUTER_BASE_URL") or PHANROUTER_DEFAULT_BASE).rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}
    payload = {"model": model,
               "content": [{"type": "text", "text": PROBE_PROMPT}],
               "ratio": ratio, "resolution": resolution, "duration": seconds,
               "generate_audio": False, "watermark": False, "output_format": "mp4"}
    from novel_manga.providers.downloads import download_file
    with httpx.Client(timeout=float(env.get("NOVEL_REQUEST_TIMEOUT") or 180), trust_env=False) as client:
        response = client.post(f"{base}{VIDEO_TASKS_PATH}", headers=headers, json=payload)
        response.raise_for_status()
        task_id = response.json().get("task_id") or response.json().get("id")
        if not task_id:
            raise ValueError("video API returned no task_id")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status_response = client.get(f"{base}{VIDEO_TASKS_PATH}/{task_id}", headers=headers)
            status_response.raise_for_status()
            data = status_response.json().get("data") or status_response.json()
            status = str(data.get("status", "")).lower()
            if status in {"succeeded", "success"}:
                url = data.get("url") or data.get("video_url")
                if not url:
                    raise ValueError("successful probe task returned no URL")
                download_file(client, str(url), output)
                return output
            if status in {"failed", "failure", "cancelled"}:
                raise RuntimeError(f"probe video generation failed: {data}")
            time.sleep(POLL_SECONDS)
    raise TimeoutError(f"probe video task timed out: {task_id}")


def _submit_h3(env, output: Path, *, model: str, ratio: str, seconds: int, timeout: float) -> Path:
    """One clip through the lane's own provider, so a pool URL behaves as it does in render."""
    from novel_manga.config import Settings
    from novel_manga.providers.local_h3 import LocalH3MediaProvider
    base = (env.get("NOVEL_LOCAL_H3_URL") or "").strip()
    settings = Settings(video_model=model,
                        request_timeout=float(env.get("NOVEL_REQUEST_TIMEOUT") or 180),
                        poll_timeout=timeout)
    provider = LocalH3MediaProvider(settings, base, ratio=ratio)
    return provider.create_video(PROBE_PROMPT, None, output, float(seconds))


def _submit_probe(ctx, env, *, lane: str, model: str, ratio: str, resolution: str,
                  seconds: int) -> Path:
    """Submit the probe clip and wait for its file; tests replace this, never the network."""
    directory = getattr(ctx, 'run_dir', None) or ctx.episode_dir
    output = directory / "probe" / f"{_today()}-{lane}.mp4"
    timeout = float(env.get("NOVEL_PROBE_TIMEOUT") or env.get("NOVEL_POLL_TIMEOUT") or 900)
    if lane == "local_h3":
        return _submit_h3(env, output, model=model, ratio=ratio, seconds=seconds, timeout=timeout)
    return _submit_paid(env, output, model=model, ratio=ratio, resolution=resolution,
                        seconds=seconds, timeout=timeout)


def _submission_environment(ctx, environ):
    env = environment(project_root()) if environ is None else dict(environ)
    settings = getattr(ctx, 'settings', None)
    if settings is not None:
        env.update(NOVEL_VIDEO_MODEL=settings.video_model,
                   NOVEL_LOCAL_H3_URL=settings.local_h3_base_url or '',
                   PHANROUTER_BASE_URL=settings.phanrouter_base_url,
                   NOVEL_REQUEST_TIMEOUT=str(settings.request_timeout),
                   NOVEL_POLL_TIMEOUT=str(settings.poll_timeout))
        key_var = (env.get('PHANROUTER_VIDEO_KEY_VAR') or '').strip() or 'PHANROUTER_API_KEY'
        env[key_var] = settings.phanrouter_api_key or ''
    return env


def _endpoint(base):
    if base == 'pool' or base.startswith('pool:'):
        return base
    url = urlsplit(base)
    # Scope the result by route without putting credentials or query tokens in logs.
    return f'{url.scheme}://{url.hostname}:{url.port or (443 if url.scheme == "https" else 80)}{url.path.rstrip("/")}'


def check_before_submit(ctx):
    """Called only when a real provider is about to submit a new video task."""
    try:
        verdict = maybe_probe(ctx)
    except Exception:
        # Failed probes are recorded by maybe_probe; preserve the existing
        # advisory policy for unavailable measurements, not known mismatches.
        return
    if verdict and verdict.get('match') is False:
        raise RuntimeError(f'resolution_mismatch: requested {verdict["requested"]}, actual {verdict["actual"]}')


def maybe_probe(ctx, *, environ=None) -> dict | None:
    """Run the day's first-take probe when this lane and date call for one.

    Returns {"match": bool, "requested": str, "actual": str} when a probe ran,
    None when disabled or an already-recorded non-blocking attempt applies.
    A recorded mismatch is returned again. Submission failures are recorded
    without provider response bodies and raised; the provider hook treats these
    unavailable measurements as advisory, preserving the existing policy.
    """
    env = _submission_environment(ctx, environ)
    if (env.get("NOVEL_PROBE") or "").strip() == "0":
        return None
    lane = _lane(env)
    if lane is None:
        return None
    log_path = _log_path(ctx)
    resolution, ratio = _request_tier(ctx)
    model = _video_model(env)
    base = env['NOVEL_LOCAL_H3_URL'] if lane == 'local_h3' else env.get('PHANROUTER_BASE_URL') or PHANROUTER_DEFAULT_BASE
    key = {'date':_today(), 'lane':lane, 'model':model, 'endpoint':_endpoint(base),
           'resolution':resolution, 'ratio':ratio,
           'key_var':(env.get('PHANROUTER_VIDEO_KEY_VAR') or 'PHANROUTER_API_KEY') if lane=='paid' else None}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from novel_manga.resource_waits import QueueWait
    with log_path.with_suffix('.lock').open('a') as lock:
        with QueueWait('book_probe') as wait:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                wait.begin()
                fcntl.flock(lock, fcntl.LOCK_EX)
        cached = _cached_probe(log_path, key)
        if cached:
            if cached.get('match') is False:
                return {name:cached[name] for name in ('match','requested','actual')}
            return None
        # Reserve this attempt before submission, so an interrupted paid probe
        # is not bought a second time by the next chapter.
        _append(log_path, {**key, 'status':'started'})
        try:
            started = time.monotonic()
            output = _submit_probe(ctx, env, lane=lane, model=model, ratio=ratio,
                                   resolution=resolution, seconds=H3_PROBE_SECONDS if lane=='local_h3' else PROBE_SECONDS)
            if lane == 'local_h3':
                elapsed = round(time.monotonic()-started, 3)
                _append(log_path, {**key, 'status':'complete','seconds':H3_PROBE_SECONDS,'elapsed_s':elapsed})
                return {'match':True,'requested':f'{H3_PROBE_SECONDS}s throughput','actual':f'{elapsed}s'}
            actual = _actual_resolution(output)
            verdict = {'match':_resolution_match(resolution, actual),'requested':resolution,'actual':actual}
            _append(log_path, {**key, 'status':'complete', **verdict})
            return verdict
        except Exception as error:
            # Provider error strings can contain authenticated download URLs.
            _append(log_path, {**key, 'status':'error', 'error':type(error).__name__})
            raise
