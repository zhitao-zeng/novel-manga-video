"""Check a book's pipeline inputs against the rules its prompts promise.

The prompts grew a rule at a time - a location is 名字：描写, an empty-scene description carries no
people, an inscription is never legible, a minor is never described by their body - and each rule
only ever applied to what was written after it.  Nothing re-read the entries already on disk, so a
book carries its oldest mistakes into every card and every clip drawn from them.

This reads the finished artifacts instead of the prompts, and reports what does not hold.  The split
is deliberate: structure is checked in code, because a gate has to be reproducible and free, and
meaning is checked by a model, because "是不是写了人" is a judgement.  Every model finding quotes the
span it objected to, so a disagreement is arguable rather than a score to tune.

    python scripts/pipeline_audit.py --novel-dir outputs/X [--storyboard DIR] [--judge flashnext|none]

Exit status is 1 when anything is reported at level 错, so it can stand in front of a render.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.llm.client import ask_json  # noqa: E402
from novel_manga.planning.audit import (  # noqa: E402
    LEVEL_ORDER, Audit, check_characters, check_clip_plan, check_locations_structure,
    check_storyboard)
from novel_manga.review.endpoints import judge_settings  # noqa: E402

# gpu81's Flash-Next is shared with everything else that judges; two at a time is its whole budget.
JUDGE_WORKERS = 2
# gpu81's Flash-Next is shared with everything else that judges; two at a time is its whole budget.

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {"type": "boolean"},
        "people_evidence": {"type": "string"},
        "readable_text": {"type": "boolean"},
        "readable_text_evidence": {"type": "string"},
        "transient": {"type": "boolean"},
        "transient_evidence": {"type": "string"},
        "states_time": {"type": "boolean"},
        "states_light": {"type": "boolean"},
    },
    "required": ["people", "people_evidence", "readable_text", "readable_text_evidence",
                 "transient", "transient_evidence", "states_time", "states_light"],
}

JUDGE_PROMPT = (
    "下面是一条地点描写，它会被原样当成「空场景卡」的绘图提示词：画出来的应该是一张没有人的背景板，"
    "而且同一个地点的每个镜头都复用这一张。请只看这段文字本身回答三个问题：\n"
    "1. people：有没有写到人？包括人群、人影、剪影、身影，以及游客、乘客、路人、学生、店员这类指人的词。"
    "「夜里游人已散」这种说人不在的句子算没有写人，填 false。\n"
    "2. readable_text：有没有写到能读出字的文字？碑文、匾额、招牌、书页写成「风化认不出字形」算没有，填 false；"
    "写成能辨认的字句才算有。\n"
    "3. transient：有没有只属于某一场戏的临时状态？比如摊开的行李、正在发生的动作、临时摆出来的东西。\n"
    "4. states_time：读完这段，画图的人知不知道这是一天里的什么时候？直接写了「下午」「夜里」算知道，"
    "写「阳光直射」「车窗透进日光」这种能推出白天的也算知道，填 true；完全看不出填 false。\n"
    "5. states_light：知不知道主光源是什么、从哪来？（日光、路灯、烛火、窗外天光……）\n"
    "前三个问题每个 true 都要在 *_evidence 里原样抄出你依据的那一小段原文；false 时 *_evidence 写空字符串。"
    "只输出 JSON。\n\n地点描写：\n"
)


def judge_locations(pairs: list[tuple[str, str]], judge: str, audit: Audit) -> None:
    settings = judge_settings(judge)

    def one(pair: tuple[str, str]) -> tuple[str, dict | str]:
        name, description = pair
        try:
            return name, ask_json([{"type": "text", "text": JUDGE_PROMPT + description}],
                                  JUDGE_SCHEMA, name="location_rules", max_tokens=600,
                                  settings=settings)
        except Exception as error:
            return name, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as pool:
        for name, answer in pool.map(one, pairs):
            where = f"圣经·地点 {name}"
            if isinstance(answer, str):
                audit.add("提醒", where, "判定没跑成", answer)
                continue
            if answer.get("people"):
                audit.add("错", where, "空场描写里写了人",
                          "这张板会被这个地点的每个镜头复用，画进去的人会跟着出现在每一镜",
                          answer.get("people_evidence", ""))
            if answer.get("readable_text"):
                audit.add("错", where, "描写里有可读文字",
                          "卡的判定会把可读文字判成缺陷", answer.get("readable_text_evidence", ""))
            if not answer.get("states_time"):
                audit.add("漏", where, "缺时段", "描写看不出是白天还是夜里，同一地点的两个镜头会打架")
            if not answer.get("states_light"):
                audit.add("漏", where, "缺主光源", "描写没交代光从哪来")
            if answer.get("transient"):
                audit.add("漏", where, "描写里有一场戏的临时状态",
                          "背景板是长期不变的样子，临时状态会被烤进所有镜头",
                          answer.get("transient_evidence", ""))


# --------------------------------------------------------------------------- entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", required=True, type=Path)
    parser.add_argument("--storyboard", type=Path, help="目录，递归找 xlsx 分镜")
    parser.add_argument("--storyboard-glob", default="**/*.xlsx",
                        help="限定要检查哪些分镜，默认这个目录下全部")
    parser.add_argument("--judge", default="flashnext", help="flashnext / local / none")
    parser.add_argument("--json", type=Path, help="把结果另存一份")
    args = parser.parse_args()

    novel_dir = args.novel_dir if args.novel_dir.is_absolute() else ROOT / args.novel_dir
    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    audit = Audit()

    describable = check_locations_structure(bible, audit)
    check_characters(bible, audit)
    check_clip_plan(novel_dir, bible, audit)
    if args.storyboard:
        check_storyboard(args.storyboard if args.storyboard.is_absolute() else ROOT / args.storyboard,
                         bible, audit, args.storyboard_glob)
    if args.judge != "none" and describable:
        judge_locations(describable, args.judge, audit)

    audit.findings.sort(key=lambda f: (LEVEL_ORDER.get(f.level, 9), f.where))
    counts = {level: sum(1 for f in audit.findings if f.level == level) for level in LEVEL_ORDER}
    print(f"《{bible.get('novel_title', novel_dir.name)}》 "
          f"角色 {len(bible.get('characters', []))} 地点 {len(bible.get('locations', []))}")
    print(f"错 {counts['错']} · 漏 {counts['漏']} · 提醒 {counts['提醒']}\n")
    for finding in audit.findings:
        print(finding.row())
    if args.json:
        args.json.write_text(json.dumps(
            [f.__dict__ for f in audit.findings], ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if audit.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
