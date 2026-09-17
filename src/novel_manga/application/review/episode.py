"""Episode review orchestration: choose takes, reuse verdicts, isolate errors and write reports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
import novel_manga.review.contracts as review_contracts
import novel_manga.review.policy as review_policy
import novel_manga.review.storage as review_storage
from novel_manga.util import atomic_write_json
import novel_manga.application.review.evidence as review_evidence
import novel_manga.application.review.judges as review_judges


def review_episode(episode_dir: Path, video_name: str = "clip.mp4") -> dict:
    novel_dir = episode_dir.parent
    rules = review_evidence.load_review_rules(novel_dir)
    bible = review_models_StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    grammar_path = novel_dir / "visual_grammar.json"
    location_time = json.loads(grammar_path.read_text(encoding="utf-8")).get("location_time", {}) if grammar_path.is_file() else {}
    plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    manifest_path = novel_dir / "series_assets" / "manifest.json"
    card_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    report_path = episode_dir / "thin_media_report.json"
    media = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    selected = {row["clip_id"]: row.get("selected") or {} for row in media.get("clips", [])}
    segments = review_evidence.segment_texts(episode_dir)
    report = {"policy": review_contracts.POLICY, "episode": episode_dir.name, "video_name": video_name, "clips": {}, "flags": [], "feedback": {}}
    suffix = "" if video_name == "clip.mp4" else "." + video_name.replace(".mp4", "")
    review_path = episode_dir / f"episode_review{suffix}.json"
    try:
        previous = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
    except (OSError, ValueError):
        previous = {}
    # A verdict on the very same file stands: under the same policy, a clip whose take has not changed since the
    # last review keeps its verdict (with whatever the verification gate wrote on it), and only new takes are judged.
    # 雾月 2026-09-14: each repair round re-judged all 633 clips of a 60-episode batch for ~40 changed takes.
    # NOVEL_REVIEW_FRESH=1 judges every clip again (a card or prompt change the policy string does not carry).
    earlier = (previous.get("clips") or {}) if previous.get("policy") == review_contracts.POLICY else {}
    resume = os.environ.get("NOVEL_REVIEW_FRESH", "").strip() != "1"
    for clip in plan["clips"]:
        if clip["kind"] != "video":
            continue
        clip_id = clip["clip_id"]
        if video_name == "clip.mp4" and selected.get(clip_id, {}).get("video"):
            video = Path(selected[clip_id]["video"])
            hypothesis = selected[clip_id].get("hypothesis", "")
        else:
            attempts = sorted((episode_dir / "work" / "clips" / clip_id).glob("attempt_*"))
            video = next((a / video_name for a in attempts if (a / video_name).is_file()), None)
            if video is None:
                continue
            asr = video.parent / ("stale_asr.json" if "stale" in video_name else "asr.json")
            hypothesis = json.loads(asr.read_text(encoding="utf-8")).get("hypothesis", "") if asr.is_file() else ""
        old = earlier.get(clip_id) if resume else None
        take = review_storage.take_identity(video)
        try:
            # The very same file, not just the same path: split_long_stages renames clip directories, so after a
            # split the path names another clip's take - with its old mtime - and that clip's verdict landed on it.
            if (old and old.get("severity") != "review_error" and old.get("video") == str(video)
                    and take and old.get("take") == take):
                verdict = {key: value for key, value in old.items() if key not in ("video", "take")}
            else:
                verdict = review_judges.judge_clip(clip, video, bible, location_time, hypothesis, episode_dir / "work" / "review" / clip_id)
        except Exception as error:  # noqa: BLE001 - a judge failure is reported, never fatal
            model_client.log(f"episode {episode_dir.name} {clip_id}: review error {type(error).__name__}: {str(error)[:120]}")
            report["clips"][clip_id] = {"video": str(video), "take": take, "severity": "review_error", "error": f"{type(error).__name__}: {str(error)[:300]}"}
            continue
        report["clips"][clip_id] = {"video": str(video), "take": take, **verdict}
        if verdict.get("severity") == "fail":
            tier = review_policy.fix_tier(verdict, bible, rules)
            if (tier == "must_fix" and "scripted" not in verdict
                    and not (verdict.get("story_ok") is False and verdict.get("story_kind") in review_contracts.STORY_FATAL)):
                check = review_judges.script_check(clip, verdict, segments)
                if check is not None:
                    verdict["scripted"] = report["clips"][clip_id]["scripted"] = (
                        {"evidence": check["evidence"], "note": check["note"]} if check["scripted"] else False)
                    tier = review_policy.fix_tier(verdict, bible, rules)
            # Into the file as well as this run's copy: without it the review said only how many clips differ from the
            # setting, never how many it actually asks to redo (雾月: 3501 fails, 600 of them must_fix).
            verdict["tier"] = report["clips"][clip_id]["tier"] = tier
            if tier != "ignore":
                report["flags"].append(review_policy.flag_line(clip_id, verdict, tier))
            if tier == "must_fix":
                report["feedback"][clip_id] = review_policy.compose_feedback(verdict, clip, card_manifest)
        model_client.log(f"episode {episode_dir.name} {clip_id}: {verdict.get('severity')} people={verdict.get('visible_people')} identity={verdict.get('identity_ok')} loc={verdict.get('location_ok')}/{verdict.get('time_of_day_ok')} text={verdict.get('text_or_watermark')} defects={verdict.get('visual_defects')}" + (f" | {verdict.get('identity_issue') or verdict.get('defect_issue')}" if verdict.get("severity") != "pass" else ""))
    # Rounds in a row that left clips unjudged: the conductor queues such a review again, a few times.
    errors = sum(1 for c in report["clips"].values() if c.get("severity") == "review_error")
    report["error_rounds"] = int(previous.get("error_rounds", 0)) + 1 if errors else 0
    atomic_write_json(review_path, report)
    return report
