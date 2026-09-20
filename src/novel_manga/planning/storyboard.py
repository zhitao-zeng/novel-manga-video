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
                if (columns["edit_seconds"] in formulas
                        and not any(own[f] for f in HEADERS.values() if f not in {"authored_id", "edit_seconds"})):
                    # The workbook's SUM footer is a note, not a shot. Its
                    # cached total must not replace the sum of actual cuts.
                    if own["authored_id"]:
                        notes.append(own["authored_id"])
                    continue
                row_number = int(row.attrib["r"])
                where = f"{sheet.get('name')}!{row_number}"
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
