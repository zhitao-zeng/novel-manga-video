"""Give a bible's bare location names the description their empty-scene card needs.

The bible used to be asked for 地点名 alone, so every book built before that changed has locations that
are names and nothing else; location_prompt then tells the image model to fix the architecture, layout,
key objects, weather, time and light direction of a place it was never told anything about.

This fills them in from the novel's own text. A location whose name matches nothing in the source is
left alone and listed: an invented description would be worse than a bare name, because it reads as
evidence.

    python scripts/fill_locations_thin.py --novel-dir outputs/X [--dry-run] [--only 诸葛庐石碑园地中心]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.llm.client import ask_json  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402

WINDOW = 420      # characters of source kept around each hit
MAX_HITS = 6      # passages shown per location


def tokens(name: str) -> list[str]:
    """Distinctive pieces of a bible location name, longest first.

    The names are the bible's own wording ("诸葛庐碑碣深处凉亭") and rarely appear verbatim, but their
    parts do. Two-character pieces are too common to search on.
    """
    parts = [p for p in re.split(r"[（）()·、，,：:]", name) if p]
    out: list[str] = []
    for part in parts:
        for size in range(min(6, len(part)), 2, -1):
            for start in range(0, len(part) - size + 1):
                piece = part[start:start + size]
                if piece not in out:
                    out.append(piece)
    return sorted(out, key=len, reverse=True)


def passages(text: str, name: str) -> tuple[list[str], str]:
    for piece in tokens(name):
        spots = [m.start() for m in re.finditer(re.escape(piece), text)]
        if not spots or len(spots) > 400:       # a piece this common is not this place
            continue
        hits = []
        for spot in spots[:MAX_HITS]:
            chunk = text[max(0, spot - WINDOW // 2): spot + WINDOW // 2]
            hits.append(chunk.replace("　", "").replace("\n", " "))
        return hits, piece
    return [], ""


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["description", "grounded"],
    "properties": {
        "description": {"type": "string", "maxLength": 160},
        "grounded": {"type": "boolean"},
    },
}


def describe(name: str, hits: list[str], genre: str) -> dict:
    parts = [{"type": "text", "text":
              "你在给一本小说的场景资产写空场描写，用来生成没有人物的背景图。\n"
              f"题材：{genre}。地点名：{name}\n"
              "下面是原文里提到这个地方的段落：\n" + "\n---\n".join(hits) +
              "\n\n写一句不超过 70 字的空场描写，交代这个地方的建筑结构、空间布局、固定陈设与材质，"
              "以及它常态下的时段和主光源方向。\n"
              "这张图会被这本书的很多镜头反复当背景板用，所以只写这个地方长期不变的样子：\n"
              "- 不写任何人、人群、人影、剪影；\n"
              "- 不写某一场戏里才有的临时状态（比如摊开的行李、摆在桌上的东西、正在发生的事）；\n"
              "- 只写原文支持的东西，原文没写的宁可不写，不要补想象的细节。\n"
              "如果这些段落根本不足以描述这个地方，grounded 填 false。"}]
    return ask_json(parts, SCHEMA, name="location-description", max_tokens=600, timeout=180)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--only", help="只补这一个地点（写地点名）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写回圣经")
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    bible_path = novel_dir / "story_bible.json"
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    novel = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    text = Path(novel["source"]).read_text(encoding="utf-8")
    genre = json.loads((novel_dir / "profile.json").read_text(encoding="utf-8")).get("genre", "")

    out, filled, ungrounded, skipped = [], [], [], []
    for entry in bible["locations"]:
        name = str(entry)
        if "：" in name or (args.only and name != args.only):
            out.append(entry)
            continue
        hits, piece = passages(text, name)
        if not hits:
            out.append(entry)
            skipped.append(name)
            print(f"× {name}：原文里找不到线索，保持原样")
            continue
        answer = describe(name, hits, genre)
        description = answer["description"].strip()
        if not answer.get("grounded") or not description:
            out.append(entry)
            ungrounded.append(name)
            print(f"? {name}（匹配「{piece}」）：证据不足，保持原样")
            continue
        out.append(f"{name}：{description}")
        filled.append(name)
        print(f"✓ {name}（匹配「{piece}」）\n    {description}")

    bible["locations"] = out
    if not args.dry_run and filled:
        atomic_write_json(bible_path, bible)
    print(json.dumps({"filled": len(filled), "ungrounded": ungrounded, "no_match": skipped,
                      "written": bool(filled) and not args.dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
