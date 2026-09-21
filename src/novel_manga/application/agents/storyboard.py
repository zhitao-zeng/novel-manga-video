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

The chosen take is copied beside the chapter and recorded with its digest, so it is a version rather
than a path: output/ is shared by every attempt of a run, and a rerun used to rewrite the same file
under the accepted record's nose.  A later proposal adds candidates and never replaces the choice.

The choice was a person's alone until a whole book went this way: three hundred chapters cannot each
wait for someone to type a path.  auto_accept takes the take only when there is nothing to choose
between - one sheet, one worksheet, every cell readable - and leaves everything else exactly where a
person would find it.  Either way the record says who chose and by what rule, because what matters is
that the choice is recorded rather than implied by which file someone happened to copy.
"""
from __future__ import annotations

import hashlib
import json
import shutil
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
    sheet: str = ""               # the accepted one, as a snapshot beside the chapter
    sheet_name: str = ""          # which worksheet inside it
    sheet_digest: str = ""        # what that snapshot contained when it was accepted
    accepted_from: str = ""       # the attempt and path it was taken from
    accepted_at: str = ""
    accepted_by: str = ""         # "person", or "auto:<rule>" when nothing was there to choose between

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


def digest_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


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
    # A new proposal is another candidate, never an overwrite of a take already accepted: the
    # snapshot beside the chapter is untouched here, so planning keeps binding what was chosen until
    # somebody chooses again.
    current = state(episode_dir)
    return write_state(episode_dir, StoryboardState(
        # status is about the CHOICE, not about whether anything new exists.  A chapter that already
        # has an accepted take keeps binding it until somebody chooses again; the new candidates are
        # recorded beside it, waiting.
        status="accepted" if current.status == "accepted" else "candidate",
        run=run, attempt=attempt.directory.name, skill=skill,
        sheets=[f"output/{name}" for name in sheets],
        sheet=current.sheet, sheet_name=current.sheet_name, sheet_digest=current.sheet_digest,
        accepted_from=current.accepted_from, accepted_at=current.accepted_at,
        accepted_by=current.accepted_by))


def accept(episode_dir: Path, sheet: str, *, sheet_name: str = "",
           config: dict | None = None, by: str = "person") -> StoryboardState:
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
    # A copy beside the chapter, not a path into the run directory.  output/ is shared by every
    # attempt of a run, so a rerun rewrites the same file: the accepted record kept pointing at it and
    # planning silently bound whatever the newest attempt had written.  Accepting a take has to mean
    # accepting that take.
    snapshot = Path(episode_dir) / "agent_storyboard" / path.name
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, snapshot)
    current.status, current.sheet_name = "accepted", sheet_name
    current.sheet = str(snapshot.relative_to(episode_dir))
    current.sheet_digest = digest_of(snapshot)
    current.accepted_from = f"{current.attempt}:{sheet}"
    current.accepted_at = time.strftime("%F %T")
    current.accepted_by = by
    return write_state(episode_dir, current)


AUTO_RULE = "one-readable-sheet-v1"


def auto_accept(episode_dir: Path, *, config: dict | None = None) -> tuple[StoryboardState, str]:
    """Take the take when there is nothing to choose between; otherwise say what a person has to decide.

    Returns (state, reason).  The reason is empty when the chapter is accepted - now, or already - and
    otherwise names the one thing that stopped it, in words that tell whoever reads the report what to
    open.  Three conditions, each of which is a place where accepting would be making a choice or
    hiding a loss:

      one sheet        several takes are a choice, and none is nothing to accept
      one worksheet    a workbook with two complete storyboards is the same choice one level down
      every cell reads a 台词 / 声音 line that does not parse is a line of dialogue that will not be
                       filmed, and nobody is told - which is exactly what a person should see first

    Nothing here judges whether the storyboard is good.  That stays with whoever watches the episode.
    """
    from zipfile import BadZipFile

    from novel_manga.planning.storyboard import authored_sound, read_workbook
    config = config or load_config()
    current = state(episode_dir)
    if current.status == "accepted":
        return current, ""
    if current.status == "none":
        return current, "还没有候选分镜"
    if not current.sheets:
        return current, "这次尝试没有产出分镜表"
    if len(current.sheets) > 1:
        return current, f"产出了 {len(current.sheets)} 份分镜表，要人选一版：{'、'.join(current.sheets)}"
    path = Path(config["runs_root"]) / current.run / current.sheets[0]
    try:
        sheets = read_workbook(path)
    except (ValueError, KeyError, OSError, BadZipFile) as error:
        return current, f"分镜表读不了：{str(error)[:200]}"
    if len(sheets) > 1:
        return current, f"工作簿里有 {len(sheets)} 个完整的分镜工作表，要人选：{'、'.join(s.name for s in sheets)}"
    unread = [(row["authored_id"], line, why) for row in sheets[0].rows
              for line, why in authored_sound(row["authored_sound"]).problems]
    if unread:
        shot, line, why = unread[0]
        return current, (f"台词/声音列有 {len(unread)} 行读不懂，这些台词会丢："
                         f"镜 {shot}「{line[:40]}」（{why}）")
    return accept(episode_dir, current.sheets[0], sheet_name=sheets[0].name, config=config,
                  by=f"auto:{AUTO_RULE}"), ""


def accepted_sheet(episode_dir: Path, *, config: dict | None = None) -> tuple[Path, str] | None:
    """The sheet planning should bind for this chapter, or None while nobody has chosen one.

    The snapshot is checked against the digest recorded when it was accepted, so a chapter binds the
    take that was chosen or nothing at all - never a different one under the same name.
    """
    current = state(episode_dir)
    if current.status != "accepted" or not current.sheet:
        return None
    path = Path(episode_dir) / current.sheet
    if not path.is_file():
        raise SandboxRefused(f"这一章接受过的分镜不在了：{path}。重新选一版")
    if current.sheet_digest and digest_of(path) != current.sheet_digest:
        raise SandboxRefused(f"这一章接受过的分镜内容变了：{path}。要用新的就重新选一版，"
                             "不要在原地改")
    return path, current.sheet_name
