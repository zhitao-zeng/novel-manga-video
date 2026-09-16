"""Repair evidence and delayed publication, owned by the episode's current job.

History records facts from current takes, not inferred past attempts. A repaired
episode is staged until its current clips pass precise review and media checks.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import time

from novel_manga.util import read_json as read, atomic_write_json

ERROR_FIELDS = ("same_person_twice", "species_or_gender_wrong", "action_by_wrong_person", "actor_missing", "lead_face_swapped")
HISTORY_DIR = "repair_history"


def load(directory: Path) -> dict:
    return read(directory / HISTORY_DIR / "history.json", {"trials": [], "observations": {}})


def save(directory: Path, history: dict):
    atomic_write_json(directory / HISTORY_DIR / "history.json", history)


def copy_complete(source: Path, target: Path):
    partial = target.with_name(target.name + ".partial")
    shutil.copy2(source, partial)
    os.replace(partial, target)


def archived_take(directory: Path, cid: str, video: Path) -> dict | None:
    """Keep media and its request before the renderer renames/prunes an old take."""
    if not video.is_file():
        return None
    folder = directory / HISTORY_DIR / "takes" / cid / f"{video.parent.name}-{video.stat().st_mtime_ns}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, source in (("clip.mp4", video), ("request.json", video.parent / "request.json"),
                         ("asr.json", video.parent / "asr.json")):
        target = folder / name
        if source.is_file() and not target.exists():
            copy_complete(source, target)
    return {"video": str(folder / "clip.mp4"), "original_video": str(video)}


def add_observations(history: dict, review: dict, takes: dict) -> bool:
    changed = False
    for cid, current in takes.items():
        row = (review.get("clips") or {}).get(cid) or {}
        if row.get("video") != current.get("video") or row.get("take") != current.get("take") or not row.get("verify"):
            continue
        verify = row["verify"]
        observation = {"video": current["video"], "take": current["take"], "policy": review.get("policy"),
                       "verdict": verify.get("verdict"), "evidence": verify.get("evidence", row.get("story_issue", "")),
                       "instruction": verify.get("instruction", row.get("feedback", "")),
                       "errors": [key for key in ERROR_FIELDS if verify.get(key)],
                       **({"repair_advice": verify["repair_advice"]} if verify.get("repair_advice") else {})}
        rows = history.setdefault("observations", {}).setdefault(cid, [])
        old = next((r for r in rows if r["video"] == observation["video"] and r["take"] == observation["take"]), None)
        if old == observation:
            continue
        if old is not None:
            rows[rows.index(old)] = observation  # confirmation of the same take is not another failed attempt
        else:
            rows.append(observation)
        changed = True
    return changed


def begin_trial(directory: Path, clip_ids: set[str], method: str, *, after_plan: dict | None = None,
                after_notes: dict | None = None, changes: dict | None = None) -> int:
    from review_store_thin import current_takes
    from thin_profile import plan_fingerprint
    plan = read(directory / "clip_plan.json", {})
    review = read(directory / "episode_review.json", {})
    notes = read(directory / "review_feedback.json", {})
    media = read(directory / "thin_media_report.json", {})
    takes = current_takes(directory, plan, review)
    history = load(directory)
    add_observations(history, review, takes)
    before = {}
    selected = {c["clip_id"]: c.get("selected") for c in media.get("clips", [])}
    for clip in plan.get("clips", []):
        cid = clip["clip_id"]
        if cid not in clip_ids:
            continue
        current = takes.get(cid)
        archive = archived_take(directory, cid, Path(current["video"])) if current else None
        before[cid] = {"plan": copy.deepcopy(clip), "note": notes.get(cid, ""),
                       "review": copy.deepcopy((review.get("clips") or {}).get(cid)),
                       "selected": selected.get(cid), "archive": archive}
    trial_id = len(history["trials"]) + 1
    trial = {"id": trial_id, "at": time.strftime("%F %T"), "method": method, "clips": sorted(clip_ids),
             "before": before, "changes": changes or {},
             "expected_plan": plan_fingerprint(after_plan if after_plan is not None else plan),
             "expected_notes": after_notes if after_notes is not None else notes, "renders": []}
    history["trials"].append(trial)
    save(directory, history)
    return trial_id


def history_context(directory: Path, cid: str, *, limit: int = 1400) -> str:
    history = load(directory)
    observations = (history.get("observations") or {}).get(cid, [])[-2:]
    trials = [t for t in history.get("trials", []) if cid in t["clips"] and t.get("renders")][-2:]
    if not observations and not trials:
        return ""
    lines = ["已发生的修复记录（只用于拟修法，不能当作当前画面的事实）："]
    for row in observations:
        verdict = "obvious" if row.get("errors") else row["verdict"]
        lines.append(f"一次实际视频的结论：{verdict}；错误类别 {row['errors']}；画面证据：{row['evidence']}")
    for trial in trials:
        change = trial.get("changes", {}).get(cid) or trial.get("expected_notes", {}).get(cid, "")
        lines.append(f"已尝试 {trial['method']}：{json.dumps(change, ensure_ascii=False)[:450]}")
    return "\n".join(lines)[:limit]


def repeated_errors(directory: Path, cid: str) -> list[str]:
    rows = (load(directory).get("observations") or {}).get(cid, [])[-2:]
    if len(rows) != 2 or any(r.get("verdict") != "obvious" and not r.get("errors") for r in rows):
        return []
    return sorted(set(rows[0].get("errors", [])) & set(rows[1].get("errors", [])))


def record_render(directory: Path, report: dict):
    if not (directory / HISTORY_DIR / "history.json").is_file():
        return
    history = load(directory)
    if not history["trials"]:
        return
    from novel_manga.review.storage import take_identity
    changed = False
    for trial in history['trials']:
        if trial is not history['trials'][-1] and not trial.get('managed'):
            continue
        if trial['expected_plan'] != report.get('clip_plan_fingerprint') or trial['expected_notes'] != (report.get('review_feedback') or {}):
            continue
        clips = {}
        for c in report.get('clips',[]):
            if c['clip_id'] not in trial['clips']:
                continue
            generated = {}
            for attempt in [*c.get('attempts',[]),c.get('selected') or {}]:
                video = attempt.get('video')
                if attempt.get('generated_this_run') and video:
                    take = take_identity(Path(video))
                    if take:
                        generated[(video,tuple(take))] = {'video':video,'take':take}
            clips[c['clip_id']] = {'selected':c.get('selected'),'error':c.get('error'),
                                  'completed_generated_seconds':sum(float(a.get('duration') or 0) for a in c.get('attempts',[]) if a.get('generated_this_run')),
                                  'generated_takes':list(generated.values())}
        record = {'clips':clips,'candidate_video':(report.get('assembly') or {}).get('final_video')}
        if not trial['renders'] or trial['renders'][-1] != record:
            trial['renders'].append(record)
            changed = True
    if changed:
        save(directory,history)


def refresh_prepared_plan(directory: Path, before_plan: dict, after_plan: dict, notes: dict):
    """A deterministic reference cleanup still belongs to the pending repair."""
    from thin_profile import plan_fingerprint
    if not (directory/HISTORY_DIR/'history.json').is_file():
        return
    before,after=plan_fingerprint(before_plan),plan_fingerprint(after_plan)
    history=load(directory);changed=False
    for trial in history['trials']:
        if (trial.get('managed') and not trial.get('renders') and trial.get('expected_plan')==before
                and trial.get('expected_notes')==notes):
            trial['expected_plan']=after;changed=True
    if changed:
        save(directory,history)


def observe(directory: Path, review: dict, takes: dict):
    if not (directory / HISTORY_DIR / "history.json").is_file():
        return
    history = load(directory)
    if add_observations(history, review, takes):
        save(directory, history)


def accepted_clip_material(clip: dict) -> dict:
    return {**{k: clip.get(k) for k in ('clip_id','kind','prompt','prompt_h3','prompt_h3_skip','references',
                                    'request_seconds','lines','chat_lines','shot_indexes','shot_parts')},
            **({'repair_take':clip['repair_take']} if clip.get('repair_take') else {}),
            **({'dialogue_bindings':clip['dialogue_bindings']} if 'dialogue_bindings' in clip else {}),
            **({'crowd_roles':clip['crowd_roles']} if clip.get('crowd_roles') else {})}


def source_accepted_take(directory: Path, clip: dict, note: str) -> Path | None:
    """A current video explicitly reviewed against corrected source intent.

The original generation request is kept as history. Any further plan, note,
reference-image or video change invalidates this acceptance.
"""
    record = read(directory / 'source_acceptances.json', {}).get(clip['clip_id'])
    if not record or record.get('clip') != accepted_clip_material(clip) or record.get('note','') != note:
        return None
    from novel_manga.review.storage import take_identity
    from novel_manga.media.common import reference_digests
    video = Path(record['video'])
    references = tuple(directory.parent / r['path'] for r in clip.get('references', []) if r.get('role') != 'voice')
    if (take_identity(video) != record.get('take') or not all(p.is_file() for p in references)
            or reference_digests(references) != record.get('reference_digests')):
        return None
    return video
