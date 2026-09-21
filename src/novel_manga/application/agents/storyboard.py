"""The sandbox as an optional planning backend: propose a storyboard, choose one, then bind it.

The pieces already existed - planning/authored_brief writes the task pack, agents/sandbox runs the
container, planning/storyboard reads the sheet and planning/binding fills in the technical fields -
and what was missing between them was the part that says which chapter is at which stage.  Without
it the hand-off was a person running docker, finding the output file and passing its path to
`--bind-storyboard`, which is not a pipeline and cannot be resumed.

A chapter is in exactly one of three states, written down in agent_storyboard.json beside the
chapter:

    none       - nothing has been asked for yet
    candidate  - an attempt produced one or more sheets, and nobody has chosen
    accepted   - one sheet is the script for this chapter, and planning binds it

The choice stays a person's for now; only the errands around it are automated.  What matters is that
the choice is recorded rather than implied by which file someone happened to copy.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from novel_manga.application.agents.sandbox import Attempt, SandboxRefused, load_config, run_agent

STATE_FILE = "agent_storyboard.json"
SHEET_SUFFIXES = (".xlsx",)


@dataclass
class StoryboardState:
    status: str = "none"          # none | candidate | accepted
    run: str = ""
    attempt: str = ""
    skill: str = ""
    sheets: list = None           # candidate sheets, as paths relative to the run directory
    sheet: str = ""               # the accepted one
    sheet_name: str = ""          # which worksheet inside it
    accepted_at: str = ""

    def __post_init__(self):
        self.sheets = list(self.sheets or [])


def state_path(episode_dir: Path) -> Path:
    return Path(episode_dir) / STATE_FILE


def state(episode_dir: Path) -> StoryboardState:
    path = state_path(episode_dir)
    if not path.is_file():
        return StoryboardState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StoryboardState()
    known = {f for f in StoryboardState.__dataclass_fields__}
    return StoryboardState(**{k: v for k, v in data.items() if k in known})


def write_state(episode_dir: Path, value: StoryboardState) -> StoryboardState:
    state_path(episode_dir).write_text(
        json.dumps(value.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
    return value


def run_name(novel_id: str, chapter: int, skill: str) -> str:
    return f"{novel_id}-ch{chapter:04d}-{skill}"


def propose(novel_dir: Path, episode_dir: Path, chapter: int, skill: str, *,
            config: dict | None = None, timeout: int = 0, log=print) -> StoryboardState:
    """Write this chapter's task pack, run the skill in the sandbox, and record what came back.

    Returns a candidate state even when nothing usable was produced: the attempt happened and is part
    of the record.  `exit=0` is not the same as "there is a storyboard", so the sheets found are
    reported separately and an empty list is a visible outcome rather than a silent one.
    """
    from novel_manga.planning.authored_brief import write_brief
    config = config or load_config()
    novel_dir, episode_dir = Path(novel_dir), Path(episode_dir)
    run = run_name(novel_dir.name, chapter, skill)
    run_dir = Path(config["runs_root"]) / run
    # the pack goes in input/ and the prompt that drives the run beside it; write_brief does both, and
    # writing the prompt again here was a second copy of the same text that could only ever disagree
    write_brief(novel_dir, chapter, run_dir / "input", skill)

    attempt: Attempt = run_agent(run, skills=skill, config=config, timeout=timeout, log=log)
    sheets = sorted(name for name in attempt.produced if name.lower().endswith(SHEET_SUFFIXES))
    log(f"{run}: 本次尝试产出 {len(sheets)} 份分镜表" + (f"：{'、'.join(sheets)}" if sheets else "（一份都没有）"))
    return write_state(episode_dir, StoryboardState(
        status="candidate", run=run, attempt=attempt.directory.name, skill=skill,
        sheets=[f"output/{name}" for name in sheets]))


def accept(episode_dir: Path, sheet: str, *, sheet_name: str = "",
           config: dict | None = None) -> StoryboardState:
    """Record that this sheet is the chapter's script.  Planning binds it from here on."""
    config = config or load_config()
    current = state(episode_dir)
    if current.status == "none":
        raise SandboxRefused("这一章还没有候选分镜，先跑一次沙箱再选稿")
    run_dir = Path(config["runs_root"]) / current.run
    path = Path(sheet)
    if not path.is_absolute():
        path = run_dir / sheet
    if not path.is_file():
        raise SandboxRefused(f"选的这份分镜不在：{path}")
    current.status, current.sheet_name = "accepted", sheet_name
    current.sheet = str(path.relative_to(run_dir)) if path.is_relative_to(run_dir) else str(path)
    current.accepted_at = time.strftime("%F %T")
    return write_state(episode_dir, current)


def accepted_sheet(episode_dir: Path, *, config: dict | None = None) -> tuple[Path, str] | None:
    """The sheet planning should bind for this chapter, or None while nobody has chosen one."""
    current = state(episode_dir)
    if current.status != "accepted" or not current.sheet:
        return None
    config = config or load_config()
    path = Path(current.sheet)
    if not path.is_absolute():
        path = Path(config["runs_root"]) / current.run / current.sheet
    return path, current.sheet_name
