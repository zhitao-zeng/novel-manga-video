#!/usr/bin/env python
"""Set up a new novel for the thin pipeline: story bible + profile + grammar.

One LLM call (the repository's own ``OpenAICompatiblePlanner.build_bible``,
configured by ``NOVEL_LLM_*`` in ``.env``) extracts the reusable characters
and locations from the novel.  This script then writes everything the three
thin scripts read from ``outputs/<novel-id>/``:

    story_bible.json     characters, locations, visual_style (from the profile style)
    profile.json         {"style": "2d"|"3d", "frame": "9:16"|"16:9"}
    visual_grammar.json  copied from configs/templates, location_time keys pre-filled
    story_bible.md       the bible as a table, for the human review before any card is paid for
    novel.json           source path, sha256 and the chapter split (check it is the split you expect)

Example:
    .venv/bin/python scripts/build_bible_thin.py inputs/斗破苍穹-前10章.md --novel-id doupo-2d --title 斗破苍穹 --style 2d --frame 9:16
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import DEFAULTS, STYLE_NAME, STYLE_VISUAL  # noqa: E402

from novel_manga.config import Settings  # noqa: E402
from novel_manga.ingest import read_novel  # noqa: E402
from novel_manga.models import StoryBible  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "configs" / "templates"


def load_dotenv(path: Path) -> None:
    """Same convenience as thin_batch.py: values already in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


def fingerprint(title: str, style: str, characters) -> str:
    try:
        from novel_manga.planner import _fingerprint  # same digest the v5 planner uses
        return _fingerprint(title, style, characters)
    except Exception:  # noqa: BLE001 - private helper; fall back to a plain digest
        return hashlib.sha256(f"{title}|{style}|{[c.name for c in characters]}".encode("utf-8")).hexdigest()[:16]


def build_bible(novel, style: str) -> StoryBible:
    os.environ["NOVEL_PLANNER_BACKEND"] = "openai-compatible"
    settings = Settings.from_env(provider="phanrouter", output_root="outputs", admission_mode="preview")
    if not (settings.llm_base_url and settings.llm_api_key):
        raise SystemExit("NOVEL_LLM_BASE_URL / NOVEL_LLM_API_KEY are not set; run `set -a; source .env; set +a` first")
    from novel_manga.planner import OpenAICompatiblePlanner
    print(json.dumps({"bible_model": settings.llm_model, "base_url": settings.llm_base_url}, ensure_ascii=False), flush=True)
    bible = OpenAICompatiblePlanner(settings).build_bible(novel)
    style_text = STYLE_VISUAL[style]
    return bible.model_copy(update={"visual_style": style_text, "style_fingerprint": fingerprint(novel.title, style_text, bible.characters)})


def bible_markdown(bible: StoryBible, novel, source: Path, profile: dict) -> str:
    lines = [
        f"# {bible.novel_title} · 故事圣经（{STYLE_NAME[profile['style']]} / {profile['frame']}）",
        "",
        f"来源：`{source}`，{len(novel.episodes)} 章，{sum(e.text_count for e in novel.episodes)} 字。类型：{bible.genre}",
        "",
        "先检查再建卡：名字是否正确、主要角色是否齐、外貌和服装是否有原文依据、地点是否都是可以画成空场的地方。改完保存 story_bible.json 再往下走。",
        "",
        "## 角色",
        "",
        "| # | 姓名 | 角色 | 性别/年龄 | 外貌 | 服装 | 识别物 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, c in enumerate(bible.characters, start=1):
        lines.append(f"| character_{index:03d} | {c.name} | {c.role} | {c.gender}/{c.age} | {c.appearance} | {c.wardrobe} | {getattr(c, 'signature_prop', '') or ''} |")
    lines += ["", "## 地点", ""]
    for index, location in enumerate(bible.locations, start=1):
        lines.append(f"- location_{index:03d} · {location}")
    lines += ["", "## 画风", "", bible.visual_style, "", "## 章节切分", ""]
    for episode in novel.episodes:
        lines.append(f"- 第 {episode.index} 集 ← {episode.source_title}（{episode.text_count} 字）")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="novel text (.md/.txt/.docx/.pdf) with 第X章 headings")
    parser.add_argument("--novel-id", required=True, help="output directory name under --output-root, e.g. doupo-2d")
    parser.add_argument("--title", required=True, help="the novel's title as it should appear on covers")
    parser.add_argument("--style", choices=tuple(STYLE_VISUAL), default=DEFAULTS["style"])
    parser.add_argument("--frame", choices=("9:16", "16:9"), default=DEFAULTS["frame"])
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--force", action="store_true", help="rebuild the bible even if story_bible.json exists")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    source = Path(args.source).resolve()
    novel = read_novel(source, novel_id=args.novel_id, title=args.title)
    novel_dir = Path(args.output_root).resolve() / args.novel_id
    novel_dir.mkdir(parents=True, exist_ok=True)
    profile = {"style": args.style, "frame": args.frame}
    print(json.dumps({"chapters": [{"index": e.index, "title": e.source_title, "chars": e.text_count} for e in novel.episodes]}, ensure_ascii=False, indent=1), flush=True)

    bible_path = novel_dir / "story_bible.json"
    if bible_path.is_file() and not args.force:
        bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
        print(json.dumps({"bible": "kept existing", "characters": len(bible.characters), "locations": len(bible.locations)}, ensure_ascii=False), flush=True)
    else:
        started = time.monotonic()
        bible = build_bible(novel, args.style)
        atomic_write_json(bible_path, bible.model_dump(mode="json"))
        print(json.dumps({"bible": "built", "seconds": round(time.monotonic() - started, 1), "characters": [c.name for c in bible.characters], "locations": [l.split("：", 1)[0] for l in bible.locations]}, ensure_ascii=False), flush=True)

    profile_path = novel_dir / "profile.json"
    if not profile_path.is_file() or args.force:
        atomic_write_json(profile_path, profile)
    grammar_path = novel_dir / "visual_grammar.json"
    if not grammar_path.is_file():
        grammar = json.loads((TEMPLATES / "visual_grammar.json").read_text(encoding="utf-8"))
        grammar["location_time"] = {location.split("：", 1)[0].strip(): "" for location in bible.locations}
        atomic_write_json(grammar_path, grammar)
    overrides_example = ROOT / "configs" / "fentian" / "clip_overrides.example.json"
    if overrides_example.is_file() and not (novel_dir / "clip_overrides.example.json").is_file():
        shutil.copy2(overrides_example, novel_dir / "clip_overrides.example.json")
    atomic_write_json(novel_dir / "novel.json", {
        "novel_id": args.novel_id, "title": args.title, "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "chapters": [{"index": e.index, "title": e.source_title, "chars": e.text_count} for e in novel.episodes],
        "profile": profile, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    (novel_dir / "story_bible.md").write_text(bible_markdown(bible, novel, source, profile), encoding="utf-8")
    print(json.dumps({
        "novel_dir": str(novel_dir), "review": str(novel_dir / "story_bible.md"),
        "next": [
            f"改 {grammar_path.name} 的 location_time（每个地点的时间和主光源）；style_line 留空则用 profile 的画风",
            f".venv/bin/python scripts/thin_batch.py --novel-dir {novel_dir} --chapters 1 --stage plan   # 第一章剧本，看 chapter_script.md",
            f".venv/bin/python scripts/thin_batch.py --novel-dir {novel_dir} --chapters 1 --stage assets # 建卡，看 series_assets/cards_sheet.jpg",
            f".venv/bin/python scripts/thin_batch.py --novel-dir {novel_dir} --chapters 1               # 第一集成片，满意后再跑 --chapters 2-{len(novel.episodes)}",
        ],
    }, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
