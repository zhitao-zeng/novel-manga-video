import sys
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.planner import CommandPlanner


def test_command_planner_can_be_driven_by_a_non_codex_model_adapter(tmp_path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一章 门外\n林晚低声说：“不要开门。”", encoding="utf-8")
    adapter = Path(__file__).parent / "fixtures" / "fake_model_adapter.py"
    settings = Settings(
        planner_backend="command",
        planner_command=f"{sys.executable} {adapter} planner",
    )
    planner = CommandPlanner(settings)
    novel = read_novel(source, novel_id="command-planner")
    bible = planner.build_bible(novel)
    plan = planner.plan_episode(novel, novel.episodes[0], bible)
    assert bible.characters[0].name == "林晚"
    assert plan.shots[0].turns[0].speaking is True
    assert plan.shots[0].turns[0].text == "不要开门。"


def test_command_planner_supports_the_full_v4_writing_harness(tmp_path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text(
        "第一章 门外\n林晚低声说：“无论听见什么声音，都不要打开这扇门。”",
        encoding="utf-8",
    )
    adapter = Path(__file__).parent / "fixtures" / "fake_model_adapter.py"
    settings = Settings(
        planner_backend="command",
        planner_command=f"{sys.executable} {adapter} planner",
        creative_profile="short-drama-adaptive-v1",
    )
    planner = CommandPlanner(settings)
    novel = read_novel(source, novel_id="command-planner-v4")
    bible = planner.build_bible(novel)

    bundle = planner.plan_episode_bundle(novel, novel.episodes[0], bible)

    assert bundle.quality_report.passed is True
    assert bundle.plan.shots[0].event_ids == ["event_001"]
    assert bundle.plan.showrunner_plan is not None
    assert bundle.plan.showrunner_plan.planning_mode == "planner"
    assert bundle.quality_report.retention_beat_coverage == 1.0
    assert bundle.updated_series_state.characters[0].current_goal == "阻止开门"
