"""Read authored XLSX cuts without asking a planner to rewrite their content.

This is an authoring import, not an accepted production screenplay. Columns
which combine dialogue and sound remain intact until voice/asset binding.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
# A shot is numbered 1, 2, S03; a footer's first cell is empty or a sentence (合计：101 秒 / 12 镜).
SHOT_ID = re.compile(r"[A-Za-z0-9._-]{1,8}")

# The 台词 / 声音 column, in the one shape planning/authored_brief.py asks the author for.  Parsed here
# and nowhere else: the audit checked the format with its own copy of this regex while the binder asked
# a model to write the dialogue over again, so a cell could be legal, checked, and still not be what
# reached the video.  An author who writes a legal cell gets those words, that speaker and that manner.
SPOKEN_LINE = re.compile(r"^(.+?)（([^）]*)）：“(.*)”$")
SOUND_PREFIX = "声音："
# The five the brief allows, and what each means downstream.  内心独白 is heard in the character's own
# voice with the mouth closed, which is the offscreen delivery, not a separate mode.
DELIVERY_LABELS = {"说": "visible_dialogue", "画外音": "offscreen_dialogue",
                   "内心独白": "offscreen_dialogue", "唱": "singing", "聊天消息": "chat_message"}


@dataclass(frozen=True)
class AuthoredSound:
    """One 台词 / 声音 cell: the lines the author wrote, the ambient sound, and what did not parse."""
    turns: tuple[dict, ...]
    sfx: str
    problems: tuple[tuple[str, str], ...]  # (line, why)

    @property
    def speakers(self) -> list[str]:
        return list(dict.fromkeys(turn["written_speaker"] for turn in self.turns))


def authored_sound(cell: str) -> AuthoredSound:
    """Split a 台词 / 声音 cell into spoken turns and ambient sound.

    `written_speaker` is the name as the author typed it; resolving it to a cast member is a separate
    judgement and the only part of this column a model is asked about.  `emotion` is the 语气 written
    after the 、, which is how the line is spoken - the one thing the picture cannot carry.
    """
    turns, sounds, problems = [], [], []
    for raw in str(cell or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(SOUND_PREFIX):
            rest = line[len(SOUND_PREFIX):].strip()
            if rest:
                sounds.append(rest)
            continue
        match = SPOKEN_LINE.match(line)
        if not match:
            problems.append((line, "要写成 说话人（发声方式）：“台词”"))
            continue
        manner = [part.strip() for part in match.group(2).split("、")]
        if manner[0] not in DELIVERY_LABELS:
            problems.append((line, f"发声方式写的是 {manner[0]!r}，只能是 {'/'.join(DELIVERY_LABELS)}"))
            continue
        turns.append({"written_speaker": match.group(1).strip(),
                      "delivery_mode": DELIVERY_LABELS[manner[0]],
                      "emotion": "、".join(p for p in manner[1:] if p),
                      "text": match.group(3).strip()})
    return AuthoredSound(tuple(turns), "，".join(sounds), tuple(problems))

HEADERS = {
    "镜号": "authored_id",
    "摄影角度": "camera_angle",
    "景别": "shot_scale",
    "画面内容 / 动作": "motion_prompt",
    "场景": "location",
    "台词 / 声音": "authored_sound",
    "机位 / 运镜 / 连续性": "camera",
    "叙事目的": "narrative_purpose",
    "预算秒": "edit_seconds",
}


@dataclass(frozen=True)
class StoryboardSheet:
    name: str
    source: str
    notes: tuple[str, ...]
    rows: tuple[dict, ...]


def _text(node) -> str:
    return "".join(t.text or "" for t in node.findall(".//s:t", NS))


def read_workbook(path: Path) -> list[StoryboardSheet]:
    """Read the nine authored columns; derive timing from numeric budgets.

    XLSX time-code caches may be stale after edits. They are deliberately not
    used as edit decisions, and no spreadsheet formulas are evaluated.
    """
    path = Path(path).resolve()
    sheets = []
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [_text(si) for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        relationships = {
            r.attrib["Id"]: r.attrib["Target"]
            for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            target = relationships[sheet.attrib[REL]]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            notes, rows, columns = [], [], None
            seen = set()
            for row in ET.fromstring(archive.read(target)).findall("s:sheetData/s:row", NS):
                cells, formulas = {}, set()
                for cell in row.findall("s:c", NS):
                    column = re.sub(r"\d+", "", cell.attrib["r"])
                    value = cell.findtext("s:v", "", NS)
                    kind = cell.get("t")
                    if kind == "s":
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = _text(cell)
                    cells[column] = value
                    if cell.find("s:f", NS) is not None:
                        formulas.add(column)
                if columns is None:
                    by_label = {v.strip(): c for c, v in cells.items() if v.strip()}
                    if "镜号" in by_label:
                        missing = HEADERS.keys() - by_label.keys()
                        if missing:
                            raise ValueError(f"{sheet.get('name')}: 缺少列 {', '.join(sorted(missing))}")
                        columns = {field: by_label[label] for label, field in HEADERS.items()}
                    else:
                        notes.extend(v for v in cells.values() if v)
                    continue
                if not any(cells.get(c, "") for c in columns.values()):
                    continue
                own = {field: cells.get(column, "") for field, column in columns.items()}
                row_number = int(row.attrib["r"])
                where = f"{sheet.get('name')}!{row_number}"
                if not own["motion_prompt"].strip() and not own["location"].strip():
                    # A total line under the table: it has no picture and no place, so it cannot be a
                    # shot, and its cached total must not replace the sum of the actual cuts.  This
                    # used to be recognised by its SUM formula, until the brief told authors to write
                    # plain numbers and 总预算（秒）：105 started looking like a shot with no 镜号.
                    if SHOT_ID.fullmatch(own["authored_id"].strip()):
                        raise ValueError(f"{where}: 镜号 {own['authored_id']!r} 这一行没有画面内容，也没有场景")
                    notes.extend(value for value in own.values() if value.strip())
                    continue
                shot_id = own["authored_id"]
                if not shot_id or shot_id in seen:
                    raise ValueError(f"{where}: 镜号缺失或重复 {shot_id!r}")
                if any(column in formulas for column in columns.values()):
                    raise ValueError(f"{where}: 内容或预算使用了公式，请先保存为数值/文本")
                try:
                    seconds = Decimal(own["edit_seconds"])
                except InvalidOperation as error:
                    raise ValueError(f"{where}: 预算秒不是数值") from error
                if not seconds.is_finite() or seconds <= 0:
                    raise ValueError(f"{where}: 预算秒必须大于零")
                for field in ("motion_prompt", "location"):
                    if not own[field].strip():
                        raise ValueError(f"{where}: {field} 为空")
                own["edit_seconds"] = float(seconds)
                own["source_row"] = row_number
                rows.append(own)
                seen.add(shot_id)
            if columns is not None:
                if not rows:
                    raise ValueError(f"{sheet.get('name')}: 分镜表没有镜头")
                sheets.append(StoryboardSheet(sheet.attrib["name"], str(path), tuple(notes), tuple(rows)))
    if not sheets:
        raise ValueError("未找到包含完整分镜列的工作表")
    return sheets


def authored_payload(sheet: StoryboardSheet) -> dict:
    """The authored cuts for a planning request: the nine columns as written, in sheet order.

    import_script maps them to production field names and leaves the bindings empty; the planner is
    given the author's own column labels instead, so nothing reads as a field it may rewrite.
    """
    return {
        "sheet": sheet.name,
        "notes": list(sheet.notes),
        "shots": [{
            "镜号": row["authored_id"],
            "摄影角度": row["camera_angle"],
            "景别": row["shot_scale"],
            "画面内容 / 动作": row["motion_prompt"],
            "场景": row["location"],
            "台词 / 声音": row["authored_sound"],
            "机位 / 运镜 / 连续性": row["camera"],
            "叙事目的": row["narrative_purpose"],
            "预算秒": row["edit_seconds"],
        } for row in sheet.rows],
    }


def import_script(sheet: StoryboardSheet, *, style: str, frame: str) -> dict:
    """Map authored fields to the chapter script without creative normalization.

    Empty production fields mean unresolved, not 'no characters/no dialogue'.
    Compilation refuses this draft until a technical binding step is available.
    """
    elapsed = Decimal(0)
    shots = []
    for number, row in enumerate(sheet.rows, 1):
        end = elapsed + Decimal(str(row["edit_seconds"]))
        shots.append({
            **row, "index": number, "segment_id": row["authored_id"],
            "source_quote": row["motion_prompt"],
            "edit_start": float(elapsed), "edit_end": float(end),
            "visual_prompt": "", "end_state": "", "light": "", "sfx": "",
            "characters": [], "in_frame": [], "extras": [], "actions": [],
            "turns": [], "avoid": "",
        })
        elapsed = end
    return {
        "video_title": sheet.notes[0] if sheet.notes else sheet.name,
        "profile": {"style": style, "frame": frame},
        "authored_storyboard": {
            "status": "imported_needs_bindings", "source": sheet.source,
            "sheet": sheet.name, "notes": list(sheet.notes),
            "pending": ["逐镜人物和参考资产绑定", "对白与音效分离及画外/记忆声音绑定",
                        "画面起止状态投影", "跨镜声音桥和剪辑预算接入"],
            "edit_budget_seconds": float(elapsed),
        },
        "shots": shots,
    }


def fidelity_report(sheet: StoryboardSheet, script: dict) -> dict:
    """Compare operative imported fields, including omissions/order, not a copy of the source."""
    expected = [s["authored_id"] for s in sheet.rows]
    actual = [s.get("authored_id") for s in script.get("shots", [])]
    differences = []
    if actual != expected:
        differences.append({"field": "shot_order", "expected": expected, "actual": actual})
    elapsed = Decimal(0)
    for row, shot in zip(sheet.rows, script.get("shots", [])):
        for field in (*HEADERS.values(), "source_row"):
            if shot.get(field) != row[field]:
                differences.append({"shot": row["authored_id"], "field": field,
                                    "expected": row[field], "actual": shot.get(field)})
        end = elapsed + Decimal(str(row["edit_seconds"]))
        for field, value in (("edit_start", float(elapsed)), ("edit_end", float(end))):
            if shot.get(field) != value:
                differences.append({"shot": row["authored_id"], "field": field,
                                    "expected": value, "actual": shot.get(field)})
        elapsed = end
    metadata = script.get("authored_storyboard", {})
    for field, value in (("source", sheet.source), ("sheet", sheet.name), ("notes", list(sheet.notes))):
        if metadata.get(field) != value:
            differences.append({"field": field, "expected": value, "actual": metadata.get(field)})
    return {"sheet": sheet.name, "source_shots": len(expected), "imported_shots": len(actual),
            "differences": differences, "text_fidelity": "passed" if not differences else "failed",
            "production_ready": False, "creative_quality_review": "not_run",
            "model_calls": 0, "video_generation_calls": 0}


def require_bound_storyboard(script: dict) -> None:
    if script.get("authored_storyboard", {}).get("status") == "imported_needs_bindings":
        raise ValueError("分镜已保真导入，但尚未绑定人物、声音及剪辑；不能按空对白剧本打包。")


def render_import(script: dict) -> str:
    meta = script["authored_storyboard"]
    lines = [f"# {script['video_title']}", "",
             f"{len(script['shots'])} 镜 · 剪辑预算 {meta['edit_budget_seconds']:g} 秒 · "
             f"{script['profile']['frame']} · {script['profile']['style']}", "",
             "状态：原分镜保真导入。尚未生成视频；人物、声音与剪辑绑定未完成。", "",
             *meta["notes"][1:], ""]
    for shot in script["shots"]:
        lines.extend([f"## {shot['authored_id']} · {shot['edit_start']:g}–{shot['edit_end']:g} 秒", "",
                      f"场景：{shot['location']}", "",
                      f"景别：{shot['shot_scale']}；摄影角度：{shot['camera_angle']}", "",
                      "画面与动作：", "", shot["motion_prompt"], "",
                      "台词与声音（原文）：", "", shot["authored_sound"], "",
                      "机位与连续性：", "", shot["camera"], "",
                      "叙事目的：", "", shot["narrative_purpose"], ""])
    return "\n".join(lines)
