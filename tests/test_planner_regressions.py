from novel_manga.ingest import read_novel
from novel_manga.planner import DeterministicPlanner


def test_deterministic_plan_keeps_closing_quote_and_extracts_real_names(tmp_path):
    source = tmp_path / "novel.txt"
    source.write_text(
        "第一章 雨夜\n林晚推开门。程野看向她，低声说：“里面没有人。”",
        encoding="utf-8",
    )
    novel = read_novel(source, novel_id="1")
    planner = DeterministicPlanner()
    bible = planner.build_bible(novel)
    plan = planner.plan_episode(novel, novel.episodes[0], bible)
    assert {character.name for character in bible.characters} >= {"林晚", "程野"}
    assert "低声" not in {character.name for character in bible.characters}
    assert all(shot.narration not in {"”", "’", "」"} for shot in plan.shots)
    assert plan.shots[-1].narration.endswith("。”")
    assert plan.shots[-1].turns[-1].speaking is True
    assert plan.shots[-1].turns[-1].speaker_name == "程野"
    assert plan.shots[-1].turns[-1].text == "里面没有人。"


def test_deterministic_planner_extracts_name_before_speech_adverb(tmp_path):
    source = tmp_path / "novel.txt"
    source.write_text("第一章 门外\n林晚低声说：“不要开门。”", encoding="utf-8")
    novel = read_novel(source, novel_id="2")
    planner = DeterministicPlanner()
    bible = planner.build_bible(novel)
    plan = planner.plan_episode(novel, novel.episodes[0], bible)
    assert [character.name for character in bible.characters] == ["林晚"]
    assert "门外" in bible.locations
    assert plan.shots[0].turns[-1].speaker_name == "林晚"
    assert plan.shots[0].turns[-1].speaking is True


def test_deterministic_planner_never_silently_samples_long_source(tmp_path):
    source = tmp_path / "novel.txt"
    sentences = [f"这是第{index}个必须保留的事件。" for index in range(1, 36)]
    source.write_text("第一章 完整性\n" + "".join(sentences), encoding="utf-8")
    novel = read_novel(source, novel_id="3")
    planner = DeterministicPlanner()
    bible = planner.build_bible(novel)
    plan = planner.plan_episode(novel, novel.episodes[0], bible)
    assert len(plan.shots) == 35
    assert [shot.source_quote for shot in plan.shots] == sentences
