"""Persistent episode sessions without Docker, a model endpoint or media services."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.application.agents.episode import session
from novel_manga.application.agents.sandbox import SandboxRefused


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(session, "key_for", lambda name: "private-test-token")
    return {"image": "agent-sandbox:test", "base_url": "http://internal-model",
            "model": "configured-model", "key_var": "EPISODE_MODEL_KEY", "effort": "medium"}


def fake_cli(monkeypatch, work_dir, *, outputs=None, code=0):
    calls = []

    def call(command, *, stdout, stderr, env):
        calls.append((command, env))
        flag = "--resume" if "--resume" in command else "--session-id"
        identity = command[command.index(flag) + 1]
        transcript = work_dir / ".agent-home/.claude/projects/-work" / f"{identity}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{"type":"user"}\n', encoding="utf-8")
        stdout.write(json.dumps({"type": "system", "subtype": "init", "session_id": identity}) + "\n")
        for name, value in (outputs or {}).items():
            path = work_dir / "output" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        return code

    monkeypatch.setattr(session.subprocess, "call", call)
    return calls


def test_initial_session_uses_configured_model_only_workspace_and_file_tools(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    calls = fake_cli(monkeypatch, work, outputs={"episode.json": '{"title":"draft"}'})
    result = session.run_session(work, "Write this whole episode", config=config)
    command, environment = calls[0]

    assert result.ok and result.produced == ["episode.json"]
    assert result.record["expected_output_produced"] is True
    assert command[command.index("--session-id") + 1] == result.record["session_id"]
    assert "--resume" not in command and "--continue" not in command
    assert command[command.index("-v") + 1] == f"{work}:/work"
    assert command.count("-v") == 1 and command[command.index("-w") + 1] == "/work"
    assert "HOME=/work/.agent-home" in command
    assert "ANTHROPIC_MODEL=configured-model" in command
    assert command[command.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep"
    assert "--strict-mcp-config" in command and "--fork-session" not in command
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "private-test-token" not in " ".join(command)
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "private-test-token"
    assert "private-test-token" not in (result.directory / "attempt.json").read_text()
    assert (work / ".claude/skills/episode-production/SKILL.md").is_file()
    assert (result.directory / "prompt.txt").read_text() == "Write this whole episode"
    assert (result.directory / "output-after/episode.json").read_text() == '{"title":"draft"}'


def test_repair_resumes_same_session_and_keeps_prior_output_versions(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "draft-v1"})
    first = session.run_session(work, "Draft", config=config)
    calls = fake_cli(monkeypatch, work, outputs={"repair.json": "repair-v1"})
    second = session.run_session(work, "Repair all supplied clip evidence", resume=True, config=config)
    command, _ = calls[0]

    assert command[command.index("--resume") + 1] == first.record["session_id"]
    assert "--session-id" not in command
    assert second.record["session_id"] == first.record["session_id"]
    assert second.directory != first.directory
    assert second.produced == ["repair.json"] and second.kept == 1
    assert second.record["expected_output"] == "repair.json"
    assert (second.directory / "output-before/episode.json").read_text() == "draft-v1"
    assert (first.directory / "output-after/episode.json").read_text() == "draft-v1"
    assert json.loads((work / session.SESSION_FILE).read_text())["last_attempt"] == second.directory.name


def test_workflow_mode_adds_bash_and_resumes_existing_file_session(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "draft-v1"})
    first = session.run_session(work, "Draft", config=config)
    installed_skill = work / ".claude/skills/episode-production/SKILL.md"
    installed_skill.write_text("earlier file-only skill", encoding="utf-8")
    calls = fake_cli(monkeypatch, work, outputs={"episode.json": "draft-v2"})
    executed = session.run_session(work, "Run the workflow client through finish", resume=True,
                                   enable_tools=True, timeout=5400, config=config)
    command, environment = calls[0]

    assert command[command.index("--resume") + 1] == first.record["session_id"]
    assert "--session-id" not in command and "--fork-session" not in command
    assert command[command.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep,Bash"
    assert command.count("-v") == 1 and command[command.index("-v") + 1] == f"{work}:/work"
    assert command[command.index("timeout") + 1] == "5400"
    assert executed.record["enable_tools"] is True
    assert executed.record["expected_output"] == "episode.json"
    assert executed.record["expected_output_produced"] is True
    assert (executed.directory / "skill-before/SKILL.md").read_text() == "earlier file-only skill"
    assert installed_skill.read_text() == (session.SKILL_SOURCE / "SKILL.md").read_text()
    assert (first.directory / "output-after/episode.json").read_text() == "draft-v1"
    assert "private-test-token" not in " ".join(command)
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "private-test-token"


def test_retained_repair_is_not_reported_as_current_produce(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "draft", "repair.json": "old repair"})
    session.run_session(work, "Draft", config=config)
    fake_cli(monkeypatch, work)
    result = session.run_session(work, "Repair", resume=True, config=config)

    assert result.ok and result.produced == [] and result.kept == 2
    assert result.record["expected_output_produced"] is False
    assert (result.directory / "output-before/repair.json").read_text() == "old repair"


def test_same_session_can_correct_initial_episode_structure(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "invalid first draft"})
    first = session.run_session(work, "Draft", config=config)
    calls = fake_cli(monkeypatch, work, outputs={"episode.json": "corrected draft"})
    corrected = session.run_session(work, "Correct the supplied schema errors", resume=True,
                                    expected_output="episode.json", config=config)

    command, _ = calls[0]
    assert command[command.index("--resume") + 1] == first.record["session_id"]
    assert corrected.record["expected_output"] == "episode.json"
    assert corrected.record["expected_output_produced"] is True
    assert corrected.produced == ["episode.json"]
    assert (corrected.directory / "output-before/episode.json").read_text() == "invalid first draft"


def test_rewriting_a_repair_archives_previous_version(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "draft"})
    session.run_session(work, "Draft", config=config)
    fake_cli(monkeypatch, work, outputs={"repair.json": "first repair"})
    first_repair = session.run_session(work, "Repair", resume=True, config=config)
    fake_cli(monkeypatch, work, outputs={"repair.json": "next repair"})
    next_repair = session.run_session(work, "Repair again", resume=True, config=config)

    assert next_repair.produced == ["repair.json"]
    assert (next_repair.directory / "output-before/repair.json").read_text() == "first repair"
    assert (next_repair.directory / "output-after/repair.json").read_text() == "next repair"
    assert (first_repair.directory / "output-after/repair.json").read_text() == "first repair"


def test_same_size_rewrite_is_detected_on_coarse_timestamp_filesystem(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "version-one"})
    first = session.run_session(work, "Draft", config=config)
    previous = (work / "output/episode.json").stat()
    calls = fake_cli(monkeypatch, work, outputs={"episode.json": "version-two"})
    cli_call = session.subprocess.call

    def preserve_timestamp(*args, **kwargs):
        code = cli_call(*args, **kwargs)
        session.os.utime(work / "output/episode.json", ns=(previous.st_atime_ns, previous.st_mtime_ns))
        return code

    monkeypatch.setattr(session.subprocess, "call", preserve_timestamp)
    result = session.run_session(work, "Correct structure", resume=True,
                                 expected_output="episode.json", config=config)
    assert result.produced == ["episode.json"]
    assert result.record["session_id"] == first.record["session_id"]
    assert calls


def test_resume_requires_an_explicit_saved_session_and_transcript(tmp_path, config):
    work = tmp_path / "episode"
    with pytest.raises(SandboxRefused, match="No saved"):
        session.run_session(work, "Repair", resume=True, config=config)
    work.mkdir()
    (work / session.SESSION_FILE).write_text('{"session_id":"bad-id"}')
    with pytest.raises(SandboxRefused, match="missing or invalid"):
        session.run_session(work, "Repair", resume=True, config=config)
    (work / session.SESSION_FILE).write_text('{"session_id":"11111111-1111-4111-8111-111111111111"}')
    with pytest.raises(SandboxRefused, match="transcript is missing"):
        session.run_session(work, "Repair", resume=True, config=config)


def test_existing_session_is_not_replaced_by_accidental_fresh_call(tmp_path, monkeypatch, config):
    work = tmp_path / "episode"
    fake_cli(monkeypatch, work, outputs={"episode.json": "draft"})
    first = session.run_session(work, "Draft", config=config)
    with pytest.raises(SandboxRefused, match="resume=True"):
        session.run_session(work, "New draft", config=config)
    assert json.loads((work / session.SESSION_FILE).read_text())["session_id"] == first.record["session_id"]
    assert (work / "output/episode.json").read_text() == "draft"


def test_missing_key_does_not_create_attempt_or_workspace(tmp_path, monkeypatch, config):
    monkeypatch.setattr(session, "key_for", lambda name: "")
    work = tmp_path / "episode"
    with pytest.raises(SandboxRefused, match="No credential"):
        session.run_session(work, "Draft", config=config)
    assert not work.exists()


def test_a_docker_start_failure_still_preserves_attempt_evidence(tmp_path, monkeypatch, config):
    def fail(*args, **kwargs):
        raise FileNotFoundError("docker is unavailable")

    monkeypatch.setattr(session.subprocess, "call", fail)
    result = session.run_session(tmp_path / "episode", "Draft", config=config)

    assert result.exit_code == 127 and not result.ok
    assert result.produced == [] and not result.record["expected_output_produced"]
    assert "docker is unavailable" in (result.directory / "stderr.log").read_text()
    assert (result.directory / "attempt.json").is_file()


def test_only_the_episode_skill_is_installed(tmp_path, config):
    work = tmp_path / "episode"
    (work / ".claude/skills/old-method").mkdir(parents=True)
    with pytest.raises(SandboxRefused, match="other skills"):
        session.run_session(work, "Draft", config=config)
    assert not (work / session.SESSION_FILE).exists()
