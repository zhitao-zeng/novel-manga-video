from __future__ import annotations

import json
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.planning_export import export_planning_bundle
from novel_manga.planning_validation import validate_planning_bundle


def test_validate_deterministic_planning_bundle(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一章 起点\n\n林舟推开门，看见桌上的旧钥匙。", encoding="utf-8")
    output = tmp_path / "plans"
    settings = Settings(provider="mock", planner_backend="deterministic", output_root=output)
    result = export_planning_bundle(settings, source, novel_id="demo", title="测试小说")

    report = validate_planning_bundle(
        source,
        result["output_directory"],
        novel_id="demo",
        title="测试小说",
    )

    assert report["passed"] is True
    assert report["source_quote_valid_ratio"] == 1.0
    assert report["visible_speaker_violations"] == []
    assert json.loads(Path(report["report"]).read_text(encoding="utf-8"))["passed"] is True
