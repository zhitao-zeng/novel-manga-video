"""Is this still what the author wrote?  Asked of every layer, for one chapter.

The binder's rule - the model is never asked for what the author wrote - can only be checked where
the author's words end up, not where they are stored.  Every defect this found was a module doing
something reasonable to a storyboard it did not know a person had cut: the durations were kept by the
binder and dropped by the next step, a two-hander was split one shot per speaker (95 seconds became
232), a coverage gate answered an uncited passage by writing a shot nobody asked for.

    python scripts/authored_trace_thin.py --novel-dir outputs/<书> --chapter 10

Three kinds of difference, and only one of them is a fault:

    verbatim   - the picture, the cut, the length: these must match the sheet exactly
    resolved   - 蝙蝠侠 becomes 布鲁斯·韦恩, 学生甲 becomes an anonymous speaker: that is the binding's job
    defaulted  - the sheet left a field empty and the pipeline filled it: nothing was lost
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.planning import storyboard as sb

OK, BAD = "✅", "❌"


def spoken(shot: dict) -> list[dict]:
    return [t for t in shot.get("turns") or [] if t["delivery_mode"] != "silent_action"]


def trace(novel_dir: Path, chapter: int) -> int:
    episode = novel_dir / f"{novel_dir.name}_{chapter}"
    state = json.loads((episode / "agent_storyboard.json").read_text(encoding="utf-8"))
    sheets = sb.read_workbook(episode / state["sheet"])
    sheet = next((s for s in sheets if s.name == state["sheet_name"]), sheets[0])
    script = json.loads((episode / "chapter_script.json").read_text(encoding="utf-8"))
    shots, rows = script["shots"], sheet.rows
    by_id = {s["authored_id"]: s for s in shots if s.get("authored_id")}
    faults = []

    def check(condition: bool, what: str) -> None:
        print(f"  {OK if condition else BAD} {what}")
        if not condition:
            faults.append(what)

    print(f"第 {chapter} 章 · 原表 {len(rows)} 镜 · 剧本 {len(shots)} 镜 · "
          f"{sum(float(r['edit_seconds']) for r in rows):g} 秒 · 技能 {state.get('skill')}")
    print(f"  取自 {state.get('accepted_from')}")

    print("\n原样保留的（必须逐字一致）")
    pairs = [(r, by_id.get(r["authored_id"])) for r in rows]
    check(all(s for _, s in pairs), f"每一镜都在（{sum(1 for _, s in pairs if s)}/{len(rows)}）")
    check(len(shots) == len(rows), f"没有多出来的镜头（多 {len(shots) - len(rows)}）")
    pairs = [(r, s) for r, s in pairs if s]
    check(all(s["motion_prompt"] == r["motion_prompt"] for r, s in pairs), "画面内容 / 动作")
    check(all(s["shot_scale"] == r["shot_scale"] for r, s in pairs), "景别")
    check(all(r["camera_angle"] in s["camera"] and r["camera"] in s["camera"] for r, s in pairs),
          "摄影角度与机位")
    check(all(s.get("duration_seconds") == float(r["edit_seconds"]) for r, s in pairs), "预算秒")
    check(all(s.get("purpose") == r["narrative_purpose"] for r, s in pairs), "叙事目的")
    check(all(s["location"] == r["location"] for r, s in pairs), "场景")

    lines = [(r, w, g) for r, s in pairs
             for w, g in zip(sb.authored_sound(r["authored_sound"]).turns, spoken(s))]
    counts = all(len(sb.authored_sound(r["authored_sound"]).turns) == len(spoken(s)) for r, s in pairs)
    check(counts, f"台词句数（{len(lines)} 句）")
    check(all(w["text"] == g["text"] for _, w, g in lines), "每句台词逐字")
    check(all(w["emotion"] == g.get("emotion", "") for _, w, g in lines if w["emotion"]), "写了的语气")

    print("\n绑定解出来的（本来就该变）")
    resolved = sorted({(w["written_speaker"], g["speaker_name"]) for _, w, g in lines
                       if w["written_speaker"] != g["speaker_name"]})
    print(f"  说话人 {len(resolved)} 处：{resolved or '无'}")
    offscreen = [(r["authored_id"], w["written_speaker"]) for r, w, g in lines
                 if w["delivery_mode"] != g["delivery_mode"]]
    print(f"  发声方式改判 {len(offscreen)} 处：{offscreen or '无'}（匿名说话人不能露脸）")
    blank = sum(1 for _, w, _ in lines if not w["emotion"])
    print(f"  语气：原表 {len(lines) - blank} 句写了，{blank} 句留空由流水线补默认值")

    report = episode / "chapter_script_report.json"
    if report.is_file():
        data = json.loads(report.read_text(encoding="utf-8"))
        print(f"\n规划报告：{data.get('status')} · 闸门 {data.get('hard_gates')}")
        for warning in data.get("warnings") or []:
            if any(k in str(warning) for k in ("不拆", "取舍", "超过单段上限")):
                print(f"  ⚠ {warning}")
    print(f"\n{'原表到剧本这一段交接是干净的' if not faults else '有 ' + str(len(faults)) + ' 处没保住：' + '、'.join(faults)}")
    return 0 if not faults else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapter", type=int, required=True)
    args = parser.parse_args()
    return trace(args.novel_dir.resolve(), args.chapter)


if __name__ == "__main__":
    raise SystemExit(main())
