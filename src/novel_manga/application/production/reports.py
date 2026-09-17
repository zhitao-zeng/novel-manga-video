"""production_reports_thin responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
import json
import sys
import time
import novel_manga.application.production.common as production_common

def volume_checkpoint(batch, chapter: int, chapters: list[int]) -> None:
    size = max(1, batch.args.volume_size)
    if chapter % size and chapter != chapters[-1]:
        return
    volume = (chapter - 1) // size + 1
    first = (volume - 1) * size + 1
    growth_path = batch.novel_dir / "bible_growth.json"
    growth = json.loads(growth_path.read_text(encoding="utf-8")) if growth_path.is_file() else {}
    added_characters, added_locations, suggestions = [], [], {}
    for index in range(first, chapter + 1):
        entry = growth.get(str(index), {})
        added_characters += entry.get("characters", [])
        added_locations += entry.get("locations", [])
        suggestions.update(entry.get("suggestions", {}))
    lines = [f"# 第 {volume} 卷复核（第 {first}–{chapter} 章）", "",
             f"- 本卷新增角色：{', '.join(added_characters) or '无'}", f"- 本卷新增地点：{', '.join(added_locations) or '无'}",
             f"- 待人工确认的称呼类人物（建议条目在 bible_growth.json）：{', '.join(suggestions) or '无'}", ""]
    flagged = [(ch, batch.rows[ch]) for ch in chapters if first <= ch <= chapter and (batch.rows[ch].get("card_flags") or batch.rows[ch].get("review_flags") or batch.rows[ch].get("note"))]
    lines.append("- 本卷标记：" + ("；".join(f"第{ch}章 {row.get('note') or ''} {' '.join(row.get('card_flags', []))} {' '.join(row.get('review_flags', []))}".strip() for ch, row in flagged) or "无"))
    # The bookkeeping above lists what was added; the arc summary keeps the
    # story itself in front of the planner once the five-chapter recap has
    # scrolled past it.
    try:
        from novel_manga.application.review.bible import summarize_volume
        arc = summarize_volume(batch.novel_dir, first, chapter)
        if arc:
            lines += ["", f"## 主线（第 {first}–{chapter} 章）", "", arc.get("summary", ""), "",
                      "未了结的线索：" + ("；".join(arc.get("open_threads", [])) or "无"), "",
                      "人物处境：" + ("；".join(arc.get("standing", [])) or "无")]
    except Exception as error:  # noqa: BLE001 - a missing summary must not stop the batch
        production_common.log(f"volume {volume} summary failed: {type(error).__name__}: {str(error)[:120]}")
    (batch.novel_dir / f"volume_review_{volume:03d}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    production_common.log(f"volume {volume} review written ({len(added_characters)} new characters, {len(added_locations)} new locations, {len(suggestions)} suggestions)")


def report(batch, chapters: list[int]) -> int:
    lines = ["| 章 | 规划 | 段 | 时长 | 薄门 | 渲染 | 备注 |", "|---|---|---|---|---|---|---|"]
    for chapter in chapters:
        row = batch.rows[chapter]
        lines.append(f"| {chapter} | {row.get('plan', batch.plan_status(chapter))} | {row.get('clips', '')} | {row.get('duration', '')} | {row.get('thin_passed', '')} | {row.get('render', batch.render_status(chapter))} | {row.get('note', '')} |")
    table = "\n".join(lines)
    print(table, flush=True)
    payload = {"novel_id": batch.novel_id, "chapters": chapters, "rows": batch.rows, "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "args": vars(batch.args)}
    (batch.novel_dir / "batch_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(batch.novel_dir / "batch_report.md", "a", encoding="utf-8") as handle:
        handle.write(f"\n## {payload['finished']} · chapters {batch.args.chapters} · stage {batch.args.stage}\n\n{table}\n")
    done = [ch for ch in chapters if batch.rows[ch].get("render", batch.render_status(ch)) in {"done", "done_with_warnings"}]
    production_common.log(f"{len(done)}/{len(chapters)} episodes have a final video")
    if batch.reviewing:
        delivery_report(batch, chapters)
        # The novel-level verdict the board shows (delivery.json): a review batch is what changes it.
        batch.run([sys.executable, str(production_common.SCRIPTS / "delivery_gate_thin.py"), "--novel-dir", str(batch.novel_dir), "--quiet"],
                 batch.novel_dir / "delivery_gate.log")
    return 0 if len(done) == len(chapters) or batch.args.stage != "all" else 2


def delivery_report(batch, chapters: list[int]) -> None:
    bible_review_path = batch.novel_dir / "bible_review.json"
    bible_review = json.loads(bible_review_path.read_text(encoding="utf-8")) if bible_review_path.is_file() else {}
    cards_path = batch.novel_dir / "series_assets" / "cards_review.json"
    cards = batch.card_review or (json.loads(cards_path.read_text(encoding="utf-8")) if cards_path.is_file() else {})
    per_episode_flags = sorted({flag for chapter in chapters for flag in batch.rows[chapter].get("card_flags", [])})
    per_episode_fixes = sorted({fix for chapter in chapters for fix in batch.rows[chapter].get("card_fixes", [])})
    lines = [f"# {batch.title} · 交付报告（{time.strftime('%Y-%m-%d %H:%M')}）", "", f"模式：{'无人值守（自动修复各一次）' if batch.args.unattended else '只审核不修复'}；章节 {batch.args.chapters}", ""]
    if bible_review:
        lines += ["## 圣经", "", f"- 原文高频但圣经缺失的人物：{', '.join(bible_review.get('missing', {})) or '无'}", f"- 自动补入：{', '.join(bible_review.get('filled', [])) or '无'}", ""]
    lines += ["## 角色卡与地点卡", ""]
    if batch.card_fixes:
        lines.append(f"- 自动重画（动画化）：{', '.join(batch.card_fixes.get('stylized', [])) or '无'}；删除重建：{', '.join(batch.card_fixes.get('deleted', [])) or '无'}")
    if per_episode_fixes:
        lines.append(f"- 各集建卡时自动修复：{', '.join(per_episode_fixes)}")
    lines.append(f"- 仍有标记：{'；'.join(per_episode_flags or cards.get('flags', [])) or '无'}")
    lines += ["", "## 各集", "", "| 集 | 时长 | 段 | 语音门未过 | 薄QC | 自动修正段 | 剩余标记 |", "|---|---|---|---|---|---|---|"]
    for chapter in chapters:
        row = batch.rows[chapter]
        report_path = batch.episode_dir(chapter) / "thin_media_report.json"
        data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        lines.append(f"| {chapter} | {row.get('duration', '')} | {row.get('clips', '')} | {', '.join(data.get('gate_failed_clips', [])) or '无'} | {row.get('thin_passed', '')} | {', '.join(row.get('auto_fixed', [])) or '无'} | {'；'.join(row.get('review_flags', [])) or '无'} |")
    videos = [row.get("video") for chapter in chapters for row in [batch.rows[chapter]] if row.get("video")]
    lines += ["", "## 交付文件", ""] + [f"- {video}" for video in videos]
    (batch.novel_dir / "delivery_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    production_common.log(f"delivery report: {batch.novel_dir / 'delivery_report.md'}")
