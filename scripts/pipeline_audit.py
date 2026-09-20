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
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.llm.client import ask_json  # noqa: E402
from novel_manga.review.endpoints import judge_settings  # noqa: E402

# gpu81's Flash-Next is shared with everything else that judges; two at a time is its whole budget.
JUDGE_WORKERS = 2

DELIVERY_MODES = ("说", "画外音", "内心独白", "唱", "聊天消息")
BODY_WORDS = ("身材", "曲线", "胸", "臀", "腰肢", "丰满", "火爆", "紧身", "凹凸", "S型", "s型")
MIN_DESCRIPTION = 15  # shorter than this is a name with a comment, not a plate the model can draw

LEVEL_ORDER = {"错": 0, "漏": 1, "提醒": 2}


@dataclass
class Finding:
    level: str
    where: str
    rule: str
    detail: str
    evidence: str = ""

    def row(self) -> str:
        ev = f"  ← 「{self.evidence}」" if self.evidence else ""
        return f"[{self.level}] {self.where} · {self.rule}\n      {self.detail}{ev}"


@dataclass
class Audit:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, where: str, rule: str, detail: str, evidence: str = "") -> None:
        self.findings.append(Finding(level, where, rule, detail, evidence))

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.level == "错")


def split_location(entry: str) -> tuple[str, str]:
    """A bible location is 名字：描写; anything before the first colon is the name."""
    name, _, description = str(entry).partition("：")
    return name.strip(), description.strip()


def parse_age(raw: str) -> int | None:
    digits = re.search(r"(\d{1,3})", str(raw or ""))
    if digits:
        return int(digits.group(1))
    table = {"十七": 17, "十六": 16, "十五": 15, "十四": 14, "十三": 13, "十二": 12, "十八": 18, "十九": 19}
    for word, value in table.items():
        if word in str(raw or ""):
            return value
    if any(w in str(raw or "") for w in ("少年", "少女", "孩童", "幼", "童")):
        return 12  # unstated but plainly a minor; the body rule should still bite
    return None


# --------------------------------------------------------------------------- structure


def check_locations_structure(bible: dict, audit: Audit) -> list[tuple[str, str]]:
    """Returns the (name, description) pairs worth sending to the judge."""
    seen: dict[str, int] = {}
    describable: list[tuple[str, str]] = []
    for entry in bible.get("locations", []):
        name, description = split_location(entry)
        where = f"圣经·地点 {name or entry}"
        seen[name] = seen.get(name, 0) + 1
        if not description:
            audit.add("错", where, "地点必须写成 名字：描写",
                      "只有地点名，没有描写；地点卡会拿这个名字当整条提示词，模型只能自己编")
            continue
        if len(description) < MIN_DESCRIPTION:
            audit.add("漏", where, "描写太短",
                      f"只有 {len(description)} 字，画不出建筑结构、布局、材质", description)
        describable.append((name, description))
    for name, count in seen.items():
        if count > 1:
            audit.add("错", f"圣经·地点 {name}", "地点名全书唯一", f"出现 {count} 次")
    return describable


def check_characters(bible: dict, audit: Audit) -> None:
    for character in bible.get("characters", []):
        if not isinstance(character, dict):
            audit.add("错", f"圣经·角色 {character}", "角色应是对象", "读到的是字符串，没有可用字段")
            continue
        name = character.get("name") or "(无名)"
        where = f"圣经·角色 {name}"
        for field_name in ("gender", "age", "appearance", "wardrobe"):
            if not str(character.get(field_name) or "").strip():
                audit.add("漏", where, f"缺 {field_name}", "角色卡这一项只能靠模型编")
        age = parse_age(character.get("age", ""))
        if age is not None and age < 18:
            text = f"{character.get('appearance', '')}{character.get('wardrobe', '')}"
            hit = next((w for w in BODY_WORDS if w in text), "")
            if hit:
                audit.add("错", where, "未成年不得有身材描写",
                          f"原著写明 {character.get('age')}，条目里仍有身材向描写", hit)


def check_clip_plan(novel_dir: Path, bible: dict, audit: Audit) -> None:
    names = {split_location(e)[0] for e in bible.get("locations", [])}
    cast = {c.get("name") for c in bible.get("characters", []) if isinstance(c, dict)}
    aliases_path = novel_dir / "bible_aliases.json"
    if aliases_path.exists():
        try:
            for key, values in json.loads(aliases_path.read_text(encoding="utf-8")).items():
                cast.add(key)
                cast.update(values if isinstance(values, list) else [values])
        except (OSError, ValueError):
            pass
    for plan_path in sorted(novel_dir.glob("*/clip_plan.json")):
        episode = plan_path.parent.name
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            audit.add("错", f"分集 {episode}", "clip_plan 读不出来", str(error))
            continue
        for clip in plan.get("clips", []):
            where = f"{episode}/{clip.get('clip_id')}"
            location = str(clip.get("location") or "").strip()
            if not location:
                audit.add("错", where, "镜头没有地点", "空场卡无从选起")
            elif location not in names:
                audit.add("错", where, "地点不在圣经里", f"用了 {location!r}，圣经的地点名里没有这个")
            for person in clip.get("characters", []) or []:
                if person and person not in cast:
                    audit.add("错", where, "角色不在圣经里",
                              f"{person!r} 没有角色卡，渲染时只能当路人画")
            seconds = clip.get("seconds") or clip.get("duration")
            if isinstance(seconds, (int, float)) and not 4 <= float(seconds) <= 15:
                audit.add("错", where, "时长超出 H3 范围", f"{seconds} 秒，本地 H3 只接 4–15 秒")


def check_storyboard(directory: Path, bible: dict, audit: Audit, pattern: str = "**/*.xlsx") -> None:
    from novel_manga.planning import storyboard as sb

    names = {split_location(e)[0] for e in bible.get("locations", [])}
    books = sorted(directory.glob(pattern))
    if not books:
        audit.add("提醒", f"分镜 {directory}", "没有找到 xlsx", "这一段没检查")
    for path in books:
        label = path.relative_to(directory) if path.is_relative_to(directory) else path
        try:
            sheets = sb.read_workbook(path)
        except Exception as error:  # the reader raises ValueError with its own wording
            audit.add("错", f"分镜 {label}", "读不通", str(error))
            continue
        for sheet in sheets:
            where = f"分镜 {label}·{sheet.name}"
            if not sheet.rows:
                audit.add("错", where, "一行都没有", "九列表头在，但没有镜头")
            for row in sheet.rows:
                shot = row.get("authored_id") or "?"
                sound = str(row.get("authored_sound") or "")
                for line in sound.splitlines():
                    line = line.strip()
                    if not line or line.startswith("声音："):
                        continue
                    match = re.match(r"^(.+?)（([^）]*)）：“(.*)”$", line)
                    if not match:
                        audit.add("错", f"{where}·{shot}", "台词格式不对",
                                  "要写成 说话人（发声方式）：“台词”", line[:60])
                        continue
                    mode = match.group(2).split("、")[0]
                    if mode not in DELIVERY_MODES:
                        audit.add("错", f"{where}·{shot}", "发声方式不在五选一里",
                                  f"写的是 {mode!r}，只能是 {'/'.join(DELIVERY_MODES)}", line[:60])
                location = str(row.get("location") or "").strip()
                if location and names and location not in names:
                    audit.add("漏", f"{where}·{shot}", "场景名对不上圣经",
                              f"写的是 {location!r}，绑定时要靠模型猜是哪个地点")


# --------------------------------------------------------------------------- meaning


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
