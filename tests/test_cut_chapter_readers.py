"""The readers that walk a novel's episode directories see a cut chapter's parts.

Twenty-odd places guarded their walk with `tail.isdigit()`, which skipped meiman-daoshi_12-2 in silence:
the identity ledger lost a cut chapter's text, cast completion never visited its parts, the phase-card
lookup returned no chapter for them.  They read the name through novel_manga.episodes now.
"""
from __future__ import annotations

import json
from pathlib import Path

from novel_manga.application.identity import ledger_store, phases
from novel_manga.application.identity.index import chapter_texts as index_chapter_texts
from novel_manga.application.planning.cast_completion import episodes_of


def _segments(episode_dir: Path, *texts: str) -> None:
    episode_dir.mkdir(parents=True)
    (episode_dir / "segments.json").write_text(
        json.dumps([{"segment_id": f"seg_{i}", "text": t} for i, t in enumerate(texts, 1)], ensure_ascii=False), encoding="utf-8")


def test_a_cut_chapters_text_is_read_back_from_its_parts_in_order(tmp_path):
    novel = tmp_path / "m"
    _segments(novel / "m_11", "第十一章。")
    _segments(novel / "m_12-2", "中集甲", "中集乙")
    _segments(novel / "m_12-1", "上集")
    _segments(novel / "m_12-3", "下集")
    (novel / "series_assets").mkdir()
    texts = ledger_store.chapter_texts(novel)
    assert texts == {11: "第十一章。", 12: "上集\n中集甲\n中集乙\n下集"}
    assert index_chapter_texts is ledger_store.chapter_texts


def test_the_cut_outranks_a_segments_file_the_chapter_kept_from_before_it(tmp_path):
    novel = tmp_path / "m"
    _segments(novel / "m_12", "整章一集时的分段")
    _segments(novel / "m_12-1", "上集")
    _segments(novel / "m_12-2", "下集")
    assert ledger_store.chapter_texts(novel)[12] == "上集\n下集"


def test_a_part_wears_its_chapters_phase():
    assert phases.chapter_of(Path("/x/meiman-daoshi_12-2")) == 12
    assert phases.chapter_of(Path("/x/meiman-daoshi_12")) == 12
    assert phases.chapter_of(Path("/x/series_assets")) is None


def test_cast_completion_visits_a_wanted_chapters_parts(tmp_path):
    novel = tmp_path / "m"
    for name in ("m_11", "m_12-1", "m_12-2", "m_13"):
        (novel / name).mkdir(parents=True)
        (novel / name / "chapter_script.json").write_text("{}", encoding="utf-8")
    (novel / "m_notes").mkdir()
    (novel / "m_notes" / "chapter_script.json").write_text("{}", encoding="utf-8")
    assert [(n, p.parent.name) for n, p in episodes_of(novel, {12})] == [(12, "m_12-1"), (12, "m_12-2")]
    assert [p.parent.name for _, p in episodes_of(novel, None)] == ["m_11", "m_12-1", "m_12-2", "m_13"]
