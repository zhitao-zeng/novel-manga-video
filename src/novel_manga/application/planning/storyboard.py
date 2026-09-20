"""Authored storyboard import IO; deliberately bypasses novel adaptation."""
from pathlib import Path
import json

from novel_manga.planning.storyboard import read_workbook, import_script, fidelity_report, render_import
from novel_manga.util import atomic_write_json
from novel_manga.application.planning.flow import PlanningInputError


def run(args) -> int:
    try:
        sheets = read_workbook(Path(args.source))
    except (ValueError, OSError) as error:
        raise PlanningInputError(str(error)) from error
    sheet = next((s for s in sheets if s.name == args.storyboard_sheet), None)
    if sheet is None:
        raise PlanningInputError("请选择分镜工作表：" + "、".join(s.name for s in sheets))
    directory = Path(args.output_root).resolve() / args.novel_id / f"{args.novel_id}_{args.episode_index}"
    # A draft must never invalidate a production plan/report or overwrite work.
    if directory.exists() and any(directory.iterdir()):
        raise PlanningInputError(f"分镜导入请使用新的产物目录；已存在内容：{directory}")
    script = import_script(sheet, style=args.style, frame=args.frame)
    report = fidelity_report(sheet, script)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / "chapter_script.json", script)
    atomic_write_json(directory / "chapter_script_report.json", report)
    (directory / "chapter_script.md").write_text(render_import(script), encoding="utf-8")
    print(json.dumps({"directory": str(directory), **report}, ensure_ascii=False))
    return 0
