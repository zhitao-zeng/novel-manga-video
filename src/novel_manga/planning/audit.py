"""The rules a book's pipeline inputs must hold, checked against the finished artifacts.

The prompts grew a rule at a time and each one only ever governed what was written after it; nothing
re-read what was already on disk.  These are the checks that need no model and no key - the shape of
a location entry, whether a shot's place and cast exist, whether a clip fits the video model's range -
so the render path can run them every time.  Whether a description reads as a crowd or a legible
inscription is a judgement, and lives in scripts/pipeline_audit.py with the model that makes it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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


def blocking_problems(novel_dir: Path) -> list[Finding]:
    """The 错-level findings a render must not start on top of.

    A shot whose 场景 names no bible location has no plate to be drawn against; a location that is a
    name alone sends the image model its own name as the whole prompt; a clip outside 4-15 s cannot
    be rendered by the local H3 at all.  None of this needs a model to see.
    """
    audit = Audit()
    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    check_locations_structure(bible, audit)
    check_characters(bible, audit)
    check_clip_plan(novel_dir, bible, audit)
    return [finding for finding in audit.findings if finding.level == "错"]
