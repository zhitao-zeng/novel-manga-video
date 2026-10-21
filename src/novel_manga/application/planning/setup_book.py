"""Take a new novel from its text to a bible the planner can work from, in one run.

Every piece of this existed and was invoked by hand, and every hand-off was a decision made on the
spot - which is how a hundred-chapter reading of 87 people got handed to a single build call sized
for 12, how NOVEL_LLM_MAX_TOKENS got raised against a min() that ignores it, and how an agent ran on
a brief written before the rules it was supposed to follow.  None of those were model failures.  They
were choices that nothing had written down.

So the hand-offs live here now:

  1. 建工作区    build_bible_thin, no cast - the novel index and a seed bible have to exist before
                 anything can read the book by chapter.
  2. 逐章读      lean_bible_thin, parallel over the local Qwen instances, cached per chapter.
  3. 归并        the sandbox agent on Flash-Next, writing files: a book's cast does not fit one
                 reply, and the local 27B fails this (three hours, or 391k tokens against a 262k
                 window).  The brief is generated now, so it cannot be stale.
  4. 重建圣经    the reading names the cast; the build designs the ones the seed chapters show and
                 records the rest in reading_cast.json, which is what growth may add from later.

    python scripts/setup_book_thin.py inputs/X.md --novel-id x --title X --style meiman --chapters 1-100

Endpoints are checked before any of it runs: the failures they cause otherwise land after minutes of
work, on a stage that had nothing to do with them.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from novel_manga.application.configuration import project_root

ROOT = project_root()
SCRIPTS = Path(__file__).resolve().parents[4]/'scripts'

SANDBOX = ROOT / "configs" / "agent_sandbox.json"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(command: list[str], env: dict | None = None, name: str = "") -> int:
    log(f"→ {name or command[1]}")
    code = subprocess.call(command, env={**os.environ, **(env or {})}, cwd=ROOT)
    if code:
        log(f"✗ {name or command[1]} 退出码 {code}")
    return code


def alive(url: str, key: str = "") -> bool:
    request = urllib.request.Request(url.rstrip("/") + "/models")
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def env_value(name: str) -> str:
    if name in os.environ:
        return os.environ[name].strip()
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def container_running(name: str) -> bool:
    """Whether a container by this name is up right now, so a rerun cannot delete a live run's files."""
    try:
        result = subprocess.run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return name in result.stdout.split()


def preflight(config: dict, *, reading: bool) -> tuple[dict, list[str]]:
    """Every endpoint THIS run needs, checked before the first minute is spent on any of them.

    Only the ones it needs: --skip-read consumes a merge that already happened, and holding it back
    because the per-chapter extraction endpoints are down blocks a recovery on a service it will not
    call.  A check that fails for something the run never touches is noise standing in a doorway.
    """
    if os.environ.get('CODEX_LUNA_ADAPTER') == '1':
        from luna_transport import check_available
        return {}, check_available()
    from novel_manga.llm.config import endpoint_settings, planner_endpoint_name
    settings = endpoint_settings(planner_endpoint_name())
    problems: list[str] = []
    live = [url for url in settings.endpoints if alive(url, settings.key)]
    if not live:
        problems.append(f'当前规划/读书端点不可用：{settings.model}')
    key = env_value(config['key_var'])
    if not key:
        problems.append(f"Agent 密钥未设置：{config['key_var']}")
    overrides = {'QWEN38_LOCAL_BASE_URL': ','.join(live), 'QWEN38_LOCAL_MODEL': settings.model,
        'QWEN38_LOCAL_API_KEY': settings.key, 'QWEN38_LOCAL_API_KEY_VAR': 'QWEN38_LOCAL_API_KEY',
        'QWEN38_LOCAL_STREAM': '1' if settings.stream else '0',
        'QWEN38_LOCAL_MIN_MAX_TOKENS': str(settings.min_max_tokens),
        'NOVEL_LLM_BASE_URL': live[0] if live else '', 'NOVEL_LLM_MODEL': settings.model,
        'NOVEL_LLM_API_KEY': settings.key}
    return overrides, problems


def register_source(novel_dir, source, novel_id, title):
    """An index needs no Bible or paid model call; preserve registered numbering."""
    from novel_manga.source.ingest import read_novel
    from novel_manga.util import atomic_write_json, read_json
    novel_dir, source = Path(novel_dir), Path(source).resolve()
    saved = read_json(novel_dir/'novel.json', {})
    if saved:
        if Path(saved['source']).resolve() != source:
            raise ValueError('Existing book uses a different source; retain its registration')
        return saved
    novel = read_novel(source, novel_id=novel_id, title=title)
    saved = {'novel_id': novel_id, 'title': title, 'source': str(source),
        'chapters': [{'index': ep.index, 'title': ep.source_title, 'text_count': ep.text_count,
                      'start': ep.source_start, 'end': ep.source_end} for ep in novel.episodes]}
    atomic_write_json(novel_dir/'novel.json', saved)
    return saved


def handoff_reading(novel_dir, chapters):
    """Use saved lean summaries as initial recaps; never replace richer later work."""
    from novel_manga.application.planning.reading import write_recap_row, recap_rows
    from novel_manga.util import read_json
    novel_dir = Path(novel_dir)
    have = recap_rows(novel_dir)
    titles = {r['index']: r['title'] for r in read_json(novel_dir/'novel.json', {}).get('chapters', [])}
    added = []
    for chapter in chapters:
        if chapter in have:
            continue
        extract = read_json(novel_dir/f'lean/extract/ch-{chapter:03d}.json', {})
        summary = str((extract.get('answer') or {}).get('summary') or '').strip()
        if summary:
            write_recap_row(novel_dir, {'chapter': chapter, 'title': titles.get(chapter, ''),
                                       'summary': summary, 'hook': ''})
            added.append(chapter)
    return added


def reading_ready(novel_dir, chapters):
    from novel_manga.util import read_json
    novel_dir = Path(novel_dir)
    roster = read_json(novel_dir/'reading_cast.json', {})
    return (isinstance(roster.get('characters'), list) and set(chapters) <= set(roster.get('chapters') or [])
            and all((novel_dir/f'lean/extract/ch-{n:03d}.json').is_file() for n in chapters))


def read_and_merge(novel_dir, chapters, *, settings, workers):
    from novel_manga.llm import usage
    from novel_manga.story import lean_reading as lean
    from novel_manga.util import atomic_write_json, read_json
    from .lean_bible import chapter_texts, read_chapters
    from .reading_merge import merge_reading
    metadata = read_json(novel_dir/'novel.json')
    texts = chapter_texts(metadata, chapters)
    with usage.scope(novel=novel_dir.name, stage='reading'):
        extracts = read_chapters(metadata['title'], texts, novel_dir/'lean/extract',
                                list(settings.endpoints), workers=workers, settings=settings)
    if set(extracts) != set(chapters):
        raise ValueError('Initial reading incomplete; successful chapter caches retained')
    candidates = lean.aggregate(extracts, texts)
    atomic_write_json(novel_dir/'lean/candidates.json', candidates)
    merge_reading(novel_dir, candidates, texts, settings=settings, workers=workers)
    return novel_dir/'lean/export.json'


def adopt_reading(novel_dir, export_path, chapters):
    """Update the source-backed roster without rebuilding existing visual designs."""
    from novel_manga.util import atomic_write_json, read_json
    from novel_manga.story.reading_facts import FACTS_FIELD, merge_character_facts
    from .initialize import read_cast, read_cast_facts, reading_verdicts
    cast, aliases = read_cast(export_path)
    old = read_json(novel_dir/'reading_cast.json', {})
    all_cast = list(dict.fromkeys([*(old.get('characters') or []), *cast]))
    aliases = {**aliases, **read_json(novel_dir/'bible_aliases.json', {}), **(old.get('aliases') or {})}
    row = {**old, 'characters': all_cast, 'aliases': aliases,
           **reading_verdicts(novel_dir, all_cast, aliases)}
    row['chapters'] = sorted(set(old.get('chapters') or []) | set(chapters))
    row[FACTS_FIELD] = merge_character_facts(old.get(FACTS_FIELD) or {}, read_cast_facts(export_path))
    atomic_write_json(novel_dir/'reading_cast.json', row)
    atomic_write_json(novel_dir/'bible_aliases.json', aliases)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="小说原文（一个 .md/.txt，章节标题各占一行）")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--style", required=True)
    parser.add_argument("--frame", default="16:9")
    parser.add_argument("--chapters", default="1-100", help="读哪些章，例如 1-100")
    parser.add_argument("--bible-chapters", type=int, default=5, help="种子圣经从前几章设计")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--agent-config", type=Path, default=SANDBOX,
                        help="本次归并的沙箱配置，不修改共享默认配置")
    parser.add_argument("--read-workers", type=int, default=0,
                        help="逐章提取并发；0 沿用每端点的默认并发")
    parser.add_argument("--force", action="store_true", help="工作区已存在时重建")
    parser.add_argument("--skip-read", action="store_true", help="已经读过，直接用现成的归并结果")
    parser.add_argument('--stage', choices=('setup', 'plan'), default='setup')
    parser.add_argument('--plan-parallel', type=int, default=8)
    args = parser.parse_args(argv)

    from novel_manga.util import load_dotenv, read_json
    from novel_manga.llm.config import endpoint_settings, planner_endpoint_name
    from .lean_bible import chapters_of
    load_dotenv(ROOT/'.env')
    novel_dir = ROOT/args.output_root/args.novel_id
    register_source(novel_dir, args.source, args.novel_id, args.title)
    chapters = chapters_of(args.chapters)
    reusable = reading_ready(novel_dir, chapters)
    has_bible = (novel_dir/'story_bible.json').is_file()

    config = json.loads(args.agent_config.read_text(encoding="utf-8"))
    overrides, problems = preflight(config, reading=not reusable and not args.skip_read) if (
        not reusable or not has_bible or args.stage == 'plan') else ({}, [])
    for problem in problems:
        log("✗ " + problem)
    if problems:
        log("开跑前的检查没过，先修这些再来——跑到一半才发现，前面的时间就白花了")
        return 2

    run_name = f"{args.novel_id}-merge"
    export = novel_dir/'lean/export.json'
    python = str(ROOT / ".venv" / "bin" / "python")

    if not reusable:
        if args.skip_read:
            if not export.is_file():
                export = Path(config['runs_root'])/run_name/'output/export/story_bible.json'
            if not export.is_file():
                log('已有归并未找到；保留工作区，不重新读书')
                return 2
        else:
            export = read_and_merge(novel_dir, chapters, settings=endpoint_settings(planner_endpoint_name()),
                                    workers=args.read_workers or 8)

    # 4. 圣经：演员表由读书定，种子只设计前几章出场的人，其余进 reading_cast.json 交给 growth
    if not has_bible or args.force:
        code = run([python, str(SCRIPTS/'build_bible_thin.py'), args.source,
                "--novel-id", args.novel_id, "--title", args.title, "--style", args.style,
                "--frame", args.frame, "--output-root", args.output_root,
                *(['--force'] if args.force else []),
                "--bible-chapters", str(args.bible_chapters), "--cast", str(export)],
               overrides, "按读书的演员表建圣经")
        if code:
            return code
    if not reusable:
        adopt_reading(novel_dir, export, chapters)
    added_recaps = handoff_reading(novel_dir, chapters)
    log(f'初读摘要交接：补齐 {len(added_recaps)} 章前情；已有摘要保留')

    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    roster = read_json(novel_dir/'reading_cast.json', {})
    aliases = read_json(novel_dir/'bible_aliases.json', {})
    log(f"完成：圣经 {len(bible['characters'])} 人 {len(bible['locations'])} 地点｜"
        f"读书名单 {len(roster['characters'])} 人｜别名 {len(aliases)} 条")

    # 5. Reuse source-backed adoption; a roster label alone cannot establish a person.
    from novel_manga.application.identity.ledger_flow import adopt_roster
    adoption = adopt_roster(novel_dir, roster, aliases) if not reusable else {'seeded': [], 'unresolved': []}
    seeded = adoption['seeded']
    unresolved = adoption['unresolved']
    log(f"台账对账：在册 {len(roster['characters'])} 项，按原文补录 {len(seeded)} 项，待核对 {len(unresolved)} 项"
        + (f"：{'、'.join(unresolved[:8])}{'…' if len(unresolved) > 8 else ''}" if unresolved else ""))
    log(f"下一步：{python} scripts/thin_batch.py --novel-dir {novel_dir} --chapters {args.chapters} --stage plan")
    if args.stage == 'plan':
        return run([python, str(SCRIPTS/'thin_batch.py'), '--novel-dir', str(novel_dir), '--chapters', args.chapters,
            '--stage', 'plan', '--plan-parallel', str(args.plan_parallel), '--no-grow-bible', '--no-eager-cards',
            '--repair-planning', '--agent-config', str(args.agent_config)],
            {**overrides, 'NOVEL_CLIP_SECONDS_MAX': '15'}, '逐章规划与既有Agent修复')
    return 0
