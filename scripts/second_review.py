#!/usr/bin/env python
"""A second pass that asks what a viewer would notice, not what the card says.

The first review judges every clip against the character cards, so it reports a dragon whose
scales are the wrong white as a failure - and 星海 has four characters written with the same
appearance, which is why it flagged 850 episodes.  This pass looks at the finished frames and
asks one question: watching this once, would an ordinary viewer see something wrong?  It is
told explicitly that two characters resembling each other is not a fault here, so what comes
back is the list worth paying to re-render.

    second_review.py <novel> [--all] [--workers N]

Without --all it only re-examines clips the first review already doubted.
"""
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from thin_review import ask_json, image_part  # noqa: E402

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["viewer_notices", "kind", "what", "severity"],
    "properties": {
        "viewer_notices": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["多出人物", "该说话的人不在画面", "身体或物件崩坏",
                                            "画面出现文字", "场景明显不符", "无问题"]},
        "what": {"type": "string"},
        "severity": {"type": "string", "enum": ["明显", "轻微", "无"]},
    },
}
RULES = (
    "你是普通观众，第一次看这段短剧，只看一遍。请只回答：画面里有没有一眼就看出不对的地方？\n"
    "算问题的：画面里多出一个本段没有的人或生物；有人说话但画面里根本没有这个人；"
    "身体结构崩坏（多手多头、肢体扭曲、穿模、凭空多出物件）；画面上出现文字、字幕或乱码；"
    "场景和台词说的地方明显对不上。\n"
    "不算问题的：两个角色长得像、颜色深浅有出入、发型或角的形状跟设定不完全一致、"
    "服装细节不同、表情不到位——这些观众看一遍不会发现，一律当作没问题。\n"
    "severity：明显＝看一眼就发现；轻微＝要盯着看才发现；无＝没问题。\n"
    "what 用一句话说清楚看到了什么。只输出 JSON。"
)


def frames_of(video: Path, count: int = 4) -> list[Path]:
    try:
        duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", str(video)], capture_output=True, text=True,
                                        check=True).stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return []
    out = []
    for i in range(count):
        at = duration * (i + 1) / (count + 1)
        path = Path(tempfile.mkstemp(suffix=".png")[1])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.1f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=640:-1", str(path)], check=False)
        if path.is_file() and path.stat().st_size:
            out.append(path)
    return out


def judge(job: tuple) -> dict | None:
    novel, index, clip_id, clip = job
    d = ROOT / "outputs" / novel / f"{novel}_{index}"
    video = d / "work" / "clips" / clip_id / "attempt_01" / "clip.mp4"
    if not video.is_file():
        return None
    images = frames_of(video)
    if len(images) < 2:
        return None
    cast = "、".join(clip.get("cast") or []) or "（未列出）"
    lines = "；".join(str(l.get("text", ""))[:30] for l in (clip.get("lines") or [])[:4] if isinstance(l, dict))
    try:
        answer = ask_json([{"type": "text", "text": RULES + f"\n\n本段应该出场的角色：{cast}。"
                            + (f"\n本段台词：{lines}" if lines else "")},
                           *[image_part(p, 768) for p in images]], SCHEMA, name="second", max_tokens=500)
    except Exception as error:  # noqa: BLE001
        return {"episode": f"{novel}_{index}", "clip": clip_id, "error": f"{type(error).__name__}"}
    finally:
        for p in images:
            p.unlink(missing_ok=True)
    row = {"episode": f"{novel}_{index}", "clip": clip_id, **answer}
    if answer.get("viewer_notices"):
        print(f"  {row['episode']} {clip_id}: {answer['severity']} {answer['kind']} — {answer['what'][:60]}", flush=True)
    return row


def main() -> int:
    novel = sys.argv[1] if len(sys.argv) > 1 else "xinghai"
    everything = "--all" in sys.argv
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 6
    jobs = []
    for d in sorted((ROOT / "outputs" / novel).glob(f"{novel}_*")):
        index = d.name.rsplit("_", 1)[-1]
        plan, review = d / "clip_plan.json", d / "episode_review.json"
        if not index.isdigit() or not plan.is_file():
            continue
        try:
            clips = {c["clip_id"]: c for c in json.loads(plan.read_text(encoding="utf-8"))["clips"]}
        except (OSError, ValueError):
            continue
        verdicts = {}
        if review.is_file():
            try:
                verdicts = json.loads(review.read_text(encoding="utf-8")).get("clips") or {}
            except (OSError, ValueError):
                verdicts = {}
        for clip_id, clip in clips.items():
            if clip.get("kind") != "video":
                continue
            doubted = verdicts.get(clip_id, {}).get("severity") == "fail"
            if everything or doubted:
                jobs.append((novel, index, clip_id, clip))
    print(f"{novel}: 二审 {len(jobs)} 个片段，{workers} 路并行")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(judge, jobs):
            if row:
                rows.append(row)
    out = ROOT / "outputs" / novel / "second_review.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    notices = [r for r in rows if r.get("viewer_notices")]
    obvious = [r for r in notices if r.get("severity") == "明显"]
    print(f"\n二审 {len(rows)} 个片段：观众会发现 {len(notices)}，其中明显 {len(obvious)}")
    print("按类型：", dict(Counter(r.get("kind") for r in obvious)))
    print("涉及集数：", len({r["episode"] for r in obvious}), "→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
