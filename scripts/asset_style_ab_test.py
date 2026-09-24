"""#8 资产风格 A/B 的缺失一格：新画风文字 + 旧诊室卡。

已完成的对照（2026-09-24）一次换了画风文字和场景图，无法归因。本脚本只换回旧诊室卡
（series_assets/locations/location_014/establishing.jpeg），其余与 clean 版完全一致：
同人物卡、同声线、同台词、同种子 1025268757、12 秒、8 步、flow_shift 12/3、同一实例
（h3-task.json 里记录的那个）。结果与 clean 版并排抽帧：如果网纹回来，资产风格是
成因之一；如果仍然干净，画风文字是主因。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/meiman-asset-ab-20260925"
OUT.mkdir(parents=True, exist_ok=True)

CLEAN_DIR = ROOT / "outputs/meiman-clean-look-20260924-211106"
E2E_DIR = ROOT / "outputs/meiman-ch12-e2e-20260924"

# 完全复用 clean 版的实际请求，只把场景图换回旧卡
request = json.loads((CLEAN_DIR / "actual-request.json").read_text(encoding="utf-8"))
old_card = E2E_DIR / "meiman-daoshi/series_assets/locations/location_014/establishing.jpeg"
assert old_card.is_file(), old_card
request["images"] = [request["images"][0], str(old_card)]

# 提交到 clean 版当时用的同一实例
task = json.loads((CLEAN_DIR / "h3-task.json").read_text(encoding="utf-8"))
base = task["endpoint"].rstrip("/")
seed = task["seed"]
request["seed"] = seed

print(f"提交到 {base}，种子 {seed}，场景图: 旧诊室卡", flush=True)


def data_url(path: Path, mime: str) -> str:
    import base64
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()


conditions = [{"type": "image", "role": "reference", "uri": data_url(Path(p), "image/jpeg")}
              for p in request["images"]]
conditions += [{"type": "audio", "role": "reference", "uri": data_url(Path(p), "audio/wav")}
               for p in request["audios"]]
payload = {
    "model": request["model"],
    "task": request["task"],
    "prompt": request["prompt"],
    "seconds": request["seconds"],
    "conditions": conditions,
    "target": request["target"],
    "num_outputs_per_prompt": 1,
    "num_inference_steps": request["num_inference_steps"],
    "flow_shift": request["flow_shift"],
    "audio_flow_shift": request["audio_flow_shift"],
    "seed": seed,
}

with httpx.Client(timeout=120, trust_env=False) as client:
    r = client.post(f"{base}/v1/videos", json=payload)
    r.raise_for_status()
    job = r.json()
    job_id = job.get("id") or job.get("task_id") or job.get("job_id")
    print(f"任务 {job_id}", flush=True)
    (OUT / "ab-task.json").write_text(json.dumps({**job, "endpoint": base}, ensure_ascii=False, indent=1), encoding="utf-8")

    started = time.time()
    while True:
        time.sleep(5)
        s = client.get(f"{base}/v1/videos/{job_id}")
        s.raise_for_status()
        status = s.json()
        state = status.get("status") or status.get("state")
        print(f"[{int(time.time() - started)}s] {state}", flush=True)
        if state in ("succeeded", "success", "done", "completed"):
            break
        if state in ("failed", "error"):
            (OUT / "ab-failed.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
            sys.exit(f"任务失败: {json.dumps(status)[:400]}")
        if time.time() - started > 1800:
            sys.exit("超时")

    content = client.get(f"{base}/v1/videos/{job_id}/content")
    content.raise_for_status()
    mp4 = OUT / "clean-text-old-card.mp4"
    mp4.write_bytes(content.content)
    print(f"下载 {mp4}（{len(content.content)} bytes）", flush=True)

# 抽 2/6/10 秒帧，与 clean / before 拼图对照
for t in (2, 6, 10):
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(mp4), "-frames:v", "1",
                    str(OUT / f"ab-{t}s.jpg")], capture_output=True, check=False)
rows = []
for t in (2, 6, 10):
    for name in ("before", "clean", "ab"):
        src = (CLEAN_DIR if name != "ab" else OUT) / f"{name}-{t}s.jpg"
        if src.is_file():
            rows.append(str(src))
subprocess.run(["ffmpeg", "-y"] + sum([["-i", p] for p in rows], []) +
               ["-filter_complex",
                f"concat=n={len(rows)}:v=1:a=0",
                str(OUT / "ab-frames-combined.jpg")], capture_output=True, check=False)
print("完成：clean-text-old-card.mp4 + ab-frames-combined.jpg（旧 | clean | 新文旧卡）", flush=True)
