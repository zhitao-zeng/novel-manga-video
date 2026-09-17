#!/usr/bin/env python3
"""Run matched Seedance speed probes without changing the production backend.

The experiment manifest fixes cases, model IDs, parameters and submission budget.
Each case runs once per model concurrently; cases run in successive paired rounds.
Existing task IDs are polled on resume. Ambiguous submissions are never repeated.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
import time

import httpx

from novel_manga.media.common import audio_levels
from novel_manga.util import atomic_write_json
sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_manga.media.speech import MIN_PEAK_DB, MAX_MISSING
from novel_manga.runtime_backends import edit_distance
from novel_manga.media.subtitles import match_key, subsequence_overlap


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[{stamp()}] {message}", flush=True)


def sanitized(value: object) -> str:
    text = str(value)
    for key in (os.getenv("PHANROUTER_API_KEY"), os.getenv("PHANROUTER_IMAGE_API_KEY")):
        if key:
            text = text.replace(key, "[redacted]")
    return re.sub(r"https?://[^\s\"'<>]+", "[URL]", text)[:500]


def task_data(data: dict) -> dict:
    result = data.get("Result") or data.get("data") or data
    return result if isinstance(result, dict) else data


def payload_for(root: Path, config: dict, case: dict, model: str) -> dict:
    content = [{"type": "text", "text": case["prompt"]}]
    for ref in case["references"]:
        path = root / ref["path"]
        voice = ref["role"] == "voice"
        kind, mime, role = ("audio_url", "audio/wav", "reference_audio") if voice else ("image_url", "image/jpeg", "reference_image")
        content.append({"type": kind, kind: {"url": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")}, "role": role})
    return {"model": model, "content": content, **config["parameters"]}


def download(url: str, path: Path) -> None:
    # Generated media uses a direct connection, not the development proxy.
    partial = path.with_suffix(".partial.mp4")
    with httpx.Client(timeout=90, trust_env=False, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
    partial.replace(path)


def evaluate(root: Path, config: dict, case: dict, directory: Path) -> dict:
    video = directory / "video.mp4"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)], capture_output=True, text=True, check=True)
    media = json.loads(probe.stdout)
    atomic_write_json(directory / "media.json", media)
    videos = [s for s in media["streams"] if s.get("codec_type") == "video"]
    audios = [s for s in media["streams"] if s.get("codec_type") == "audio"]
    duration = float(media["format"]["duration"])
    result = {"duration_seconds": duration, "video_codec": videos[0].get("codec_name") if videos else None,
              "audio_codec": audios[0].get("codec_name") if audios else None,
              "width": videos[0].get("width") if videos else None, "height": videos[0].get("height") if videos else None,
              "fps": videos[0].get("r_frame_rate") if videos else None,
              "requested_resolution_exact": bool(videos and videos[0].get("height") == 480),
              "media_passed": bool(videos and audios and videos[0].get("width", 0) > 0 and videos[0].get("height", 0) > 0 and abs(duration - config["parameters"]["duration"]) <= 1)}
    if not audios:
        return {**result, "usable": False, "speech_passed": False}
    wav = directory / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ar", "16000", "-ac", "1", str(wav)], check=True, capture_output=True)
    command = shlex.split(os.environ["NOVEL_ASR_COMMAND"])
    asr_path = directory / "asr.json"
    if not asr_path.is_file():
        subprocess.run([*command, "--unit-id", directory.name, "--audio", str(wav), "--text", case["spoken_text"], "--output", str(asr_path)], check=True, capture_output=True, timeout=120)
    asr = json.loads(asr_path.read_text())
    ref, hyp = match_key(case["spoken_text"]), match_key(asr["hypothesis"])
    missing = 1 - subsequence_overlap(ref, hyp) / max(1, len(ref))
    mean_db, peak_db = audio_levels(wav)
    speech_passed = bool(hyp and peak_db is not None and peak_db >= MIN_PEAK_DB and missing <= MAX_MISSING)
    return {**result, "reference": case["spoken_text"], "hypothesis": asr["hypothesis"], "asr_backend": asr.get("backend"),
            "cer": edit_distance(ref, hyp) / max(1, len(ref)), "missing_fraction": missing,
            "mean_db": mean_db, "peak_db": peak_db, "speech_passed": speech_passed,
            "usable": result["media_passed"] and speech_passed}


def run_one(root: Path, config: dict, case: dict, model: str, deadline: float) -> dict:
    directory = root / "runs" / case["id"] / model
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / "result.json"
    row = json.loads(record_path.read_text()) if record_path.is_file() else {"case": case["id"], "model": model}
    if row.get("status") in {"completed", "submission_rejected", "submission_unknown", "provider_failed", "model_unavailable"}:
        return row
    if row.get("status") == "submitting" and not row.get("task_id"):
        row.update(status="submission_unknown", error="Prior submission has no saved task ID; do not resubmit.")
        atomic_write_json(record_path, row)
        return row
    base = os.getenv("PHANROUTER_BASE_URL", "https://cloud.phanthy.com/phanrouter").rstrip("/")
    credential_env = config.get("credential_env_by_model", {}).get(model, "PHANROUTER_API_KEY")
    headers = {"Authorization": "Bearer " + os.environ[credential_env]}
    row["credential_env"] = credential_env
    with httpx.Client(timeout=90) as client:
        try:
            if not row.get("task_id"):
                payload = payload_for(root, config, case, model)
                row.update(status="submitting", submitted_at=stamp(), submitted_epoch=time.time())
                atomic_write_json(record_path, row)
                atomic_write_json(directory / "request.json", {"model": model, **config["parameters"], "case": case["id"], "references": case["references"], "prompt": case["prompt"]})
                response = client.post(base + "/api/v3/contents/generations/tasks", headers=headers, json=payload)
                row["submission_seconds"] = time.time() - row["submitted_epoch"]
                if response.status_code >= 400:
                    row.update(status="submission_rejected", http_status=response.status_code, error=sanitized(response.text))
                    atomic_write_json(record_path, row)
                    log(f"{case['id']} / {model}: submission rejected ({response.status_code})")
                    return row
                submitted = task_data(response.json())
                task_id = submitted.get("task_id") or submitted.get("id")
                if not task_id:
                    raise ValueError("Successful HTTP submission did not return a task ID")
                row.update(status="submitted", task_id=task_id, accepted_at=stamp())
                atomic_write_json(record_path, row)
                log(f"{case['id']} / {model}: accepted")
            cutoff = min(deadline, row["submitted_epoch"] + config["task_timeout_seconds"])
            previous_status = None
            while time.time() < cutoff:
                response = client.get(base + "/api/v3/contents/generations/tasks/" + row["task_id"], headers=headers, timeout=30)
                response.raise_for_status()
                data = task_data(response.json())
                status = str(data.get("status", "")).lower()
                observed = time.time()
                if status != previous_status:
                    row.setdefault("transitions", []).append({"status": status, "at": stamp(), "seconds_since_submit": observed - row["submitted_epoch"]})
                    previous_status = status
                    row["provider_status"] = status
                    if status in {"processing", "running"} and "first_processing_seconds" not in row:
                        row["first_processing_seconds"] = observed - row["submitted_epoch"]
                    atomic_write_json(record_path, row)
                    log(f"{case['id']} / {model}: {status} after {observed - row['submitted_epoch']:.0f}s")
                if data.get("model"):
                    row["returned_model"] = data["model"]
                if data.get("usage"):
                    row["usage"] = data["usage"]
                if status in {"failed", "failure", "cancelled", "canceled"}:
                    row.update(status="provider_failed", error=sanitized(data.get("error") or data.get("message") or status))
                    break
                if status in {"success", "succeeded", "completed"}:
                    row.setdefault("ready_seconds", observed - row["submitted_epoch"])
                    row["ready_at"] = stamp()
                    video = directory / "video.mp4"
                    if not video.is_file():
                        url = data.get("url") or data.get("video_url") or (data.get("content") or {}).get("video_url")
                        if not url:
                            raise ValueError("Completed task has no video URL")
                        before = time.monotonic()
                        download(url, video)
                        row["download_seconds"] = time.monotonic() - before
                    row["video_bytes"] = video.stat().st_size
                    row["downloaded_at"] = stamp()
                    row["end_to_end_seconds"] = time.time() - row["submitted_epoch"]
                    row["status"] = "downloaded"
                    atomic_write_json(record_path, row)
                    row["quality"] = evaluate(root, config, case, directory)
                    row.update(status="completed", evaluated_at=stamp())
                    log(f"{case['id']} / {model}: complete, ready {row['ready_seconds']:.1f}s, usable={row['quality']['usable']}")
                    break
                time.sleep(config["poll_interval_seconds"])
            else:
                row.update(status="timed_out", error="Polling time budget reached; retain the task ID without resubmitting.")
        except Exception as error:
            row.update(status="submission_unknown" if not row.get("task_id") else "interrupted", error=f"{type(error).__name__}: {sanitized(error)}")
            log(f"{case['id']} / {model}: {row['status']} ({type(error).__name__})")
        atomic_write_json(record_path, row)
        return row


def summarize(root: Path, config: dict) -> dict:
    rows = [json.loads(p.read_text()) for p in sorted((root / "runs").glob("*/*/result.json"))]
    matched_cases = {c["id"] for c in config["cases"] if c.get("comparable", True)}
    summary = {"updated_at": stamp(), "evidence_level": "probe", "cases": len(config["cases"]), "matched_cases": sorted(matched_cases), "models": {}, "rows": rows,
               "comparability_note": config.get("comparability_note", "Matched inputs, resolution, duration and concurrency.")}
    for model in config["models"]:
        own = [r for r in rows if r["model"] == model]
        completed = [r for r in own if r.get("status") == "completed"]
        matched = [r for r in completed if r["case"] in matched_cases]
        ready = [r["ready_seconds"] for r in matched]
        summary["models"][model] = {"recorded": len(own), "accepted": sum(bool(r.get("task_id")) for r in own),
                                    "completed": len(completed), "usable": sum(r["quality"]["usable"] for r in completed),
                                    "matched_completed": len(matched), "matched_usable": sum(r["quality"]["usable"] for r in matched),
                                    "median_ready_seconds": statistics.median(ready) if ready else None,
                                    "median_download_seconds": statistics.median(r.get("download_seconds", 0) for r in completed) if completed else None,
                                    "total_usage_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in own),
                                    "usage_records": sum(bool(r.get("usage")) for r in own)}
    atomic_write_json(root / "summary.json", summary)
    lines = ["# Seedance 速度对照", "", f"更新：{summary['updated_at']}", "", "相同场景、参考图/音频、480p档、15秒、16:9，每型号最多一个请求同时在途。后两轮按场景同时提交；首轮仅作接口探针，不计入速度中位数。现有生产负载继续运行。", "", "| 型号 | 接受 / 已记录 | 全部完成 | 匹配样本通过 / 完成 | 匹配返回中位秒数 | 实际用量 tokens |", "|---|---:|---:|---:|---:|---:|"]
    for model, m in summary["models"].items():
        elapsed = f"{m['median_ready_seconds']:.1f}" if m["median_ready_seconds"] is not None else "—"
        lines.append(f"| {model} | {m['accepted']} / {m['recorded']} | {m['completed']} | {m['matched_usable']} / {m['matched_completed']} | {elapsed} | {m['total_usage_tokens'] if m['usage_records'] else '未返回'} |")
    lines += ["", "| 场景 | 型号 | 状态 | 返回秒数 | 台词缺失比例 | 视频 |", "|---|---|---|---:|---:|---|"]
    for r in rows:
        video = root / "runs" / r["case"] / r["model"] / "video.mp4"
        ready = f"{r['ready_seconds']:.1f}" if "ready_seconds" in r else "—"
        missing = f"{r['quality']['missing_fraction']:.1%}" if "missing_fraction" in r.get("quality", {}) else "—"
        lines.append(f"| {r['case']} | {r['model']} | {r['status']} | {ready} | {missing} | {'[查看](' + str(video) + ')' if video.is_file() else '—'} |")
    lines += ["", "限制：每型号最多3条，其中匹配速度样本仅2条，属探针；不能据此直接推算30并发生产吞吐。返回耗时包含服务端排队，轮询精度约5秒；自动检查只覆盖媒体完整性、时长和语音覆盖，不等于完整审片通过。480p为请求档位，实际返回像素尺寸单独记录；生产合成时需统一缩放。未核实实际币种计费换算，仅记录服务端用量。", "", "凭据及路由限制：" + summary["comparability_note"]]
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json", help="frozen experiment configuration filename")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true", help="re-evaluate downloaded artifacts without submitting or polling tasks")
    args = parser.parse_args()
    root = args.experiment_dir.resolve()
    config = json.loads((root / args.manifest).read_text())
    if len(config["cases"]) * len(config["models"]) > config["max_paid_submissions"]:
        raise ValueError("Manifest exceeds its paid submission budget")
    if args.evaluate_only:
        for case in config["cases"]:
            for model in config["models"]:
                directory = root / "runs" / case["id"] / model
                if (directory / "video.mp4").is_file():
                    row = json.loads((directory / "result.json").read_text())
                    row["quality"] = evaluate(root, config, case, directory)
                    row.update(status="completed", evaluated_at=stamp())
                    atomic_write_json(directory / "result.json", row)
    elif not args.summary_only:
        submitted = [r["submitted_epoch"] for p in (root / "runs").glob("*/*/result.json")
                     if "submitted_epoch" in (r := json.loads(p.read_text()))]
        deadline = min(submitted, default=time.time()) + config["experiment_timeout_seconds"]
        for index, case in enumerate(config["cases"]):
            if time.time() >= deadline:
                break
            order = config["models"][index:] + config["models"][:index]
            unavailable = {r["model"] for p in (root / "runs").glob("*/*/result.json")
                           if (r := json.loads(p.read_text())).get("status") == "submission_rejected" and r.get("http_status") in {401, 403}}
            for model in unavailable:
                path = root / "runs" / case["id"] / model / "result.json"
                if not path.exists():
                    atomic_write_json(path, {"case": case["id"], "model": model, "status": "model_unavailable", "error": "Skipped after an earlier credential/model access rejection; no request submitted."})
            order = [model for model in order if model not in unavailable]
            log(f"Starting paired round {index + 1}: {case['id']}")
            with ThreadPoolExecutor(max_workers=config["global_experiment_concurrency"]) as pool:
                results = list(pool.map(lambda model: run_one(root, config, case, model, deadline), order))
            summarize(root, config)
            if any(r.get("status") == "submission_unknown" for r in results):
                log("Stopping after an ambiguous submission; no automatic new generation.")
                break
    summary = summarize(root, config)
    log(json.dumps(summary["models"], ensure_ascii=False))
    return 0 if len(summary["rows"]) == len(config["models"]) * len(config["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
