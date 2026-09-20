"""Give a bible's bare location names the description their empty-scene card needs.

The bible used to be asked for 地点名 alone, so every book built before that changed has locations that
are names and nothing else; location_prompt then tells the image model to fix the architecture, layout,
key objects, weather, time and light direction of a place it was never told anything about.

This fills them in from the novel's own text. A location whose name matches nothing in the source is
left alone and listed: an invented description would be worse than a bare name, because it reads as
evidence.

    python scripts/fill_locations_thin.py --novel-dir outputs/X [--dry-run]
        [--only 诸葛庐石碑园地中心] [--from-audit 审查结果.json]

With --from-audit it rewrites the locations that audit reported on - a plate with a crowd
in it, or one that never says whether it is day or night - keeping what the old wording
got right.  Without it, it fills bare names, as before.
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


def describe(name: str, hits: list[str], genre: str, current: str = "") -> dict:
    # A rewrite keeps whatever the old description got right about the architecture; dropping it and
    # starting from the source again loses detail that no passage states outright.
    source = ("下面是原文里提到这个地方的段落：\n" + "\n---\n".join(hits) + "\n\n") if hits else ""
    repair = (f"这个地方现在的描写是：{current}\n"
              "它违反了下面的规则，请逐句处理，不要整句重写：\n"
              "- 写建筑结构、空间布局、固定陈设、材质、层数、方位的部分一律原样保留，这是这张板的主体，不许精简；\n"
              "- 只删掉写人的部分、只属于某一场戏的临时状态、以及可辨读的文字；\n"
              "- 删完如果没交代时段或主光源，补一句；删完如果只剩十几个字，用上面的原文段落把固定的建筑陈设补回来。\n\n"
              ) if current else ""
    parts = [{"type": "text", "text":
              "你在给一本小说的场景资产写空场描写，用来生成没有人物的背景图。\n"
              f"题材：{genre}。地点名：{name}\n"
              + source + repair +
              "写一句不超过 70 字的空场描写，交代这个地方的建筑结构、空间布局、固定陈设与材质，"
              "以及它常态下的时段和主光源方向。\n"
              "这张图会被这本书的很多镜头反复当背景板用，所以只写这个地方长期不变的样子：\n"
              "- 不写任何人、人群、人影、剪影；\n"
              "- 不写某一场戏里才有的临时状态（比如摊开的行李、摆在桌上的东西、正在发生的事）；\n"
              "- 只写原文支持的东西，原文没写的宁可不写，不要补想象的细节。\n"
              "写到碑文、匾额、招牌、书页这类本来有字的东西时，写成看不出字形的样子（风化的刻痕、磨平的笔画、模糊的符号），不要写可辨读的文字——地点卡的判定会把可读文字判成缺陷。"
              + ("grounded 填的是「有没有原文段落支持你补充的新细节」；没有就填 false，"
                 "但 description 一定要给出改好的那一句，不能因为证据不足就把原句原样退回。"
                 if current else "如果这些段落根本不足以描述这个地方，grounded 填 false。")}]
    return ask_json(parts, SCHEMA, name="location-description", max_tokens=600, timeout=180)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--only", help="只补这一个地点（写地点名）")
    parser.add_argument("--from-audit", type=Path,
                        help="pipeline_audit.py --json 的结果；改写它报出问题的那些地点")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写回圣经")
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    bible_path = novel_dir / "story_bible.json"
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    novel = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    text = Path(novel["source"]).read_text(encoding="utf-8")
    genre = json.loads((novel_dir / "profile.json").read_text(encoding="utf-8")).get("genre", "")

    flagged: set[str] = set()
    if args.from_audit:
        prefix = "圣经·地点 "
        for finding in json.loads(args.from_audit.read_text(encoding="utf-8")):
            where = str(finding.get("where", ""))
            if where.startswith(prefix) and finding.get("level") in ("错", "漏"):
                flagged.add(where[len(prefix):].strip())

    out, filled, ungrounded, skipped = [], [], [], []
    for entry in bible["locations"]:
        name, _, current = str(entry).partition("：")
        name, current = name.strip(), current.strip()
        if args.only:
            wanted = name == args.only
        elif flagged:
            wanted = name in flagged
        else:
            wanted = not current
        if not wanted:
            out.append(entry)
            continue
        hits, piece = passages(text, name)
        if not hits and not current:
            out.append(entry)
            skipped.append(name)
            print(f"× {name}：原文里找不到线索，保持原样")
            continue
        answer = describe(name, hits, genre, current)
        description = answer["description"].strip()
        # 改写时证据不足只许删不许添：留着旧描写就是留着审查报出来的那个缺陷，而删掉违规内容不需要证据。
        if not description or (not answer.get("grounded") and not current):
            out.append(entry)
            ungrounded.append(name)
            print(f"? {name}（匹配「{piece}」）：证据不足，保持原样")
            continue
        if not answer.get("grounded"):
            ungrounded.append(name)
            print(f"  （{name}：没有补充新细节，只删了违规部分）")
        out.append(f"{name}：{description}")
        filled.append(name)
        print(f"✓ {name}（{'改写' if current else '补全'}，匹配「{piece or '无，仅凭现有描写'}」）"
              f"\n    旧 {current or '（空）'}\n    新 {description}")

    bible["locations"] = out
    if not args.dry_run and filled:
        atomic_write_json(bible_path, bible)
    print(json.dumps({"filled": len(filled), "ungrounded": ungrounded, "no_match": skipped,
                      "written": bool(filled) and not args.dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
