#!/usr/bin/env python
"""A second pass that asks what a viewer would notice, not what the card says.

The first review judges every clip against the character cards, so it reports a dragon whose
scales are the wrong white as a failure - and 星海 has four characters written with the same
appearance, which is why it flagged 850 episodes.  This pass looks at the finished frames and
asks one question: watching this once, would an ordinary viewer see something wrong?  It is
told explicitly that two characters resembling each other is not a fault here, so what comes
back is the list worth paying to re-render.

    second_review.py <novel> [--all] [--workers N] [--judge local|flashnext] [--summary]

Without --all it only re-examines clips the first review already doubted.  Each verdict is
written to its own file the moment it arrives, and a worker claims a clip by creating that
file exclusively - so a crash costs one clip, a restart resumes, and two judges on different
machines can chew through the same queue at their own pace without coordinating.
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

# The two Qwen that can see pictures.  ask_json carries one model name and one key for the
# whole endpoint list, so a judge is selected per process, not per call.
JUDGES = {
    "local": {"QWEN38_LOCAL_BASE_URL": ",".join(f"http://127.0.0.1:{p}/v1" for p in range(18120, 18125)),
              "QWEN38_LOCAL_MODEL": "Qwen3.8-27B-Project",
              "QWEN38_LOCAL_API_KEY_VAR": "SECOND_REVIEW_NO_KEY", "QWEN38_LOCAL_STREAM": "0"},
    "flashnext": {"QWEN38_LOCAL_BASE_URL": "http://172.28.4.81:8038/v1",
                  "QWEN38_LOCAL_MODEL": "Qwen3.8-Flash-Next",
                  "QWEN38_LOCAL_API_KEY_VAR": "GPU81_QWEN_API_KEY", "QWEN38_LOCAL_STREAM": "1"},
}
JUDGE = os.environ.get("SECOND_REVIEW_JUDGE", "local")
if JUDGE not in JUDGES:
    raise SystemExit(f"unknown judge {JUDGE}")
os.environ.update(JUDGES[JUDGE])

from thin_review import ask_json, image_part  # noqa: E402  (after the endpoint choice)

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["saw", "wrong_out_of_100", "kind"],
    "properties": {
        "saw": {"type": "string"},
        "wrong_out_of_100": {"type": "integer"},
        "kind": {"type": "string", "enum": ["身体结构错误", "同一角色重复出现", "穿模或比例荒谬",
                                            "画面出现文字", "有台词却空无一人", "无问题"]},
    },
}
# Asking "is there a fault?" while listing the possible faults gets a fault every time: the
# first wording flagged 74% of the film, and the clips it named were taverns full of dragons.
# Asking for a number instead gives a weak suspicion somewhere to go that is not 明显, and
# saying most clips are fine sets the prior the model otherwise takes from the question itself.
RULES = (
    "这是一段 AI 生成的短剧画面，抽了几帧。先用一句话客观描述你看到了什么（saw），"
    "再回答：一百个普通观众看一遍，有几个人会说这段“画错了”（wrong_out_of_100，填 0 到 100 的整数）。\n"
    "判断依据只有画面本身，不需要知道剧情：手脚多了少了、手指数目明显不对、动物身上长出人的手臂、"
    "两个身体粘连、脸融化、同一个角色在一个画面里出现两次、画面上有文字或乱码——这些一眼就能看出，"
    "会有很多人说画错了。\n"
    "以下情况观众不会说画错：画面里人多、有群众、有敌人或怪物、背景里还有别的龙或别的人；"
    "两个角色长得像、颜色深浅不同、发型或角的形状和设定不一致、服装细节不同；"
    "肢体互相遮挡、边缘不清、构图拥挤；地点和台词对不上。\n"
    "绝大多数片段是没问题的。如果你要放大或者盯着看才能说出毛病，那就是观众不会发现，填 0 到 10。\n"
    "kind 填最主要的那一类，没问题就填“无问题”。只输出 JSON。"
)
# Kept out of the model's hands so a threshold can be moved without judging anything twice.
BAD, MAYBE = 60, 30


def severity_of(row: dict) -> str:
    n = row.get("wrong_out_of_100")
    if not isinstance(n, int):
        return "无"
    return "明显" if n >= BAD else ("轻微" if n >= MAYBE else "无")


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
        # mkstemp hands back an open descriptor as well as a name; dropping it on the floor
        # leaks one per frame, and at four frames a clip the process dies around clip 250.
        handle, name = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        path = Path(name)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.1f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=640:-1", str(path)], check=False)
        if path.is_file() and path.stat().st_size:
            out.append(path)
        else:
            path.unlink(missing_ok=True)
    return out


def claim(path: Path) -> bool:
    """Take a clip by creating its result file exclusively; whoever loses moves on."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def judge(job: tuple) -> dict | None:
    novel, index, clip_id, clip, rows_dir = job
    episode = f"{novel}_{index}"
    out = rows_dir / f"{episode}__{clip_id}.json"
    if out.exists() or not claim(out):
        return None
    row = None
    try:
        video = ROOT / "outputs" / novel / episode / "work" / "clips" / clip_id / "attempt_01" / "clip.mp4"
        if not video.is_file():
            return None
        images = frames_of(video)
        if len(images) < 2:
            return None
        lines = "；".join(str(l.get("text", ""))[:30] for l in (clip.get("lines") or [])[:4] if isinstance(l, dict))
        try:
            answer = ask_json([{"type": "text", "text": RULES
                                + (f"\n\n本段台词：{lines}" if lines else "\n\n本段没有台词。")},
                               *[image_part(p, 768) for p in images]], SCHEMA, name="second", max_tokens=500)
        except Exception as error:  # noqa: BLE001
            row = {"episode": episode, "clip": clip_id, "judge": JUDGE, "error": f"{type(error).__name__}: {error}"[:200]}
            return row
        finally:
            for p in images:
                p.unlink(missing_ok=True)
        row = {"episode": episode, "clip": clip_id, "judge": JUDGE, **answer}
        row["severity"] = severity_of(row)
        if row["severity"] != "无":
            print(f"  {episode} {clip_id}: {row['severity']} {answer.get('wrong_out_of_100')}/100 "
                  f"{answer.get('kind')} — {answer.get('saw', '')[:60]}", flush=True)
        return row
    finally:
        # A claim with nothing behind it would block the retry, so it is given back.
        if row is None:
            out.unlink(missing_ok=True)
        else:
            out.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")


def collect(rows_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(rows_dir.glob("*.json")):
        try:
            rows.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


def report(novel: str, rows: list[dict]) -> None:
    good = [r for r in rows if not r.get("error")]
    notices = [r for r in good if r.get("severity") != "无"]
    obvious = [r for r in notices if r.get("severity") == "明显"]
    print(f"\n二审 {len(good)} 个片段（出错 {len(rows) - len(good)}）：观众会发现 {len(notices)}，其中明显 {len(obvious)}")
    print("按类型：", dict(Counter(r.get("kind") for r in obvious)))
    print("涉及集数：", len({r["episode"] for r in obvious}))
    out = ROOT / "outputs" / novel / f"second_review_{os.environ.get('SECOND_REVIEW_RULES', 'v2')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", out)


def main() -> int:
    novel = sys.argv[1] if len(sys.argv) > 1 else "xinghai"
    everything = "--all" in sys.argv
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 6
    version = os.environ.get("SECOND_REVIEW_RULES", "v2")
    rows_dir = ROOT / "outputs" / novel / f"second_review_rows_{version}"
    rows_dir.mkdir(parents=True, exist_ok=True)
    if "--summary" in sys.argv:
        report(novel, collect(rows_dir))
        return 0
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
                jobs.append((novel, index, clip_id, clip, rows_dir))
    done = len(list(rows_dir.glob("*.json")))
    print(f"{novel}: 二审队列 {len(jobs)} 个片段，已判 {done}，判官 {JUDGE}，{workers} 路并行", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(judge, jobs):
            pass
    report(novel, collect(rows_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
