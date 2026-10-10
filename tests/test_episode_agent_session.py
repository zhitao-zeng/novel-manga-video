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


def fake_cli(monkeypatch, work_dir, *, outputs=None, code=0, result=None):
    calls = []

    def call(command, *, stdout, stderr, env):
        calls.append((command, env))
        flag = "--resume" if "--resume" in command else "--session-id"
        identity = command[command.index(flag) + 1]
        transcript = work_dir / ".agent-home/.claude/projects/-work" / f"{identity}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{"type":"user"}\n', encoding="utf-8")
        stdout.write(json.dumps({"type": "system", "subtype": "init", "session_id": identity}) + "\n")
        if result:
            stdout.write(json.dumps(result) + '\n')
        for name, value in (outputs or {}).items():
            path = work_dir / "output" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        return code

    monkeypatch.setattr(session.subprocess, "call", call)
    return calls


def test_cli_turn_limit_is_recorded_as_resumable_without_claiming_success(tmp_path, monkeypatch, config):
    work = tmp_path/'episode'
    calls = fake_cli(monkeypatch, work, code=1, result={
        'type': 'result', 'subtype': 'error_max_turns', 'is_error': True, 'num_turns': 16})
    result = session.run_session(work, 'Draft', config={**config, 'max_turns': 16})
    command, _ = calls[0]
    assert command[command.index('--max-turns')+1] == '16'
    assert result.ok is False and result.record['turn_limit_reached'] is True
    assert result.record['num_turns'] == 16
    first_id = result.record['session_id']
    calls = fake_cli(monkeypatch, work)
    session.run_session(work, 'Continue', config=config, resume=True)
    assert calls[0][0][calls[0][0].index('--resume')+1] == first_id


def test_loopback_provider_network_and_extra_body_reach_real_container_arguments(tmp_path,monkeypatch,config):
    work=tmp_path/'episode';calls=fake_cli(monkeypatch,work)
    session.run_session(work,'Connectivity check',config={**config,'docker_network':'host',
        'extra_body':{'reasoning_effort':'low','max_tokens':1024}})
    command,env=calls[0]
    assert command[command.index('--network')+1]=='host'
    body=next(v for v in command if v.startswith('CLAUDE_CODE_EXTRA_BODY='))
    assert json.loads(body.split('=',1)[1])=={'reasoning_effort':'low','max_tokens':1024}
    assert 'private-test-token' not in ' '.join(command)


def test_summary_completion_is_recorded_without_treating_it_as_work(tmp_path,monkeypatch,config):
    work=tmp_path/'episode'
    fake_cli(monkeypatch,work,result={'type':'result','subtype':'success',
        'result':'<analysis>Conversation history.</analysis><summary>Pending task: apply repair.</summary>'})
    result=session.run_session(work,'Continue',config=config)
    assert result.record['summary_only'] is True
    assert result.produced==[]


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


def test_reassigned_model_is_explicit_while_resuming_the_same_transcript(tmp_path, monkeypatch, config):
    work = tmp_path/'episode'
    fake_cli(monkeypatch, work, outputs={'repair.json': 'saved candidate'})
    old = session.run_session(work, 'Repair inputs', config=config, enable_tools=True)
    calls = fake_cli(monkeypatch, work)
    selected = {**config, 'model': 'other-allocated-model', 'base_url': 'http://other-model'}
    new = session.run_session(work, 'Continue saved work', config=selected, resume=True, enable_tools=True)
    command, _ = calls[0]
    assert command[command.index('--resume')+1] == old.record['session_id'] == new.record['session_id']
    assert command[command.index('--model')+1] == selected['model']
    assert f'ANTHROPIC_BASE_URL={selected["base_url"]}' in command
    assert (new.directory/'output-before/repair.json').read_text() == 'saved candidate'
    assert new.record['model'] == selected['model'] and not new.produced


def test_explicit_thinking_budget_reaches_the_agent_container(tmp_path, monkeypatch, config):
    work = tmp_path/'episode'
    calls = fake_cli(monkeypatch, work, outputs={'episode.json': '{}'})
    session.run_session(work, 'Read the source and write the episode',
                        config={**config, 'effort': 'low', 'max_thinking_tokens': 0})
    command, _ = calls[0]
    assert 'MAX_THINKING_TOKENS=0' in command
    assert 'CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1' in command
    assert 'CLAUDE_CODE_EFFORT_LEVEL=low' in command


def test_template_options_use_the_cli_extra_body(tmp_path, monkeypatch, config):
    work = tmp_path/'episode'
    calls = fake_cli(monkeypatch, work, outputs={'episode.json': '{}'})
    session.run_session(work, 'Repair the supplied draft', config={
        **config, 'max_thinking_tokens': 0,
        'chat_template_kwargs': {'enable_thinking': False}})
    command, _ = calls[0]
    extra = next(value.split('=', 1)[1] for value in command
                 if value.startswith('CLAUDE_CODE_EXTRA_BODY='))
    assert json.loads(extra) == {'chat_template_kwargs': {'enable_thinking': False}}


def test_tool_session_can_read_running_source_without_writable_host_mount(tmp_path,monkeypatch,config):
    work=tmp_path/'episode'
    calls=fake_cli(monkeypatch,work)
    session.run_session(work,'Diagnose the tool failure',config=config,enable_tools=True)
    command,_=calls[0]
    mounts=[command[i+1] for i,v in enumerate(command) if v=='-v']
    assert len(mounts)==2
    assert mounts[1].endswith('/src:/opt/novel-code:ro')
    assert not any('/.env' in value for value in mounts)


def test_first_current_task_starts_one_fresh_context_and_keeps_old_work(tmp_path,monkeypatch,config):
    work=tmp_path/'episode'
    fake_cli(monkeypatch,work,outputs={'repair.json':'saved repair'})
    old=session.run_session(work,'Old workflow context',config=config,enable_tools=True)
    (work/'input').mkdir();(work/'input/current-task.json').write_text('{"targets":[]}')
    calls=fake_cli(monkeypatch,work)
    fresh=session.run_session(work,'Current task',resume=True,config=config,enable_tools=True)
    assert '--session-id' in calls[0][0] and '--resume' not in calls[0][0]
    assert fresh.record['session_id']!=old.record['session_id']
    assert json.loads((work/'episode-session-before-current-task.json').read_text())['session_id']==old.record['session_id']
    assert (work/'output/repair.json').read_text()=='saved repair'
    calls=fake_cli(monkeypatch,work)
    continued=session.run_session(work,'Next tool result',resume=True,config=config,enable_tools=True)
    assert '--resume' in calls[0][0] and continued.record['session_id']==fresh.record['session_id']


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
    assert command.count("-v") == 2 and command[command.index("-v") + 1] == f"{work}:/work"
    assert any(value.endswith('/src:/opt/novel-code:ro') for value in command)
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


def test_sensitive_stop_is_recorded_as_refusal_so_fallback_fires(tmp_path, monkeypatch, config):
    work = tmp_path/'episode'
    fake_cli(monkeypatch, work, code=0, result={
        'type': 'result', 'subtype': 'success', 'stop_reason': 'sensitive', 'result': ''})
    result = session.run_session(work, 'Draft', config=config)
    assert result.record['refusal'] == 'content moderation: sensitive stop'
