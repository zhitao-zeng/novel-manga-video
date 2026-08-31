import sys
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.planner import CommandPlanner
from novel_manga.script_planning import evaluate_script_quality


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


def test_command_planner_supports_v5_per_beat_writing_and_direction(tmp_path) -> None:
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
        output_root=tmp_path / "outputs",
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
    assert bundle.quality_report.derived_serves_coverage == 1.0
    assert bundle.updated_series_state.characters[0].current_goal == "阻止开门"
    assert bundle.updated_series_state.previous_episode_end is not None
    assert bundle.episode_contract is not None
    assert bundle.episode_contract.development_version == "v001"
    assert bundle.episode_contract.allowed_event_ids == ["event_001"]
    assert bundle.episode_contract.allowed_information_fact_ids == ["fact_001"]
    assert len(bundle.plan.shots) == 15
    draft_root = (
        settings.output_root
        / novel.novel_id
        / "script_drafts/episode_001/beats"
    )
    assert len(list(draft_root.glob("*/script_accepted.json"))) == 5
    assert len(list(draft_root.glob("*/direction_accepted.json"))) == 5
    assert (draft_root.parent / "series_state_attempt_01.raw.json").is_file()
    assert (draft_root.parent / "series_state_accepted.json").is_file()
    development_root = settings.output_root / novel.novel_id / "series_development"
    assert (development_root / "series_development.v001.json").is_file()
    assert (development_root / "series_development_review.v001.json").is_file()
    assert (development_root / "active.json").is_file()
    assert (
        settings.output_root
        / novel.novel_id
        / "script_drafts/episode_001/episode_contract.json"
    ).is_file()

    withheld = bundle.plan.model_copy(deep=True)
    assert withheld.showrunner_plan is not None
    withheld.showrunner_plan.information_states[0].dramatic_use = "withheld"
    withheld.showrunner_plan.information_states[0].reveal_beat_id = "beat_005"
    withheld_report = evaluate_script_quality(
        withheld,
        bundle.diagnosis,
        novel.episodes[0],
    )
    assert "withheld_fact_release_order_invalid" in {
        issue.code for issue in withheld_report.issues
    }

    choice = bundle.plan.model_copy(deep=True)
    assert choice.dramaturgy is not None
    grounded_row = novel.episodes[0].source_text.splitlines()[-1]
    choice.dramaturgy.episode_mode = "choice_episode"
    choice.dramaturgy.protagonist_choice = "不要打开这扇门"
    choice.dramaturgy.choice_source_quote = grounded_row
    choice.dramaturgy.cost_paid = "林晚低声说"
    choice.dramaturgy.cost_source_quote = grounded_row
    grounded_choice_report = evaluate_script_quality(
        choice,
        bundle.diagnosis,
        novel.episodes[0],
    )
    grounded_codes = {issue.code for issue in grounded_choice_report.issues}
    assert "protagonist_choice_not_grounded" not in grounded_codes
    assert "choice_cost_not_grounded" not in grounded_codes

    choice.dramaturgy.cost_paid = "失去家族身份"
    ungrounded_choice_report = evaluate_script_quality(
        choice,
        bundle.diagnosis,
        novel.episodes[0],
    )
    assert "choice_cost_not_grounded" in {
        issue.code for issue in ungrounded_choice_report.issues
    }
