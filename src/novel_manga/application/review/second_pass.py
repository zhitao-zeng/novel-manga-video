#!/usr/bin/env python
"""Ask what a viewer would notice, in two passes, and combine the answers.

The first review judges each clip against the character cards, so it reports a dragon whose
scales are the wrong white.  This one asks whether the picture itself is wrong - and it took
three failed wordings to get there, each failing the same way: a rule that is true in general
was false for this particular show.

    "画面里多出一个本段没有的人"        - the cast list names who must appear, not everyone
                                          allowed on screen, so every crowd became a fault
    "动物身上长着人的手"                - 星海's dragons are anthropomorphic by design
    "物件浮在空中"                      - in a cultivation story, swords are supposed to fly

So what counts as normal is not written into this file.  It is read from
``outputs/<novel>/review_normal.txt`` and pasted into both prompts.  A new novel gets its own
note; getting that note wrong is the failure mode to watch for.

Two passes, because the model is better at seeing than at judging.  It will describe "一个拥有
三个龙头的类人生物" accurately and then score it 0, since a three-headed dragon sounds like a
legitimate creature.  So the first pass looks at frames and scores, the second reads back the
description it wrote - with no picture to soften it - and rules on that alone.  A clip is
worth money when both agree.

    second_review.py <novel> [--all] [--stage look|read|report|all] [--workers N]
                             [--judge local|flashnext] [--limit N]

Without --all only the clips the first review already doubted are examined.  Every verdict is
written as it arrives and a worker claims a clip by creating its file exclusively, so a crash
costs one clip and a restart resumes.
"""
from novel_manga.application.configuration import project_root
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = project_root()

# The two judges that can see pictures.  ask_json carries one model name and one key for its
# whole endpoint list, so a judge is chosen per process, not per call.
from novel_manga.review.endpoints import JUDGES, judge_settings
JUDGE = os.environ.get("SECOND_REVIEW_JUDGE", "local")
if JUDGE not in JUDGES:
    raise SystemExit(f"unknown judge {JUDGE}; pick one of {sorted(JUDGES)}")

from novel_manga.llm.client import ask_json, image_part

KINDS = ["身体结构错误", "同一角色重复出现", "穿模或比例荒谬", "画面出现文字", "有台词却空无一人", "无问题"]
LOOK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["saw", "wrong_out_of_100", "kind"],
    "properties": {"saw": {"type": "string"}, "wrong_out_of_100": {"type": "integer"},
                   "kind": {"type": "string", "enum": KINDS}},
}
READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["broken", "why"],
               "properties": {"broken": {"type": "boolean"}, "why": {"type": "string"}}}

# Asking "is there a fault?" while listing the possible faults gets a fault every time - the
# first wording condemned 74% of a series.  Asking for a number gives a weak suspicion
# somewhere to go that is not 明显, and saying most clips are fine sets the prior the model
# would otherwise take from the question itself.
LOOK_RULES = (
    "这是一段 AI 生成的短剧画面，抽了几帧。先用一句话客观描述你看到了什么（saw），"
    "再回答：一百个普通观众看一遍，有几个人会说这段“画错了”（wrong_out_of_100，填 0 到 100 的整数）。\n"
    "判断依据只有画面本身，不需要知道剧情：手脚多了少了、手指数目明显不对、两个身体粘连、"
    "脸融化、同一个角色在一个画面里出现两次、画面上有文字或乱码——这些一眼就能看出。\n"
    "以下情况观众不会说画错：画面里人多、有群众、有敌人或怪物、背景里还有别人；"
    "两个角色长得像、颜色深浅不同、发型或服装细节和设定不完全一致；"
    "肢体互相遮挡、边缘不清、构图拥挤；地点和台词对不上。\n"
    "绝大多数片段是没问题的。如果你要放大或者盯着看才能说出毛病，那就是观众不会发现，填 0 到 10。\n"
    "kind 填最主要的那一类，没问题就填“无问题”。只输出 JSON。"
)
READ_RULES = (
    "下面是别人对一段 AI 生成画面的客观描述。判断：描述里说到的画面，是不是生成出错了？\n"
    "算出错：一个身体上有多个头或多张脸；同一个身体上多出或少了手臂、腿、翅膀；两个身体粘在一起；"
    "肢体扭曲、融化、断裂、错位；同一个角色在同一个画面里出现两次；画面上有文字、字幕或乱码。\n"
    "不算出错：画面里角色多、有群众或敌人；不同颜色体型的生物同时出现；"
    "镜头切换、构图拥挤、光线昏暗、边缘模糊；描述本身很简略或说“结构清晰/正常”。\n"
    "只输出 JSON。"
)
BAD, MAYBE = 60, 30  # kept out of the model's hands so a threshold moves without judging again
POLL_FRAMES = 4


# The one thing a show's note may not do is make a broken body acceptable.  No art direction
# gives a character two heads on one neck or a limb that melts, so these are restated after the
# note, in code, where a per-novel file cannot soften them.
NEVER_NORMAL = (
    "【以下永远算生成错误，不受本片设定影响】一个身体上长出多个头或多张脸"
    "（唯一例外：【本片设定】里点名说某个角色本来就长着几个头——那个角色长着设定里写的那个数量的头才是正确的；"
    "头的数量和设定不一样、几张脸粘在一起，或者其他任何生物多头，仍然算错误）；"
    "同一个身体多出或缺少手臂、腿、翅膀；两个身体粘在一起；肢体扭曲、融化、断裂、错位；"
    "同一个角色在同一个画面里出现两次；画面上出现文字、字幕或乱码。"
    "即使描述里说这是某种奇幻生物，多头、粘连、断肢也依然算错误——唯一的例外只有上面那一条。\n"
)


def normal_note(novel_dir: Path) -> str:
    """What this show's art direction makes normal, so a general rule does not condemn it.

    Every false-positive wave this pass has produced came from missing this: dragons with
    hands, floating swords, a tavern full of extras.  The note is the novel's own; an empty
    one is a bet that nothing in this show looks like a defect from outside.
    """
    candidates = [ROOT / "configs" / "review_normal" / f"{novel_dir.name}.txt",
                  novel_dir / "review_normal.txt"]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        return ""
    text = " ".join(line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#"))
    if not text:
        return ""
    return f"\n【本片设定】以下是本片的美术设定，属于正常，不要当成生成错误：{text}\n" + NEVER_NORMAL


def frames_of(video: Path, count: int = POLL_FRAMES) -> list[Path]:
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


def run_claimed(jobs: list, work, rows_dir: Path, workers: int, label: str) -> None:
    """Map work over jobs, each writing its own result file, claimed before it starts."""
    rows_dir.mkdir(parents=True, exist_ok=True)

    def one(job):
        out = rows_dir / job["file"]
        if out.exists() or not claim(out):
            return
        row = None
        try:
            row = work(job)
        finally:
            if row is None:
                out.unlink(missing_ok=True)  # a claim with nothing behind it would block retry
            else:
                out.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    print(f"{label}: {len(jobs)} 个待办，{workers} 路并行，判官 {JUDGE}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(one, jobs):
            pass


def collect(rows_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(rows_dir.glob("*.json")):
        try:
            rows.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


# ------------------------------------------------------------------ stage one
def look_jobs(novel: str, novel_dir: Path, everything: bool) -> list[dict]:
    jobs = []
    for d in sorted(novel_dir.glob(f"{novel}_*")):
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
            if not (everything or verdicts.get(clip_id, {}).get("severity") == "fail"):
                continue
            video = d / "work" / "clips" / clip_id / "attempt_01" / "clip.mp4"
            if not video.is_file():
                continue
            jobs.append({"episode": d.name, "clip": clip_id, "video": video, "clip_plan": clip,
                         "file": f"{d.name}__{clip_id}.json"})
    return jobs


def look(job: dict, note: str) -> dict | None:
    images = frames_of(job["video"])
    if len(images) < 2:
        return None
    lines = "；".join(str(l.get("text", ""))[:30] for l in (job["clip_plan"].get("lines") or [])[:4]
                      if isinstance(l, dict))
    try:
        answer = ask_json([{"type": "text", "text": LOOK_RULES + note
                            + (f"\n本段台词：{lines}" if lines else "")},
                           *[image_part(p, 768) for p in images]], LOOK_SCHEMA, name="look", max_tokens=500, settings=judge_settings(JUDGE))
    except Exception as error:  # noqa: BLE001
        return {"episode": job["episode"], "clip": job["clip"], "judge": JUDGE,
                "error": f"{type(error).__name__}: {error}"[:200]}
    finally:
        for p in images:
            p.unlink(missing_ok=True)
    row = {"episode": job["episode"], "clip": job["clip"], "judge": JUDGE, **answer}
    if isinstance(row.get("wrong_out_of_100"), int) and row["wrong_out_of_100"] >= MAYBE:
        print(f"  {row['episode']} {row['clip']}: {row['wrong_out_of_100']}/100 {row.get('kind')}"
              f" — {row.get('saw','')[:60]}", flush=True)
    return row


# ------------------------------------------------------------------ stage two
def read_back(job: dict, note: str) -> dict | None:
    try:
        answer = ask_json([{"type": "text", "text": READ_RULES + note + "\n\n描述：" + job["saw"]}],
                          READ_SCHEMA, name="read", max_tokens=400, settings=judge_settings(JUDGE))
    except Exception as error:  # noqa: BLE001
        return {**job["row"], "read": None, "read_why": type(error).__name__}
    row = {**job["row"], "read": bool(answer.get("broken")), "read_why": (answer.get("why") or "")[:150]}
    if row["read"]:
        print(f"  {row['episode']} {row['clip']}: {row['read_why'][:70]}", flush=True)
    return row


# ---------------------------------------------------------------- stage three
def report(novel_dir: Path, look_dir: Path, read_dir: Path) -> list[dict]:
    looked = {(r["episode"], r["clip"]): r for r in collect(look_dir) if not r.get("error")}
    been_read = {(r["episode"], r["clip"]): r for r in collect(read_dir)}
    rows = []
    for key, r in looked.items():
        score = r.get("wrong_out_of_100")
        by_picture = isinstance(score, int) and score >= BAD
        second = been_read.get(key, {})
        by_words = bool(second.get("read"))
        if not (by_picture or by_words):
            continue
        rows.append({"episode": key[0], "clip": key[1], "kind": r.get("kind"), "score": score,
                     "by_picture": by_picture, "by_words": by_words,
                     "votes": int(by_picture) + int(by_words),
                     "saw": r.get("saw", ""), "why": second.get("read_why", "")})
    rows.sort(key=lambda r: (-r["votes"], r["episode"], r["clip"]))
    print(f"\n看了 {len(looked)} 段，其中 {len(been_read)} 段复读过描述")
    print(f"两道合计挑出 {len(rows)} 段 = {len(rows)/max(1,len(looked)):.1%}")
    for v, label in ((2, "两道都中（值得花钱重渲）"), (1, "只有一道中（自己扫一眼）")):
        sub = [r for r in rows if r["votes"] == v]
        print(f"  {v} 票 {label}: {len(sub)} 段  {dict(Counter(r['kind'] for r in sub).most_common(4))}")
    per_ep = Counter(r["episode"] for r in rows)
    print(f"涉及 {len(per_ep)} 集，每集坏几段：{dict(sorted(Counter(per_ep.values()).items()))}")
    print("最集中的 8 集：", ", ".join(f"{e}({n})" for e, n in per_ep.most_common(8)))
    out = novel_dir / "second_review_final.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", out)
    return rows


def main() -> int:
    novel = sys.argv[1] if len(sys.argv) > 1 else "xinghai"
    stage = sys.argv[sys.argv.index("--stage") + 1] if "--stage" in sys.argv else "all"
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 12
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    everything = "--all" in sys.argv
    novel_dir = ROOT / "outputs" / novel
    if not novel_dir.is_dir():
        raise SystemExit(f"没有这本小说：{novel_dir}")
    note = normal_note(novel_dir)
    print(f"{novel}: 本片设定 {'已加载' if note else '（无 review_normal.txt）'}")
    look_dir = novel_dir / "second_review" / "look" / JUDGE
    read_dir = novel_dir / "second_review" / "read" / JUDGE

    if stage in ("look", "all"):
        jobs = look_jobs(novel, novel_dir, everything)
        if limit:
            jobs = jobs[:limit]
        run_claimed(jobs, lambda j: look(j, note), look_dir, workers, "看图")
    if stage in ("read", "all"):
        jobs = [{"row": r, "saw": r["saw"], "file": f"{r['episode']}__{r['clip']}.json"}
                for r in collect(look_dir) if r.get("saw")]
        run_claimed(jobs, lambda j: read_back(j, note), read_dir, workers, "复读描述")
    if stage in ("report", "all"):
        report(novel_dir, look_dir, read_dir)
    return 0
