#!/usr/bin/env python
"""Write director corrections for the clips the second review flagged, ready to re-render.

A correction points at the fault in the reviewer's own words, then adds the standing
instruction for that kind of fault - the earlier A/B showed the reviewer's sentence beats a
tidied rewrite 6-7 to 2-8.  Writing the file is the whole trigger: it changes the prompt, so
the request hash changes, so that clip regenerates and every other clip in the episode stays
cached.

    repair_from_review.py <novel> [--votes 2] [--apply]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE = {
    "画面出现文字": "画面中不得出现任何文字、字幕、招牌字、书页字迹、水印或乱码；"
                    "牌匾、书本、旗帜、石碑一律保持空白或只用花纹装饰；画面下方不得出现字幕条",
    "身体结构错误": "人物肢体、面部和道具必须结构正常：一个身体只有一个头，不得多出或缺少头、肢体、手指、翅膀，"
                    "不得穿模、重影、断裂或出现多余物体；动作幅度放小，保持角色形体稳定",
    "同一角色重复出现": "画面中每个角色只能出现一次，不得出现同一角色的第二个身影、分身或镜像；"
                        "同框角色必须是不同的人，各自保持自己的外形特征",
    "穿模或比例荒谬": "人物、道具和场景的大小关系必须合理，不得互相穿过或浮在空中；"
                      "角色与场景的比例保持一致",
}
LEAD = re.compile(r"^(描述中?(明确)?(提到|指出|说明|说)|根据描述|画面上有)[，,：:]?\s*")
TAIL = re.compile(r"[，,]?\s*(这)?(直接|符合|属于|根据|违反)[^。；]*[。；]?$")


def observation(row: dict) -> str:
    """What was seen, without the grader's throat-clearing or its citation of the rule."""
    text = LEAD.sub("", (row.get("why") or "").strip())
    first = re.split(r"(?<=[。；])", text)[0].strip()
    text = TAIL.sub("", first or text).strip() or (row.get("saw") or "").strip()
    if len(text) > 110:
        cut = max(text.rfind("，", 0, 110), text.rfind("、", 0, 110))
        text = text[:cut] if cut > 40 else text[:110]
    return text.rstrip("。；;，,").translate(str.maketrans("", "", "“”‘’"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel")
    parser.add_argument("--votes", type=int, default=2, help="至少几道判定都中才修")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    novel_dir = ROOT / "outputs" / args.novel
    rows = [r for r in json.loads((novel_dir / "second_review_final.json").read_text(encoding="utf-8"))
            if r["votes"] >= args.votes]
    per_episode: dict[str, dict] = {}
    for row in rows:
        rule = RULE.get(row["kind"]) or RULE["身体结构错误"]
        per_episode.setdefault(row["episode"], {})[row["clip"]] = f"{observation(row)}；{rule}"
    print(f"{args.novel}: {len(rows)} 段 / {len(per_episode)} 集")
    print("按类型：", dict(Counter(r["kind"] for r in rows).most_common()))
    for episode, clips in list(per_episode.items())[:3]:
        for clip, text in clips.items():
            print(f"  {episode} {clip}\n    {text[:150]}")
    if not args.apply:
        print("\n（预览，加 --apply 才写）")
        return 0
    for episode, clips in per_episode.items():
        path = novel_dir / episode / "review_feedback.json"
        existing = {}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        path.write_text(json.dumps({**existing, **clips}, ensure_ascii=False, indent=1), encoding="utf-8")
    numbers = sorted((e.rsplit("_", 1)[-1] for e in per_episode), key=int)
    print(f"\n写了 {len(per_episode)} 个 review_feedback.json")
    print("集号：" + ",".join(numbers))
    (novel_dir / "repair_targets.txt").write_text(",".join(numbers), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
