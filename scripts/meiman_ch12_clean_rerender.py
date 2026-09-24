"""整集重拍：第12章上集换成清爽诊室卡，全部 14 段重渲。"""
import json, subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EP = ROOT / "outputs/meiman-daoshi/meiman-daoshi_12-1"
OV = EP / "clip_overrides.json"

overrides = json.loads(OV.read_text(encoding="utf-8"))
clean = "meiman-daoshi_12-1/work/references/clinic-clean-episode12.png"
# e2e 实验的 clinic-clean.png 需要先拷到 work/references
src = ROOT / "outputs/meiman-clean-look-20260924-211106/clinic-clean.png"
dst = EP / "work/references/clinic-clean-episode12.png"
dst.parent.mkdir(parents=True, exist_ok=True)
if src.is_file() and not dst.is_file():
    import shutil
    shutil.copy2(src, dst)

for cid, entry in overrides.items():
    entry.setdefault("reference_paths", {})["地狱厨房第九尾巷心理诊所"] = clean
OV.write_text(json.dumps(overrides, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"overrides 更新：{len(overrides)} clips 换清爽诊室卡")

env = {**os.environ,
       "NOVEL_VIDEO_MODEL": "minimax-h3-ref2va-turbo",
       "NOVEL_LOCAL_H3_URL": "pool",
       "NOVEL_H3_POOL_CONFIG": str(ROOT / "configs/h3_pool.json"),
       "NOVEL_POLL_TIMEOUT": "3600",
       "NOVEL_INFLIGHT_POOL": "h3pool",
       "NOVEL_INFLIGHT_DIR": "/mnt/disk1/zengzhitao/tmp/inflight/h3pool",
       "NOVEL_CLIP_SECONDS_MAX": "15",
       "PYTHONPATH": str(ROOT / "src")}
cmd = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/render_clips_thin.py"),
       "--novel-dir", str(ROOT / "outputs/meiman-daoshi"),
       "--episode", "meiman-daoshi_12-1", "--workers", "2", "--inflight", "24",
       "--max-attempts", "2", "--style", "meiman", "--frame", "16:9", "--tier", "quality", "--rerender"]
print("开始重渲：", " ".join(cmd), flush=True)
r = subprocess.run(cmd, env=env, cwd=str(ROOT))
sys.exit(r.returncode)
