"""#8 资产风格 A/B 扩展：多 seed × 多段。

上一轮只测了单 seed 单段（clip_11 的台词），归因虽有一格但样本不足以宣称稳定。本脚本
把同一组提示词分别配旧诊室卡和清爽卡，各跑三个种子（原种子 +1009 和 +2018），量化
三个信号：纹理密度（网纹/排线抬高它）、暗部占比（黑斑抬高它）、对比度（硬边平涂的
美漫清爽版应当低）。段选两条：诊室开场（原图大黑斑最重的一条）和公交站（换场后的
第一条，检验干净画风的迁移）。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/meiman-asset-ab-multiseed-20260925"
OUT.mkdir(parents=True, exist_ok=True)
CLEAN_DIR = ROOT / "outputs/meiman-clean-look-20260924-211106"
E2E = ROOT / "outputs/meiman-ch12-e2e-20260924/meiman-daoshi"

OLD_CARD = E2E / "series_assets/locations/location_014/establishing.jpeg"
CLEAN_CARD = E2E / "series_assets/locations/location_014/establishing-clean-v1.jpeg"
PERSON = E2E / "series_assets/characters/character_001/turnaround.jpeg"
VOICE = E2E / "series_assets/voices/席勒.wav"
BASE = "http://172.28.4.3:30020"

SEEDS = [1025268757, 1025268757 + 1009, 1025268757 + 2018]

# 两段的提示词：诊室开场（席勒喝咖啡说话）和公交站（战衣首秀的抱怨）
SEGMENTS = {
    "clinic": (CLEAN_DIR / "prompt-clean.txt").read_text(encoding="utf-8"),
    "bus": (
        "subject_definitions:\n"
        "<Subject 1> is the person shown in <Picture 1>. Take only the face, hair, build and clothing from <Picture 1>. Exactly one <Subject 1> appears in the video; no other person has <Subject 1>'s face, hair or clothes.\n"
        "<Subject 2> is the setting in <Picture 2>: take its architecture, ground, fixed props and light from it, and none of the people in it.\n"
        "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).\n\n"
        "summary:\n"
        "[reference generation + audio reference] A 12-second Chinese animated short-drama scene in 1 shots. The scene follows <Subject 1> in <Subject 2>.\n\n"
        "retention_analysis:\n"
        "<Subject 1>: fully_preserved - the identity, face, hair and clothing of <Picture 1>.\n"
        "<Subject 2>: fully_preserved - the setting from <Picture 2>.\n\n"
        "detailed_description:\n"
        "All subjects, animals, props and environments share one consistent stylized 2D animation appearance. Clean 2D American comic animation with clear contour lines, consistent local colors, controlled contrast and broad simple shading.\n"
        "[Shot 1] A full shot under a rusted bus stop sign in a run-down street. <Subject 1> stands in the silver-white powered armour, looking comically out of place next to the shabby bus stop. An old bus belching dark smoke pulls up and stops with a groan; its doors fold open. <Subject 1> speaks, gesturing at the bus with one armoured hand.\n"
        "<Subject 1> (S1) says <d>[Chinese] 我真不敢相信我跨时代战衣的首秀就是在一辆冒黑烟的破旧巴士上……</d> The speaking voice follows the timbre referenced from <Audio 1>.\n"
        "The quoted words complete this shot's utterance. The speaker finishes, closes their mouth and holds a silent reaction.\n\n"
        "overall_soundscape:\n"
        "The diesel engine rattles, the hiss of the bus doors opening, distant street ambience.\n\n"
        "non_diegetic_music:\nNone."
    ),
}


def data_url(path: Path, mime: str) -> str:
    import base64
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()


def submit(prompt: str, card: Path, seed: int, tag: str) -> Path:
    payload = {
        "model": "minimax-h3-ref2va-turbo",
        "task": "ref2va",
        "prompt": prompt,
        "seconds": 12,
        "conditions": [
            {"type": "image", "role": "reference", "uri": data_url(PERSON, "image/jpeg")},
            {"type": "image", "role": "reference", "uri": data_url(card, "image/jpeg")},
            {"type": "audio", "role": "reference", "uri": data_url(VOICE, "audio/wav")},
        ],
        "target": {"short_edge": 768, "aspect_ratio": "16:9", "duration_seconds": 12.0},
        "num_outputs_per_prompt": 1,
        "num_inference_steps": 8,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
        "seed": seed,
    }
    out = OUT / f"{tag}.mp4"
    with httpx.Client(timeout=120, trust_env=False) as client:
        r = client.post(f"{BASE}/v1/videos", json=payload)
        r.raise_for_status()
        job = r.json()
        job_id = job.get("id") or job.get("task_id")
        print(f"  {tag}: 任务 {job_id}", flush=True)
        started = time.time()
        while True:
            time.sleep(5)
            s = client.get(f"{BASE}/v1/videos/{job_id}")
            s.raise_for_status()
            state = (s.json().get("status") or s.json().get("state"))
            if state in ("succeeded", "success", "done", "completed"):
                break
            if state in ("failed", "error"):
                (OUT / f"{tag}-failed.json").write_text(json.dumps(s.json(), ensure_ascii=False), encoding="utf-8")
                sys.exit(f"{tag} 失败: {json.dumps(s.json())[:300]}")
            if time.time() - started > 1200:
                sys.exit(f"{tag} 超时")
        c = client.get(f"{BASE}/v1/videos/{job_id}/content")
        c.raise_for_status()
        out.write_bytes(c.content)
    for t in (2, 6, 10):
        subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-q:v", "2",
                        str(OUT / f"{tag}-{t}s.jpg")], capture_output=True, check=False)
    return out


def metrics(path: Path, size=(640, 366)) -> dict:
    img = np.asarray(Image.open(path).convert('L').resize(size), dtype=np.float32)
    dx = np.abs(np.diff(img, axis=1)).mean()
    dy = np.abs(np.diff(img, axis=0)).mean()
    return {"edge": round((dx + dy) / 2, 2), "dark": round(float((img < 60).mean()), 2),
            "contrast": round(float(img.std()), 1)}


def main() -> None:
    results = {}
    for seg_name, prompt in SEGMENTS.items():
        for card_name, card in (("old", OLD_CARD), ("clean", CLEAN_CARD)):
            for seed in SEEDS:
                tag = f"{seg_name}-{card_name}-{seed % 10000}"
                print(f"渲染 {tag}…", flush=True)
                submit(prompt, card, seed, tag)
                row = {}
                for t in (2, 6, 10):
                    frame = OUT / f"{tag}-{t}s.jpg"
                    if frame.is_file():
                        row[f"{t}s"] = metrics(frame)
                results[tag] = row
    # 汇总：按 段×卡 平均
    summary = {}
    for seg_name in SEGMENTS:
        for card_name in ("old", "clean"):
            key = f"{seg_name}-{card_name}"
            edges = [m["edge"] for tag, frames in results.items()
                     if tag.startswith(key) for m in frames.values()]
            darks = [m["dark"] for tag, frames in results.items()
                     if tag.startswith(key) for m in frames.values()]
            contrasts = [m["contrast"] for tag, frames in results.items()
                          if tag.startswith(key) for m in frames.values()]
            summary[key] = {"edge_avg": round(sum(edges) / len(edges), 2),
                            "dark_avg": round(sum(darks) / len(darks), 2),
                            "contrast_avg": round(sum(contrasts) / len(contrasts), 1)}
    (OUT / "multiseed-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
