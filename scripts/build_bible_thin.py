#!/usr/bin/env python
"""Set up a new novel for the thin pipeline: story bible + profile + grammar.

One LLM call (the repository's own ``BibleBuilder.build_bible``,
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
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import DEFAULTS, STYLE_NAME, STYLE_VISUAL, detect_genre, load_genre  # noqa: E402

from novel_manga.config import Settings  # noqa: E402
from novel_manga.ingest import read_novel  # noqa: E402
from novel_manga.bible import BibleBuilder, _fingerprint as fingerprint
from novel_manga.models.bible import StoryBible
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


def build_bible(novel, style: str) -> StoryBible:
    os.environ["NOVEL_PLANNER_BACKEND"] = "openai-compatible"
    settings = Settings.from_env(provider="phanrouter", output_root="outputs", admission_mode="preview")
    if not (settings.llm_base_url and settings.llm_api_key):
        raise SystemExit("NOVEL_LLM_BASE_URL / NOVEL_LLM_API_KEY are not set; run `set -a; source .env; set +a` first")
    print(json.dumps({"bible_model": settings.llm_model, "base_url": settings.llm_base_url}, ensure_ascii=False), flush=True)
    bible = BibleBuilder(settings).build_bible(novel)
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
    parser.add_argument("--fill", action="store_true", help="after building, add entries for named characters the seed chapters keep mentioning but the bible lacks (thin_review bible)")
    parser.add_argument("--bible-chapters", type=int, default=5, help="seed the bible from the first N chapters; later characters and locations are added chapter by chapter during the batch (--grow-bible)")
    parser.add_argument("--genre", help="genre preset key (configs/genres/*.json); default: detected from the bible's genre line and the opening chapters")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    source = Path(args.source).resolve()
    novel = read_novel(source, novel_id=args.novel_id, title=args.title)
    novel_dir = Path(args.output_root).resolve() / args.novel_id
    novel_dir.mkdir(parents=True, exist_ok=True)
    profile = {"style": args.style, "frame": args.frame}
    chapters = [{"index": e.index, "title": e.source_title, "chars": e.text_count} for e in novel.episodes]
    print(json.dumps({"chapter_count": len(chapters), "first": chapters[:3], "last": chapters[-1:], "seed_chapters": args.bible_chapters}, ensure_ascii=False), flush=True)

    bible_path = novel_dir / "story_bible.json"
    if bible_path.is_file() and not args.force:
        bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
        print(json.dumps({"bible": "kept existing", "characters": len(bible.characters), "locations": len(bible.locations)}, ensure_ascii=False), flush=True)
    else:
        started = time.monotonic()
        seed = novel.model_copy(update={"text": "\n\n".join(e.source_text for e in novel.episodes[:max(1, args.bible_chapters)])})
        bible = build_bible(seed, args.style)
        atomic_write_json(bible_path, bible.model_dump(mode="json"))
        print(json.dumps({"bible": "built", "seconds": round(time.monotonic() - started, 1), "characters": [c.name for c in bible.characters], "locations": [l.split("：", 1)[0] for l in bible.locations]}, ensure_ascii=False), flush=True)

    seed_text = "".join(e.source_text for e in novel.episodes[:max(1, args.bible_chapters)])
    genre_key = args.genre or detect_genre(bible.genre or "", args.title, seed_text[:20000])
    profile["genre"] = genre_key
    genre = load_genre(profile)
    print(json.dumps({"genre": genre_key, "genre_name": genre["name"]}, ensure_ascii=False), flush=True)
    profile_path = novel_dir / "profile.json"
    if not profile_path.is_file() or args.force:
        atomic_write_json(profile_path, profile)
    else:
        current = json.loads(profile_path.read_text(encoding="utf-8"))
        if "genre" not in current:
            current["genre"] = genre_key
            atomic_write_json(profile_path, current)
    grammar_path = novel_dir / "visual_grammar.json"
    if not grammar_path.is_file():
        grammar = json.loads((TEMPLATES / "visual_grammar.json").read_text(encoding="utf-8"))
        grammar["location_time"] = {location.split("：", 1)[0].strip(): "" for location in bible.locations}
        grammar["rejects"] = list(dict.fromkeys([*grammar.get("rejects", []), *genre.get("grammar_rejects_extra", [])]))
        grammar["name"] = f"{grammar['name']} · 题材预设：{genre['name']}"
        atomic_write_json(grammar_path, grammar)
    chat_path = novel_dir / "chat_screen.json"
    wants_chat = bool(genre.get("chat_screen")) or ("群聊" in seed_text or "微信" in seed_text or seed_text.count("群") >= 20)
    if wants_chat and not chat_path.is_file() and (TEMPLATES / "chat_screen.json").is_file():
        # Group-chat novels: guess the group's name (the most frequent XX群 in the
        # seed chapters) and the protagonist as "self"; the user can edit the file.
        import collections
        seed_text = "".join(e.source_text for e in novel.episodes[:max(1, args.bible_chapters)])
        counts = collections.Counter(m.group(1) for m in re.finditer(r"([一-鿿]{2,6}群)(?=[，。！？、：\s])", seed_text))
        for generic in ("人群", "这个群", "一群", "这群", "那群", "的群"):
            counts = collections.Counter({k: v for k, v in counts.items() if not k.endswith(generic) and k != generic})
        group = counts.most_common(1)[0][0] if counts and counts.most_common(1)[0][1] >= 2 else ""
        chat = json.loads((TEMPLATES / "chat_screen.json").read_text(encoding="utf-8"))
        chat["group_name"] = group
        chat["self_name"] = next((c.name for c in bible.characters if "主角" in c.role), "")
        atomic_write_json(chat_path, chat)
        print(json.dumps({"chat_screen": str(chat_path), "group_name": group, "self_name": chat["self_name"]}, ensure_ascii=False), flush=True)
    overrides_example = ROOT / "configs" / "fentian" / "clip_overrides.example.json"
    if overrides_example.is_file() and not (novel_dir / "clip_overrides.example.json").is_file():
        shutil.copy2(overrides_example, novel_dir / "clip_overrides.example.json")
    atomic_write_json(novel_dir / "novel.json", {
        "novel_id": args.novel_id, "title": args.title, "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "chapters": chapters, "seed_chapters": args.bible_chapters,
        "profile": profile, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    filled: list[str] = []
    if args.fill:
        from review_bible_thin import review_bible
        review = review_bible(novel_dir, source, fill=True, chapters=max(1, args.bible_chapters))
        filled = review["filled"]
        bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    (novel_dir / "story_bible.md").write_text(bible_markdown(bible, novel, source, profile), encoding="utf-8")
    print(json.dumps({
        "novel_dir": str(novel_dir), "review": str(novel_dir / "story_bible.md"), "filled_characters": filled,
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
