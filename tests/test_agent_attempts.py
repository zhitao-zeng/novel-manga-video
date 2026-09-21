"""A sandbox run has a stable name; each execution of it is an attempt, and they were the same thing.

Everything that needed to tell them apart answered for itself, and the answers contradicted: the
skills were kept because the directory existed, the logs were emptied because they existed, the old
outputs were counted because they existed, and the whole directory was deleted because it existed.
"""
from __future__ import annotations

import json

import pytest

from novel_manga.application.agents import sandbox as runner


def sandbox(tmp_path, *, skills="merge"):
    """A runs root with one installed skill template and one run ready to go."""
    runs = tmp_path / "runs"
    template = runs / "template" / ".claude" / "skills"
    template.mkdir(parents=True, exist_ok=True)
    (template / "method.md").write_text("第一版方法", encoding="utf-8")
    run_dir = runs / "book-merge"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text("干活", encoding="utf-8")
    config = {"runs_root": str(runs), "skills": {skills: "template"}, "key_var": "SANDBOX_KEY",
              "model": "m", "base_url": "http://endpoint", "image": "img"}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return runs, run_dir


# --- the key --------------------------------------------------------------------------------------

def test_the_key_comes_from_the_environment_as_well_as_the_env_file(monkeypatch, tmp_path):
    """A container or scheduler injects credentials by exporting them; reading only .env told it the
    key was missing while it sat in the environment."""
    monkeypatch.setattr(runner, "ROOT", tmp_path)          # no .env here
    monkeypatch.setenv("SANDBOX_KEY", "from-the-environment")
    assert runner.key_for("SANDBOX_KEY") == "from-the-environment"
    monkeypatch.delenv("SANDBOX_KEY")
    assert runner.key_for("SANDBOX_KEY") == ""
    (tmp_path / ".env").write_text("SANDBOX_KEY=from-the-file\n", encoding="utf-8")
    assert runner.key_for("SANDBOX_KEY") == "from-the-file"


# --- the skills -----------------------------------------------------------------------------------

def test_skills_are_identified_by_their_contents_not_by_a_directory_existing(tmp_path):
    runs, run_dir = sandbox(tmp_path)
    template = runs / "template" / ".claude"
    assert runner.install_skills(run_dir, template, replace=False) == runner.skills_digest(template)
    assert (run_dir / ".claude" / "skills" / "method.md").read_text(encoding="utf-8") == "第一版方法"


def test_reusing_a_run_with_different_skills_is_refused_rather_than_run_silently(tmp_path):
    """"Change the skill name and run it again" was not an experiment: the old skills stayed, and
    nothing said so."""
    runs, run_dir = sandbox(tmp_path)
    other = runs / "other" / ".claude" / "skills"
    other.mkdir(parents=True, exist_ok=True)
    (other / "method.md").write_text("另一套方法", encoding="utf-8")
    runner.install_skills(run_dir, runs / "template" / ".claude", replace=False)
    with pytest.raises(runner.SandboxRefused, match="--replace-skills"):
        runner.install_skills(run_dir, other.parent, replace=False)
    assert (run_dir / ".claude" / "skills" / "method.md").read_text(encoding="utf-8") == "第一版方法"


def test_an_updated_template_is_also_a_different_skill_set(tmp_path):
    runs, run_dir = sandbox(tmp_path)
    template = runs / "template" / ".claude"
    runner.install_skills(run_dir, template, replace=False)
    (template / "skills" / "method.md").write_text("第二版方法", encoding="utf-8")
    with pytest.raises(runner.SandboxRefused):
        runner.install_skills(run_dir, template, replace=False)
    assert runner.install_skills(run_dir, template, replace=True) == runner.skills_digest(template)
    assert (run_dir / ".claude" / "skills" / "method.md").read_text(encoding="utf-8") == "第二版方法"


# --- the attempt ----------------------------------------------------------------------------------

def run_once(tmp_path, monkeypatch, *, exit_code, writes=None):
    runs, run_dir = sandbox(tmp_path)
    monkeypatch.setenv("SANDBOX_KEY", "k")

    def fake_docker(command, stdout=None, stderr=None, env=None):
        stdout.write('{"type":"result"}\n')
        stderr.write("docker said something\n")
        for name, text in (writes or {}).items():
            path = run_dir / "output" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return exit_code

    monkeypatch.setattr(runner.subprocess, "call", fake_docker)
    attempt = runner.run_agent("book-merge", skills="merge",
                               config=json.loads((tmp_path / "config.json").read_text(encoding="utf-8")),
                               log=lambda line: None)
    return attempt.exit_code, run_dir, sorted((run_dir / "attempts").iterdir())


def test_a_docker_that_never_started_cannot_wipe_the_log_that_says_why(tmp_path, monkeypatch):
    """Both logs were opened "w" before docker was called, so a start-layer failure (125) truncated
    the previous session and its errors before anything could be learnt from either."""
    code, run_dir, first = run_once(tmp_path, monkeypatch, exit_code=0, writes={"export/out.json": "{}"})
    assert code == 0 and len(first) == 1
    kept = (first[0] / "session.jsonl", first[0] / "stderr.log")

    code, run_dir, both = run_once(tmp_path, monkeypatch, exit_code=125)
    assert code == 125 and len(both) == 2
    assert all(path.exists() and path.stat().st_size for path in kept)  # the earlier attempt is intact
    assert json.loads((both[0] / "attempt.json").read_text(encoding="utf-8"))["exit"] == 0


def test_an_attempt_that_wrote_nothing_does_not_report_the_previous_ones_files(tmp_path, monkeypatch):
    run_once(tmp_path, monkeypatch, exit_code=0, writes={"export/story_bible.json": "{}"})
    code, run_dir, attempts = run_once(tmp_path, monkeypatch, exit_code=1)
    record = json.loads((attempts[-1] / "attempt.json").read_text(encoding="utf-8"))
    assert record["produced"] == []
    assert record["kept_from_earlier_attempts"] == 1
    assert (run_dir / "output" / "export" / "story_bible.json").is_file()  # still there for a consumer


def test_the_record_says_which_skills_and_model_actually_ran(tmp_path, monkeypatch):
    _, run_dir, attempts = run_once(tmp_path, monkeypatch, exit_code=0, writes={"a.json": "{}"})
    record = json.loads((attempts[0] / "attempt.json").read_text(encoding="utf-8"))
    assert record["skills"] == "merge"
    assert record["skills_digest"] == runner.skills_digest(run_dir / ".claude")
    assert record["produced"] == ["a.json"] and record["exit"] == 0
    assert (run_dir / "latest").resolve() == attempts[0].resolve()
