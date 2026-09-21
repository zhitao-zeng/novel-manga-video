"""The sandbox as a planning backend: an explicit choice, three states, and the same binding path.

The pieces all existed; what was missing was anything that said which chapter is at which stage, so
the hand-off was a person running docker, finding the output file and pasting its path into
--bind-storyboard.  That is not a pipeline: it cannot be resumed, and nothing records which take was
chosen or which skills produced it.
"""
from __future__ import annotations

import json

import pytest

from novel_manga.application.agents import storyboard as agent_storyboard
from novel_manga.application.agents.sandbox import Attempt, SandboxRefused
from novel_manga.application.profiles import DEFAULTS, load_profile


def book(tmp_path, **profile):
    novel = tmp_path / "book"
    (novel / "book_3").mkdir(parents=True, exist_ok=True)
    (novel / "profile.json").write_text(json.dumps({**DEFAULTS, **profile}, ensure_ascii=False),
                                        encoding="utf-8")
    return novel


# --- the profile says how, separately from which method ------------------------------------------

def test_the_backend_is_an_explicit_choice_and_defaults_to_local(tmp_path):
    novel = book(tmp_path)
    assert load_profile(novel).get("planning_backend", "local") == "local"


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(ValueError, match="planning_backend"):
        load_profile(book(tmp_path, planning_backend="whatever"))


def test_the_sandbox_backend_has_to_name_a_skill(tmp_path):
    """story_method picks a method the local planner follows; this picks where planning happens.  One
    field for both would make story_method mean a prompt flow sometimes and a container run others."""
    with pytest.raises(ValueError, match="agent_skill"):
        load_profile(book(tmp_path, planning_backend="sandbox_agent"))
    profile = load_profile(book(tmp_path / "ok", planning_backend="sandbox_agent", agent_skill="shanyin"))
    assert profile["agent_skill"] == "shanyin"


# --- three states, written down ------------------------------------------------------------------

def sandbox_config(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return {"runs_root": str(runs), "skills": {"shanyin": "template"}, "key_var": "K",
            "model": "m", "base_url": "http://endpoint", "image": "img"}


def proposed(tmp_path, monkeypatch, *, produced, attempt_name="20260921-000000-shanyin"):
    novel = book(tmp_path, planning_backend="sandbox_agent", agent_skill="shanyin")
    episode = novel / "book_3"
    config = sandbox_config(tmp_path)
    run = agent_storyboard.run_name(novel.name, 3, "shanyin")
    attempt_dir = tmp_path / "runs" / run / "attempts" / attempt_name
    attempt_dir.mkdir(parents=True, exist_ok=True)
    for name in produced:
        path = tmp_path / "runs" / run / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sheet")

    monkeypatch.setattr(agent_storyboard, "run_agent",
                        lambda *a, **k: Attempt(run=run, directory=attempt_dir, exit_code=0,
                                                seconds=1, produced=list(produced)))
    monkeypatch.setattr("novel_manga.planning.authored_brief.write_brief", lambda *a, **k: [])
    monkeypatch.setattr("novel_manga.planning.authored_brief.run_prompt", lambda *a, **k: "干活")
    (novel / "novel.json").write_text(json.dumps({"title": "书", "chapters": [{"index": 3}]}),
                                      encoding="utf-8")
    return novel, episode, config, agent_storyboard.propose(novel, episode, 3, "shanyin",
                                                            config=config, log=lambda *a: None)


def test_a_chapter_starts_with_nothing_asked_for(tmp_path):
    novel = book(tmp_path)
    assert agent_storyboard.state(novel / "book_3").status == "none"
    assert agent_storyboard.accepted_sheet(novel / "book_3") is None


def test_a_run_that_produced_sheets_becomes_a_candidate(tmp_path, monkeypatch):
    _, episode, config, current = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx", "notes.md"])
    assert current.status == "candidate"
    assert current.sheets == ["output/分镜表.xlsx"]      # the notes are not a storyboard
    assert agent_storyboard.accepted_sheet(episode, config=config) is None   # nobody has chosen


def test_a_run_that_produced_nothing_is_still_recorded(tmp_path, monkeypatch):
    """exit=0 is not "there is a storyboard".  An empty attempt is a visible outcome, not a silence."""
    _, episode, _, current = proposed(tmp_path, monkeypatch, produced=[])
    assert current.status == "candidate" and current.sheets == []
    assert current.attempt == "20260921-000000-shanyin"


def test_accepting_a_take_records_the_choice_and_hands_planning_the_sheet(tmp_path, monkeypatch):
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    agent_storyboard.accept(episode, "output/分镜表.xlsx", sheet_name="第3集", config=config)
    current = agent_storyboard.state(episode)
    assert current.status == "accepted" and current.accepted_at
    sheet, name = agent_storyboard.accepted_sheet(episode, config=config)
    assert sheet.is_file() and name == "第3集"


def test_accepting_a_sheet_that_is_not_there_is_refused(tmp_path, monkeypatch):
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    with pytest.raises(SandboxRefused, match="不在"):
        agent_storyboard.accept(episode, "output/不存在.xlsx", config=config)


def test_nothing_can_be_accepted_before_anything_was_asked_for(tmp_path):
    novel = book(tmp_path)
    with pytest.raises(SandboxRefused, match="还没有候选"):
        agent_storyboard.accept(novel / "book_3", "output/x.xlsx", config=sandbox_config(tmp_path))


# --- the choice is a version, not a path ----------------------------------------------------------

def rewrite(tmp_path, monkeypatch, content):
    """What a rerun does: the same run name, the same output path, different bytes."""
    novel = tmp_path / "book"
    run = agent_storyboard.run_name(novel.name, 3, "shanyin")
    (tmp_path / "runs" / run / "output" / "分镜表.xlsx").write_bytes(content)


def test_the_accepted_take_is_the_one_that_was_accepted(tmp_path, monkeypatch):
    """output/ is shared by every attempt of a run, so a rerun rewrites the same file.  The accepted
    record kept pointing at that path, and planning silently bound whatever was newest."""
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    rewrite(tmp_path, monkeypatch, b"V1")
    agent_storyboard.accept(episode, "output/分镜表.xlsx", config=config)
    sheet, _ = agent_storyboard.accepted_sheet(episode, config=config)
    assert sheet.read_bytes() == b"V1"

    rewrite(tmp_path, monkeypatch, b"V2")                      # a later attempt overwrites it
    sheet, _ = agent_storyboard.accepted_sheet(episode, config=config)
    assert sheet.read_bytes() == b"V1"                         # planning still binds what was chosen


def test_a_new_proposal_adds_candidates_and_does_not_unchoose(tmp_path, monkeypatch):
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    rewrite(tmp_path, monkeypatch, b"V1")
    agent_storyboard.accept(episode, "output/分镜表.xlsx", config=config)
    proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx", "另一版.xlsx"],
             attempt_name="20260921-010000-shanyin")
    current = agent_storyboard.state(episode)
    assert current.status == "accepted"                        # the choice survives the rerun
    assert "output/另一版.xlsx" in current.sheets              # and the new takes are on offer
    assert agent_storyboard.accepted_sheet(episode, config=config)[0].read_bytes() == b"V1"


def test_a_snapshot_edited_in_place_is_refused_rather_than_bound(tmp_path, monkeypatch):
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    rewrite(tmp_path, monkeypatch, b"V1")
    agent_storyboard.accept(episode, "output/分镜表.xlsx", config=config)
    sheet, _ = agent_storyboard.accepted_sheet(episode, config=config)
    sheet.write_bytes(b"edited by hand")
    with pytest.raises(SandboxRefused, match="内容变了"):
        agent_storyboard.accepted_sheet(episode, config=config)


def test_the_record_says_which_attempt_the_take_came_from(tmp_path, monkeypatch):
    _, episode, config, _ = proposed(tmp_path, monkeypatch, produced=["分镜表.xlsx"])
    rewrite(tmp_path, monkeypatch, b"V1")
    current = agent_storyboard.accept(episode, "output/分镜表.xlsx", config=config)
    assert current.accepted_from == "20260921-000000-shanyin:output/分镜表.xlsx"
    assert current.sheet_digest == agent_storyboard.digest_of(episode / current.sheet)
