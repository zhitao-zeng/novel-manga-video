"""Read a book with the lean pass and fold what it finds into the bible.

Three steps, all free: every chapter read on its own and all at once over the local Qwen instances,
the reads folded into one table of name forms, and one call that decides who is who over the whole
table at the same time.  See novel_manga.story.lean_reading for why the merge is a call and not an
agent.

What it writes back is the reading, not the art direction.  A character the pass finds and the bible
does not gets added with the evidence behind it; a character the bible already has keeps its
appearance, wardrobe and casting profile, because those came from a prompt that was asked to design
them and this pass only quotes the source.  Same for locations: a new place arrives as 名字：描写, and
one already in the bible is left alone.

归并走 agent：仓库写输入，沙箱跑，仓库读 output/export/ —— 和技能写分镜是同一个接缝。

    python scripts/lean_bible_thin.py --novel-dir outputs/X --chapters 1-100 \
        --agent-input /mnt/disk1/zengzhitao/tmp/agent-sandbox/runs/<名字>
    # 然后在沙箱里：AGENT_MODEL=Qwen3.8-Flash-Next bash run_skill_keyed.sh <名字> runs/<名字>/prompt.txt

不带 --agent-input 时走单次调用，那是**小书的近路**：一本书的条目装不进一次响应。
超品相师 1-100 同一份候选表，agent 写出 54 个人物 183 个地点（87 KB），单次调用只拿到 44 个人物、
地点为空。判断需要全书视野说的是输入；写出答案不需要，agent 一次写一个文件天然把它拆开了。

    python scripts/lean_bible_thin.py --novel-dir outputs/X --chapters 1-5 --into-bible   # 小书
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.llm.client import ask_json  # noqa: E402
from novel_manga.review.endpoints import judge_settings  # noqa: E402
from novel_manga.story import lean_reading as lean  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402

PER_ENDPOINT = 3  # max-num-seqs is 6 on these instances; leave room for everything else


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


def chapter_texts(novel: dict, wanted: list[int]) -> dict[int, str]:
    """Cut each wanted chapter out of the source, in one pass over it."""
    lines = Path(novel["source"]).read_text(encoding="utf-8").splitlines()
    titles = {c.get("title", "").strip(): int(c["index"]) for c in novel.get("chapters", [])}
    starts: dict[int, int] = {}
    for position, line in enumerate(lines):
        index = titles.get(line.strip())
        if index is not None:
            starts.setdefault(index, position)
    ordered = sorted(starts.items(), key=lambda kv: kv[1])
    ends = {index: (ordered[n + 1][1] if n + 1 < len(ordered) else len(lines))
            for n, (index, _) in enumerate(ordered)}
    out = {}
    for chapter in wanted:
        if chapter not in starts:
            raise ValueError(f"在原文里找不到第 {chapter} 章的标题")
        out[chapter] = "\n".join(lines[starts[chapter]:ends[chapter]]).strip()
    return out


def read_chapters(title: str, texts: dict[int, str], out_dir: Path, endpoints: list[str]) -> dict:
    """One call per chapter, fanned out; a chapter already on disk is not read again."""
    out_dir.mkdir(parents=True, exist_ok=True)
    from novel_manga.llm.config import JsonEndpoint

    def one(job: tuple[int, str]) -> str:
        chapter, endpoint = job
        path = out_dir / f"ch-{chapter:03d}.json"
        if path.exists():
            return f"第{chapter}章：已有"
        settings = JsonEndpoint.from_env({
            "QWEN38_LOCAL_BASE_URL": endpoint,
            "QWEN38_LOCAL_MODEL": os.environ.get("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"),
            "QWEN38_LOCAL_API_KEY_VAR": "LEAN_NO_KEY", "QWEN38_LOCAL_STREAM": "0"})
        prompt = lean.EXTRACT_PROMPT.format(title=title, n=chapter, text=texts[chapter])
        started = time.time()
        try:
            answer = ask_json([{"type": "text", "text": prompt}], lean.EXTRACT_SCHEMA,
                              name="lean_extract", max_tokens=8000, settings=settings)
        except Exception as error:  # noqa: BLE001 - one chapter failing must not stop the book
            return f"第{chapter}章：失败 {type(error).__name__}: {str(error)[:160]}"
        path.write_text(json.dumps({"chapter": chapter, "answer": answer,
                                    "seconds": round(time.time() - started, 1)},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return (f"第{chapter}章：{len(answer['people'])} 个称谓、{len(answer['locations'])} 个地点，"
                f"{round(time.time() - started, 1)}s")

    jobs = [(chapter, endpoints[n % len(endpoints)]) for n, chapter in enumerate(sorted(texts))]
    with ThreadPoolExecutor(max_workers=PER_ENDPOINT * len(endpoints)) as pool:
        for line in pool.map(one, jobs):
            print("  " + line, flush=True)

    extracts = {}
    for chapter in texts:
        path = out_dir / f"ch-{chapter:03d}.json"
        if path.exists():
            extracts[chapter] = json.loads(path.read_text(encoding="utf-8"))["answer"]
    return extracts


def missing_locations(bible: dict, export: dict) -> list[dict]:
    """Places the reading found that the bible has no name for, coarser duplicates removed."""
    have = [str(entry).split("：", 1)[0].strip() for entry in bible.get("locations", [])]
    out = []
    for place in export.get("locations", []):
        name = place["name"].strip()
        if not name or any(name in known or known in name for known in have):
            continue
        out.append(place)
    return out


def planned_cast(novel_dir: Path) -> set[str]:
    """Names on stage in an episode that has been planned - they need a face, recurring or not.

    The reading rules a one-chapter character an extra, which is right for a hundred-chapter series
    and wrong for the episode that character carries: 任老 reads 秦宇's fortune in chapter 1 and never
    returns, and episode 1 is that reading.  An extra speaks from offscreen; he cannot.
    """
    out: set[str] = set()
    for episode_dir in novel_dir.glob("*"):
        script = episode_dir / "chapter_script.json"
        if not episode_dir.is_dir() or not script.is_file():
            continue
        try:
            shots = json.loads(script.read_text(encoding="utf-8")).get("shots", [])
        except (OSError, ValueError):
            continue
        for shot in shots:
            out.update(shot.get("characters") or [])
            for stage in shot.get("stages") or []:
                out.update(stage.get("in_frame") or [])
    return out


def declined_cast(bible: dict, export: dict, candidates: dict) -> list[dict]:
    """Bible characters the reading did not make an entry for, with the evidence behind that.

    The reading keeps a descriptor only when three or more chapters use it for the same person; the
    rest are extras.  Each one here costs a card that the story never reuses.
    """
    kept = {person["name"] for person in export.get("characters", [])}
    for person in export.get("characters", []):
        kept.update(person.get("aliases") or [])
    listed = {row["form"]: row for row in candidates.get("forms", [])}
    out = []
    for character in bible.get("characters", []):
        if not isinstance(character, dict):
            continue
        name = character.get("name", "")
        if not name or name in kept:
            continue
        row = listed.get(name, {})
        out.append({"name": name,
                    "chapters": len(row.get("listed") or []),
                    "on_stage": len(row.get("on_stage") or []),
                    "speaks": len(row.get("speaks") or []),
                    "appearance": str(character.get("appearance") or "")[:40]})
    out.sort(key=lambda item: (-item["chapters"], item["name"]))
    return out


def fold_into_bible(novel_dir: Path, export: dict, add_locations: bool = False) -> dict:
    """Add what the reading found and the bible lacks; never overwrite what is already drawn.

    The pass quotes the source; it does not design a look.  So an existing character keeps its
    appearance, wardrobe and casting profile - a card has probably been drawn from them - and only a
    character the bible has never heard of is added, with the evidence that justifies it.
    """
    path = novel_dir / "story_bible.json"
    bible = json.loads(path.read_text(encoding="utf-8"))
    known = {c.get("name") for c in bible.get("characters", []) if isinstance(c, dict)}
    places = {str(entry).split("：", 1)[0].strip() for entry in bible.get("locations", [])}
    tiers = {"主要": "主角", "重要": "重要配角", "次要": "配角", "龙套": "龙套"}

    added_characters, added_locations, skipped = [], [], []
    for person in export.get("characters", []):
        if person["name"] in known:
            continue
        if not person.get("appearance", "").strip() or not person.get("wardrobe", "").strip():
            # The bible validator requires both, and an empty one would fail the next build; the
            # grower can design them later from a name the reading has now put on record.
            skipped.append(f"{person['name']}（缺外貌或服装）")
            continue
        bible.setdefault("characters", []).append({
            "name": person["name"], "role": tiers.get(person.get("tier", ""), "配角"),
            "gender": person.get("gender", "未写明"), "age": person.get("age", "未写明"),
            "appearance": person["appearance"], "wardrobe": person["wardrobe"]})
        added_characters.append(person["name"])
    if add_locations:
        # By name only.  The pass quotes what a chapter says about a place, and that is not the
        # empty-stage plate a card is drawn from - it has people in it and skips the light.  The
        # audit will flag a bare name and fill_locations_thin writes the description from the source.
        for place in missing_locations(bible, export):
            bible.setdefault("locations", []).append(place["name"].strip())
            added_locations.append(place["name"].strip())

    aliases_path = novel_dir / "bible_aliases.json"
    aliases = {}
    if aliases_path.exists():
        try:
            aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            aliases = {}
    for person in export.get("characters", []):
        for alias in person.get("aliases", []):
            aliases.setdefault(alias, person["name"])

    atomic_write_json(path, bible)
    atomic_write_json(aliases_path, aliases)
    return {"characters": added_characters, "locations": added_locations, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", required=True, type=Path)
    parser.add_argument("--chapters", required=True, help="例如 1-5 或 1,3,5")
    parser.add_argument("--judge", default="flashnext", help="归并用哪个端点：flashnext / local")
    parser.add_argument("--agent-input", type=Path,
                        help="主路径：把归并交给沙箱里的 agent，输入写到这个目录，跑完读 output/export/")
    parser.add_argument("--endpoints", help="逐章提取用的端点，逗号分隔；默认读 .env 的 Qwen 列表")
    parser.add_argument("--out", type=Path, help="中间结果目录，默认 <novel-dir>/lean")
    parser.add_argument("--into-bible", action="store_true", help="把读到的人物和别名并进 story_bible.json")
    parser.add_argument("--add-missing-locations", action="store_true",
                        help="圣经没有的地方按名字加进去，再用 fill_locations_thin 补描写")
    parser.add_argument("--demote-extras", action="store_true",
                        help="把读书判定为路人的角色从圣经里去掉：他们靠画面文字和匿名说话人出镜，不画卡")
    args = parser.parse_args()

    novel_dir = args.novel_dir if args.novel_dir.is_absolute() else ROOT / args.novel_dir
    out_dir = args.out or (novel_dir / "lean")
    novel = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    title = novel.get("title", novel_dir.name)
    wanted = chapters_of(args.chapters)
    endpoints = [e.strip() for e in (args.endpoints or os.environ.get("QWEN38_LOCAL_BASE_URL", "")).split(",") if e.strip()]
    if not endpoints:
        parser.error("没有可用的提取端点：给 --endpoints，或在环境里设 QWEN38_LOCAL_BASE_URL")

    print(f"《{title}》第 {wanted[0]}–{wanted[-1]} 章，{len(endpoints)} 个端点")
    texts = chapter_texts(novel, wanted)
    started = time.time()
    extracts = read_chapters(title, texts, out_dir / "extract", endpoints)
    print(f"逐章读完 {len(extracts)}/{len(wanted)} 章，{time.time() - started:.0f}s")
    if len(extracts) < len(wanted):
        print(f"缺章：{sorted(set(wanted) - set(extracts))}——先补齐再归并", file=sys.stderr)
        return 1

    candidates = lean.aggregate(extracts, texts)
    table = lean.candidate_table(title, candidates, wanted[-1])
    (out_dir / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "candidates.md").write_text(table, encoding="utf-8")
    print(f"候选表：{len(candidates['forms'])} 个称谓、{len(candidates['locations'])} 个地点")

    if args.agent_input:
        # The repo writes the input, the sandbox runs the agent, the repo reads output/export/ -
        # the same seam the authoring skills already use.
        directory = args.agent_input
        (directory / "input").mkdir(parents=True, exist_ok=True)
        (directory / "output").mkdir(parents=True, exist_ok=True)
        (directory / "input" / "candidates.md").write_text(table, encoding="utf-8")
        (directory / "input" / "candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8")
        (directory / "input" / "任务说明_归并.md").write_text(
            lean.merge_brief(title, wanted[-1]), encoding="utf-8")
        (directory / "input" / "source.md").write_text(
            "\n\n".join(texts[chapter] for chapter in sorted(texts)), encoding="utf-8")
        (directory / "prompt.txt").write_text(
            "这是一次无人值守的批量运行，没有人会回复你，不要停下来等待回答。\n\n"
            "请先完整读 input/任务说明_归并.md，再读 input/candidates.md，"
            "按任务说明决定谁是谁、有哪些地点，把结果写进 output/export/。\n", encoding="utf-8")
        print(f"agent 输入已写到 {directory}；候选表 {len(table)} 字，原文 {sum(len(t) for t in texts.values())} 字")
        return 0

    started = time.time()
    # Two calls over the same table: who is who and where is where are independent judgements, and a
    # hundred chapters of both in one answer runs past the output budget half-written.
    settings = judge_settings(args.judge)
    export = ask_json([{"type": "text", "text": lean.MERGE_PROMPT.format(
        title=title, last=wanted[-1], table=table)}], lean.MERGE_SCHEMA, name="lean_merge",
        max_tokens=32000, timeout=1800, settings=settings)
    export["locations"] = ask_json([{"type": "text", "text": lean.MERGE_LOCATIONS_PROMPT.format(
        title=title, last=wanted[-1], table=table)}], lean.MERGE_LOCATIONS_SCHEMA,
        name="lean_merge_locations", max_tokens=32000, timeout=1800,
        settings=settings).get("locations", [])
    (out_dir / "export.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归并：{len(export['characters'])} 个人物、{len(export['locations'])} 个地点，"
          f"{time.time() - started:.0f}s")
    for person in export["characters"]:
        aliases = "／".join(person["aliases"]) or "—"
        print(f"  {person['tier']} {person['name']}（别名 {aliases}）"
              f" 第{person['first_chapter']}章起，{len(person['chapters'])} 章")
        for doubt in person.get("unresolved", []):
            print(f"      未决：{doubt}")

    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    absent = missing_locations(bible, export)
    if absent:
        print(f"圣经没有、但原文里读到的地方 {len(absent)} 处——这些章的镜头只能挤进别的地点：")
        for place in absent:
            print(f"  {place['name']}（第{place['first_chapter']}章起，"
                  f"{len(place['chapters'])} 章）")

    declined = declined_cast(bible, export, candidates)
    if declined:
        print(f"读书判定为路人的 {len(declined)} 个（圣经有、读书没建条目）——每个都在花一张卡：")
        for item in declined:
            print(f"  {item['name']}：出现 {item['chapters']} 章、在场 {item['on_stage']} 章、"
                  f"说话 {item['speaks']} 章｜{item['appearance']}")
        if args.demote_extras:
            safe = planned_cast(novel_dir)
            removed = [item["name"] for item in declined if item["name"] not in safe]
            kept_back = [item["name"] for item in declined if item["name"] in safe]
            bible["characters"] = [c for c in bible.get("characters", [])
                                   if not (isinstance(c, dict) and c.get("name") in removed)]
            atomic_write_json(novel_dir / "story_bible.json", bible)
            print(f"已从圣经去掉 {len(removed)} 个：{'、'.join(removed) or '无'}")
            if kept_back:
                print(f"留着没动（在已排的集里有戏，需要一张脸）：{'、'.join(kept_back)}")

    if args.into_bible:
        result = fold_into_bible(novel_dir, export, args.add_missing_locations)
        print(f"并进圣经：新增人物 {result['characters'] or '无'}；新增地点 {result['locations'] or '无'}")
        if result["skipped"]:
            print(f"  没并进去：{'、'.join(result['skipped'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
