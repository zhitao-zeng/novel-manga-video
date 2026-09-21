"""A whole book through the sandbox: ask, take what needs no choosing, bind it, check it, and report.

One chapter was four commands typed by a person - propose, look, accept, plan - and that is the right
size for a pilot and the wrong one for three hundred chapters at a quarter of an hour each.  This runs
the same four steps a chapter at a time, several chapters at once, and keeps a report of where every
chapter stands so that the part still needing a person is a list and not a search.

What it will not do is choose.  auto_accept takes a take only when there was nothing to choose
between; a chapter with two takes, or an unreadable sheet, stops at "candidate" with the reason
written down, and a chapter whose binding came out different from its sheet is reported as needing a
look rather than quietly passed along.  Nothing is rendered from here.

Each chapter holds one slot from its first step to its last.  The proposal and the binding both call
the same shared endpoint, so keeping them in one slot is what makes "at most N at once" true.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from novel_manga.application.agents import storyboard as agent_storyboard
from novel_manga.application.agents.sandbox import SandboxRefused, container_running, load_config, parallel_now
from novel_manga.util import atomic_write_json

REPORT = "agent_storyboard_batch"
READY, LOOK, WAITING, FAILED = "ready", "needs_a_look", "needs_a_choice", "failed"
HEADINGS = {READY: "可以渲染", LOOK: "采用了，但要看一眼", WAITING: "等人选稿", FAILED: "没跑成"}


@dataclass
class ChapterResult:
    chapter: int
    outcome: str = ""
    reason: str = ""
    accepted_by: str = ""
    sheet: str = ""
    clips: int = 0
    seconds: dict = field(default_factory=dict)      # how long each step took, for the next estimate


@dataclass
class Steps:
    """The four things that touch the world, so the order they run in can be tested without them."""
    propose: Callable[[int], object]
    bind: Callable[[int], tuple[bool, str]]           # (ok, problem)
    trace: Callable[[int], tuple[bool, str]]          # (clean, what differed)
    endpoint_up: Callable[[], bool]
    busy: Callable[[int], bool] = lambda chapter: False
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = print


def episode_dir(novel_dir: Path, chapter: int) -> Path:
    directory = Path(novel_dir) / f"{Path(novel_dir).name}_{chapter}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def bound_since_accepted(directory: Path) -> bool:
    """Whether the plan beside this chapter was made from the take that is accepted now.

    A plan older than the acceptance belongs to something else - the ordinary planner, or an earlier
    take - and rendering it would film a script nobody chose.
    """
    plan, record = directory / "clip_plan.json", agent_storyboard.state_path(directory)
    return plan.is_file() and record.is_file() and plan.stat().st_mtime >= record.stat().st_mtime


def clip_count(directory: Path) -> int:
    try:
        return len(json.loads((directory / "clip_plan.json").read_text(encoding="utf-8")).get("clips") or [])
    except (OSError, ValueError):
        return 0


def run_chapter(novel_dir: Path, chapter: int, steps: Steps, *, config: dict,
                repropose: bool = False) -> ChapterResult:
    """One chapter from wherever it stands to as far as it can go without a person."""
    directory, result = episode_dir(novel_dir, chapter), ChapterResult(chapter)

    def timed(name, call, *args, **kwargs):
        started = time.monotonic()
        try:
            return call(*args, **kwargs)
        finally:
            result.seconds[name] = round(time.monotonic() - started)

    current = agent_storyboard.state(directory)
    if current.status == "none" or (repropose and current.status == "candidate"):
        if steps.busy(chapter):
            result.outcome, result.reason = FAILED, "这一章上一次的沙箱容器还在跑，先等它结束"
            return result
        try:
            timed("propose", steps.propose, chapter)
        except SandboxRefused as refusal:
            result.outcome, result.reason = FAILED, f"沙箱没起来：{refusal}"
            return result
    accepted, reason = timed("accept", agent_storyboard.auto_accept, directory, config=config)
    if reason:
        result.outcome, result.reason = WAITING, reason
        return result
    result.accepted_by, result.sheet = accepted.accepted_by, accepted.sheet
    if not bound_since_accepted(directory):
        # Before binding, not inside it: binding refuses a scene the bible does not have, and says to
        # put the place in first.  That was a step nobody owned.
        added, problem = timed("place", agent_storyboard.place_new_locations, novel_dir, directory, config=config)
        if problem:
            result.outcome, result.reason = LOOK, problem
            return result
        if added:
            steps.log(f"[{time.strftime('%T')}] ch{chapter}: 分镜里的新地点已补进圣经：{'、'.join(added)}")
        ok, problem = timed("bind", steps.bind, chapter)
        if not ok:
            result.outcome, result.reason = LOOK, f"分镜采用了，但绑定没过：{problem}"
            return result
    result.clips = clip_count(directory)
    clean, difference = timed("trace", steps.trace, chapter)
    result.outcome = READY if clean else LOOK
    result.reason = "" if clean else f"绑定出来的剧本和原表对不上：{difference}"
    return result


def wait_for_endpoint(steps: Steps, *, every: float = 60.0, remind: int = 10) -> None:
    """Hold here while the endpoint is down, instead of failing the rest of the book in a minute.

    Planning did exactly that once: the endpoint was still loading, every chapter failed in 0.004
    seconds, and two hundred chapters would have been marked failed before anyone looked.  A proposal
    that cannot reach its model says nothing about the chapter.
    """
    waited = 0
    while not steps.endpoint_up():
        if waited % remind == 0:
            steps.log(f"[{time.strftime('%T')}] 模型端点不通，等它回来（每 {int(every)} 秒探一次，不会把章节记成失败）")
        waited += 1
        steps.sleep(every)
    if waited:
        steps.log(f"[{time.strftime('%T')}] 端点回来了，继续")


def run_batch(novel_dir: Path, chapters: list[int], steps: Steps, *, config: dict | None = None,
              repropose: bool = False, allowance: Callable[[dict], int] = parallel_now) -> list[ChapterResult]:
    """Every chapter, as many at once as the endpoint allows at this hour, reported as each one lands."""
    config = config or load_config()
    novel_dir = Path(novel_dir)
    ceiling = max(int(v) for k, v in (config.get("parallel") or {"day": 1}).items() if k in {"day", "night"})
    results: dict[int, ChapterResult] = {}
    writing = threading.Lock()
    pending, running = list(chapters), {}

    def landed(future: Future) -> None:
        chapter = running.pop(future)
        try:
            outcome = future.result()
        except Exception as error:                     # noqa: BLE001 - one chapter must not end the book
            outcome = ChapterResult(chapter, FAILED, f"{type(error).__name__}: {str(error)[:200]}")
        results[chapter] = outcome
        steps.log(f"[{time.strftime('%T')}] ch{chapter}: {HEADINGS[outcome.outcome]}"
                  + (f"｜{outcome.reason}" if outcome.reason else f"｜{outcome.clips} 段")
                  + f"｜{sum(outcome.seconds.values())}s")
        with writing:
            write_report(novel_dir, list(results.values()), total=len(chapters))

    with ThreadPoolExecutor(max_workers=ceiling) as pool:
        while pending or running:
            # Asked every time round, not once: the allowance changes at ten in the morning and again
            # at eight at night, and a run this long crosses both.  When it drops, nothing is stopped -
            # the chapters already in their slots finish, and no new one starts until there is room.
            while pending and len(running) < min(ceiling, max(1, allowance(config))):
                wait_for_endpoint(steps)
                chapter = pending.pop(0)
                running[pool.submit(run_chapter, novel_dir, chapter, steps, config=config,
                                    repropose=repropose)] = chapter
            finished, _ = wait(list(running), timeout=30, return_when=FIRST_COMPLETED)
            for future in finished:
                landed(future)
    return [results[chapter] for chapter in chapters if chapter in results]


def write_report(novel_dir: Path, results: list[ChapterResult], *, total: int) -> Path:
    """Where every chapter stands, with the ones that need a person at the top."""
    results = sorted(results, key=lambda r: r.chapter)
    atomic_write_json(novel_dir / f"{REPORT}.json",
                      {"written": time.strftime("%F %T"), "total": total, "done": len(results),
                       "chapters": [asdict(r) for r in results]})
    lines = [f"# 沙箱分镜批量：{len(results)}/{total} 章", "", f"写于 {time.strftime('%F %T')}", ""]
    for outcome in (WAITING, LOOK, FAILED, READY):
        rows = [r for r in results if r.outcome == outcome]
        if not rows:
            continue
        lines += [f"## {HEADINGS[outcome]}（{len(rows)}）", ""]
        if outcome == READY:
            lines += ["、".join(f"{r.chapter}" for r in rows), ""]
            continue
        lines += [f"- 第 {r.chapter} 章：{r.reason}" for r in rows] + [""]
    spent = [sum(r.seconds.values()) for r in results if r.seconds.get("propose")]
    if spent:
        lines += [f"写过分镜的章节平均 {sum(spent) // len(spent)} 秒一章。", ""]
    path = novel_dir / f"{REPORT}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def default_busy(novel_id: str, skill: str) -> Callable[[int], bool]:
    return lambda chapter: container_running(f"agent-{agent_storyboard.run_name(novel_id, chapter, skill)}")
