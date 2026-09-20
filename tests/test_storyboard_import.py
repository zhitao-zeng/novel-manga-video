"""Authored edits must survive ingestion without being treated as silent video."""
import copy
import json
import sys
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from novel_manga.planning.storyboard import (
    HEADERS, read_workbook, import_script, fidelity_report, require_bound_storyboard,
)
from novel_manga.application.planning import cli
from novel_manga.application.packing import flow as packing_flow, service


S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def workbook(path, *, strings="str", budget="2", duplicate=False, budget_formula=False):
    """Small source fixture: cold open → flashback → return, then a sound exit."""
    book = ET.Element(f"{{{S}}}workbook")
    ET.SubElement(ET.SubElement(book, f"{{{S}}}sheets"), f"{{{S}}}sheet",
                  {"name": "导演稿", "sheetId": "5", f"{{{R}}}id": "r5"})
    rels = ET.Element(f"{{{P}}}Relationships")
    ET.SubElement(rels, f"{{{P}}}Relationship", {"Id": "r5", "Target": "/xl/worksheets/custom.xml"})
    xml = ET.Element(f"{{{S}}}worksheet")
    data = ET.SubElement(xml, f"{{{S}}}sheetData")
    shared = ET.Element(f"{{{S}}}sst")
    shared_count = 0
    rows = [
        ["导演原稿"], ["16:9；记忆中的家人不入镜"], list(HEADERS),
        ["C01", "平视", "中景", "他举着地图，只见身后的天空。", "冷开场·坡顶·日",
         "云烨：不可能。我就住这儿。", "固定；不揭示盆地。", "先提出疑问", budget],
        ["C01" if duplicate else "A04", "俯拍", "手部特写", "成年人的手伸向水中的反光。", "记忆·水潭·日",
         "骤起水声。", "硬切进入和离开记忆。", "保留身体变化的原因", "3"],
        ["E11", "平视", "近景", "双手先停住，随后侧头。", "营地·夜",
         "儿子［记忆声］：爸，我那份多放点辣。云烨：你上回还嫌——。", "先拍手再拍反应。", "刺激引出反应", "4"],
        ["F29", "平视", "大全景", "第一眼看见空旷盆地。", "坡顶望盆地·日",
         "城市记忆声在切入瞬间停止；真实风与鸟鸣不断。", "不得提前展示。", "撤走希望", "7"],
    ]
    for i, values in enumerate(rows, 1):
        row = ET.SubElement(data, f"{{{S}}}row", {"r": str(i)})
        for j, value in enumerate(values):
            numeric = i >= 4 and j == 8
            cell = ET.SubElement(row, f"{{{S}}}c", {"r": f"{chr(65+j)}{i}", "t": "n" if numeric else strings})
            if budget_formula and numeric and i == 4:
                ET.SubElement(cell, f"{{{S}}}f").text = "1+1"
            if strings == "s" and not numeric:
                ET.SubElement(ET.SubElement(shared, f"{{{S}}}si"), f"{{{S}}}t").text = value
                value = str(shared_count)
                shared_count += 1
            if strings == "inlineStr" and not numeric:
                ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = value
            else:
                ET.SubElement(cell, f"{{{S}}}v").text = value
        if i >= 4:
            # Bad cached time codes are common after users edit budget cells.
            cell = ET.SubElement(row, f"{{{S}}}c", {"r": f"K{i}", "t": "n"})
            ET.SubElement(cell, f"{{{S}}}f").text = "0"
            ET.SubElement(cell, f"{{{S}}}v").text = "999"
    footer = ET.SubElement(data, f"{{{S}}}row", {"r": "9"})
    cell = ET.SubElement(footer, f"{{{S}}}c", {"r": "A9", "t": "str"})
    ET.SubElement(cell, f"{{{S}}}v").text = "版本总计"
    cell = ET.SubElement(footer, f"{{{S}}}c", {"r": "I9", "t": "n"})
    ET.SubElement(cell, f"{{{S}}}f").text = "SUM(I4:I7)"
    ET.SubElement(cell, f"{{{S}}}v").text = "999"
    with ZipFile(path, "w") as z:
        for name, root in (("xl/workbook.xml", book), ("xl/_rels/workbook.xml.rels", rels),
                           ("xl/worksheets/custom.xml", xml), ("xl/sharedStrings.xml", shared)):
            z.writestr(name, ET.tostring(root, encoding="utf-8"))
    return path


@pytest.mark.parametrize("strings", ["str", "s", "inlineStr"])
def test_full_authored_columns_and_presentation_order_survive_xlsx(tmp_path, strings):
    sheet, = read_workbook(workbook(tmp_path / "source.xlsx", strings=strings))
    before = copy.deepcopy(sheet.rows)
    script = import_script(sheet, style="3d", frame="16:9")
    assert sheet.rows == before
    assert script == import_script(sheet, style="3d", frame="16:9")
    assert [s["authored_id"] for s in script["shots"]] == ["C01", "A04", "E11", "F29"]
    assert script["shots"][1]["location"] == "记忆·水潭·日"
    assert "成年人的手" in script["shots"][1]["motion_prompt"]
    assert script["shots"][2]["authored_sound"] == "儿子［记忆声］：爸，我那份多放点辣。云烨：你上回还嫌——。"
    assert "切入瞬间停止" in script["shots"][3]["authored_sound"]
    assert script["shots"][0]["camera"] == "固定；不揭示盆地。"
    assert [(s["edit_start"], s["edit_end"]) for s in script["shots"]] == [(0, 2), (2, 5), (5, 9), (9, 16)]
    assert script["authored_storyboard"]["edit_budget_seconds"] == 16
    assert fidelity_report(sheet, script)["differences"] == []


@pytest.mark.parametrize("change", ["order", "omit", "sound", "camera", "time"])
def test_comparison_catches_actual_output_losses(tmp_path, change):
    sheet, = read_workbook(workbook(tmp_path / "source.xlsx"))
    script = import_script(sheet, style="3d", frame="16:9")
    if change == "order":
        script["shots"].reverse()
    elif change == "omit":
        script["shots"].pop()
    elif change == "time":
        script["shots"][0]["edit_end"] = 4  # Native generation minimum isn't edit length.
    else:
        script["shots"][2]["authored_sound" if change == "sound" else "camera"] = ""
    assert fidelity_report(sheet, script)["text_fidelity"] == "failed"


@pytest.mark.parametrize("options", [{"budget": ""}, {"budget": "0"}, {"budget": "NaN"},
                                    {"duplicate": True}, {"budget_formula": True}])
def test_bad_source_rows_do_not_silently_disappear(tmp_path, options):
    with pytest.raises(ValueError):
        read_workbook(workbook(tmp_path / "bad.xlsx", **options))


def test_cli_import_never_invokes_novel_planner_or_overwrites_episode(tmp_path, monkeypatch):
    source = workbook(tmp_path / "source.xlsx")
    def forbidden(*args, **kwargs):
        pytest.fail("authored input must not enter model-based novel adaptation")
    monkeypatch.setattr(cli, "run", forbidden)
    monkeypatch.setattr(sys, "argv", ["plan_chapter_thin.py", str(source), "--storyboard-sheet", "导演稿",
        "--novel-id", "trial", "--output-root", str(tmp_path / "out"), "--style", "3d", "--frame", "16:9"])
    assert cli.main() == 0
    directory = tmp_path / "out/trial/trial_1"
    saved = (directory / "chapter_script.json").read_bytes()
    report = json.loads((directory / "chapter_script_report.json").read_text())
    assert report["text_fidelity"] == "passed" and report["production_ready"] is False
    assert not (directory / "clip_plan.json").exists()
    assert "你上回还嫌——。" in (directory / "chapter_script.md").read_text()
    with pytest.raises(SystemExit):
        cli.main()
    assert (directory / "chapter_script.json").read_bytes() == saved


def test_unbound_draft_cannot_become_a_silent_plan_or_invalidate_old_media(tmp_path, monkeypatch):
    sheet, = read_workbook(workbook(tmp_path / "source.xlsx"))
    script = import_script(sheet, style="3d", frame="16:9")
    (tmp_path / "chapter_script.json").write_text(json.dumps(script))
    (tmp_path / "thin_media_report.json").write_text('{"existing":"keep"}')
    (tmp_path / "clip_plan.json").write_text('{"existing":"keep"}')
    def forbidden(*args, **kwargs):
        pytest.fail("unbound draft must be rejected before asset/identity IO")
    monkeypatch.setattr(packing_flow, "load_context", forbidden)
    for call in (lambda: service.prepared_shots(script, tmp_path),
                 lambda: packing_flow.run(SimpleNamespace(episode_dir=tmp_path))):
        with pytest.raises(ValueError, match="尚未绑定"):
            call()
    assert json.loads((tmp_path / "clip_plan.json").read_text()) == {"existing": "keep"}
    assert json.loads((tmp_path / "thin_media_report.json").read_text()) == {"existing": "keep"}
    require_bound_storyboard({"shots": []})  # Existing novel route remains unchanged.
