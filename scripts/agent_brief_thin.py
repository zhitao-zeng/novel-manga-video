"""Write the input directory an authoring agent reads, from the book's own bible.

Replaces the hand-kept 任务说明.md / 人物与画风.md copies.  Those had to be edited once per episode and
went stale silently: the thirty 超品相师 sheets were all written against a spec that had lost its
dialogue-format rule, and none of them ever saw the location list, so six skills invented 32 names
for four places.  Here the spec is one constant in the repo and everything else is read fresh.

    python scripts/agent_brief_thin.py --novel-dir outputs/X --chapter 3 --out runs/x3/input
    python scripts/agent_brief_thin.py --novel-dir outputs/X --chapters 1-5 --out-template runs/cp{n}/input
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.planning.authored_brief import write_brief  # noqa: E402


def chapters_of(text: str) -> list[int]:
    out: list[int] = []
    for piece in text.split(","):
        piece = piece.strip()
        match = re.fullmatch(r"(\d+)-(\d+)", piece)
        if match:
            out.extend(range(int(match.group(1)), int(match.group(2)) + 1))
        elif piece:
            out.append(int(piece))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", required=True, type=Path)
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--chapters", help="例如 1-5 或 1,3,5")
    parser.add_argument("--out", type=Path, help="写到这个目录（配合 --chapter）")
    parser.add_argument("--out-template", help="多章时的目录模板，用 {n} 代表章号")
    parser.add_argument("--skill", default="",
                        help="同时写出启动提示词 prompt.txt：drama/community/dream/leos/visual/shanyin")
    args = parser.parse_args()

    novel_dir = args.novel_dir if args.novel_dir.is_absolute() else ROOT / args.novel_dir
    wanted = chapters_of(args.chapters) if args.chapters else ([args.chapter] if args.chapter else [])
    if not wanted:
        parser.error("要么给 --chapter，要么给 --chapters")
    if len(wanted) > 1 and not args.out_template:
        parser.error("多章要用 --out-template，里面带 {n}")

    for chapter in wanted:
        out = Path(args.out_template.format(n=chapter)) if args.out_template else args.out
        for path in write_brief(novel_dir, chapter, out, args.skill):
            print(f"  第{chapter}章 → {path}  {path.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
