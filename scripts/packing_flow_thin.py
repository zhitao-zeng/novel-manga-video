"""Packing reads/writes and existing report invalidation; no model requests."""
from __future__ import annotations

from pathlib import Path
import json
import time
from novel_manga.util import atomic_write_json
from packing_context_thin import load_context, POLICY
from packing_service_thin import compile_plan
from thin_profile import plan_fingerprint

def carry_corrections(old_plan: dict | None, plan: dict, feedback_path: Path) -> dict:
    """Keep each director correction on the clip it was written for.

    Corrections are keyed by clip id, and a new plan's ids can name other clips (a re-plan, a re-pack): a note about
    乙 landed on the clip that now shows 甲.  A note stays only where the new plan has the very clip it was written
    for - the same prompt - under that clip's id there; the others are set aside beside it.  Returns those."""
    if not feedback_path.is_file():
        return {}
    try:
        notes = json.loads(feedback_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    old_prompts = {clip.get("clip_id"): clip.get("prompt") for clip in (old_plan or {}).get("clips", [])}
    new_ids: dict[str, str] = {}
    for clip in plan.get("clips", []):
        if clip.get("prompt"):
            new_ids.setdefault(clip["prompt"], clip["clip_id"])
    kept, dropped = {}, {}
    for clip_id, note in notes.items():
        target = new_ids.get(old_prompts.get(clip_id) or "")
        if target:
            kept[target] = note
        else:
            dropped[clip_id] = note
    if dropped:
        atomic_write_json(feedback_path.with_name(f"review_feedback.set-aside-{time.strftime('%Y%m%d-%H%M%S')}.json"),
                          {"reason": "the clip plan was written again and these clips are not in it", "notes": dropped})
    if kept != notes:
        atomic_write_json(feedback_path, kept)
    return dropped


def run(args) -> int:
    episode_dir = args.episode_dir.resolve()
    ctx = load_context(episode_dir, args.bible, args.grammar, args.style, args.frame, args.tier)
    script = json.loads((episode_dir / 'chapter_script.json').read_text(encoding='utf-8'))
    plan, decisions = compile_plan(script, ctx)
    clips, totals = plan['clips'], plan['totals']
    try:
        old_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old_plan = None
    atomic_write_json(episode_dir / "clip_plan.json", plan)
    carry_corrections(old_plan, plan, episode_dir / "review_feedback.json")
    # Observation only: why the packer cut where it did.  clip_plan.json is unchanged by this.
    atomic_write_json(episode_dir / 'pack_decisions.json', decisions)
    report_path = episode_dir / "thin_media_report.json"
    if report_path.is_file():
        # The runner stamps the plan it rendered; a report for a different plan
        # would let a batch driver skip this episode as finished.
        stamped = json.loads(report_path.read_text(encoding="utf-8")).get("clip_plan_fingerprint")
        current = plan_fingerprint(plan)
        if stamped and stamped != current:  # reports from before the stamp are trusted
            report_path.unlink()
            (episode_dir / "media_qc_report.json").unlink(missing_ok=True)
            print(json.dumps({"note": "clip plan changed; stale thin_media_report.json removed"}, ensure_ascii=False))
    md = [f"# 片段计划（{POLICY}）", "", f"{totals['video_clip_count']} 段视频，{totals['shot_count']} 镜，预计 {totals['estimated_seconds']} 秒，申请 {totals['requested_seconds']} 秒", "", "| 片段 | 镜 | 阶段数 | 预计秒 | 申请秒 | 人物 | 台词 |", "|---|---|---|---|---|---|---|"]
    for clip in clips:
        if clip["kind"] != "video":
            md.append(f"| {clip['clip_id']} | {clip['shot_indexes']} | 字幕卡 | {clip['seconds_estimate']} | {clip['request_seconds']} | | {clip['text']} |")
            continue
        md.append(f"| {clip['clip_id']} | {clip['shot_indexes'][0]}–{clip['shot_indexes'][-1]} | {clip['stage_count']} | {clip['seconds_estimate']} | {clip['request_seconds']} | {'、'.join(clip['cast'])} | {len(clip['lines'])} 条 |")
    md.append("")
    for clip in clips:
        if clip["kind"] != "video":
            continue
        md.append(f"## {clip['clip_id']} · 镜 {clip['shot_indexes'][0]}–{clip['shot_indexes'][-1]} · {clip['request_seconds']} 秒")
        md.append("参考图：" + "；".join(f"{ref['tag']}={ref['path']}" for ref in clip["references"]))
        md.append("")
        md.append("```")
        md.append(clip["prompt"])
        md.append("```")
        if clip.get("lint"):
            md.append("提示词检查（只报告）：" + "；".join(f"镜{k}: {', '.join(v)}" for k, v in clip["lint"].items()))
        md.append("")
    (episode_dir / "clip_plan.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"totals": totals, "clips": [{k: clip[k] for k in ("clip_id", "shot_indexes", "seconds_estimate", "request_seconds") if k in clip} | {"cast": clip.get("cast", [])} for clip in clips]}, ensure_ascii=False, indent=2))
    return 0


