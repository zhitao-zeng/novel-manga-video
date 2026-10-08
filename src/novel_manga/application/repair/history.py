"""Repair evidence and delayed publication, owned by the episode's current job.

History records facts from current takes, not inferred past attempts. A repaired
episode is staged until its current clips pass precise review and media checks.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import time

from novel_manga.util import read_json as read, atomic_write_json

ERROR_FIELDS = ("same_person_twice", "species_or_gender_wrong", "action_by_wrong_person", "actor_missing", "lead_face_swapped")
HISTORY_DIR = "repair_history"
GENERATION_FIELD = "managed_generation"
MAX_GENERATED_TAKES = 3


def generation_limit(directory: Path, cid: str, *, grants: dict | None = None) -> int:
    """Explicit clip-level grants after a reviewed change; history is retained."""
    grants = read(directory/'repair_budget_grants.json', {}) if grants is None else grants
    grant = grants.get(cid, {})
    limit = int(grant.get('limit', MAX_GENERATED_TAKES)) if grant.get('reason') else MAX_GENERATED_TAKES
    common = grants.get('*', {})
    return min(limit, int(common['limit'])) if common.get('reason') and 'limit' in common else limit


def conditional_extra(directory: Path, cid: str, *, grants=None) -> dict:
    grants = read(directory/'repair_budget_grants.json', {}) if grants is None else grants
    extra = grants.get(cid, {}).get('conditional_extra') or {}
    return extra if extra.get('authorization') and extra.get('reason') else {}


def preparation_limit(directory: Path, cid: str, *, grants=None) -> int:
    """Preparation may use a pending approval; actual generation still may not."""
    grants = read(directory/'repair_budget_grants.json', {}) if grants is None else grants
    return max(generation_limit(directory, cid, grants=grants),
               int(conditional_extra(directory, cid, grants=grants).get('limit', 0)))


def grant_conditional_extra(directory: Path, cid: str, *, authorization: str, reason: str) -> dict:
    """Register one explicitly authorized recovery round; never replenish it on restart."""
    directory = Path(directory)
    if not authorization.strip() or not reason.strip():
        raise ValueError('conditional take requires an explicit authorization and reason')
    if cid not in {c['clip_id'] for c in read(directory/'clip_plan.json', {}).get('clips', [])}:
        raise ValueError(f'unknown episode clip: {cid}')
    with (directory/'repair_budget_grants.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory/'repair_budget_grants.json'
        grants = read(path, {})
        entry = grants.get(cid, {})
        current = conditional_extra(directory, cid, grants=grants)
        prior = entry.get('previous_conditional_extras', [])
        for recorded in [current, *prior]:
            if recorded.get('authorization') == authorization:
                return copy.deepcopy(recorded)
        count = generation_counts(directory).get(cid, 0)
        available = preparation_limit(directory, cid, grants=grants)
        if count < available:
            return copy.deepcopy(current or {'limit': available, 'reason': 'existing unspent allowance'})
        entry = grants.setdefault(cid, {})
        if current:
            entry.setdefault('previous_conditional_extras', []).append(copy.deepcopy(current))
        used_inputs = sum(read(p, {}).get('status') != 'tool_error'
                          for p in (directory/'work/input_repairs').glob(f'{cid}-*.json'))
        extra = {'authorization': authorization, 'reason': reason, 'limit': count+1,
                 'baseline_count': count, 'baseline_limit': generation_limit(directory, cid, grants=grants),
                 'input_limit': max(2, used_inputs+1),
                 'recheck_limit': int(recheck_limit(directory, cid, grants=grants) or 0)+1,
                 'prepared': False, 'activated': False, 'authorized_at': time.strftime('%F %T')}
        entry['conditional_extra'] = extra
        atomic_write_json(path, grants)
        return copy.deepcopy(extra)


def input_limit(directory: Path, cid: str, *, default: int, grants=None, counts=None) -> int:
    grants = read(directory/'repair_budget_grants.json', {}) if grants is None else grants
    base = min(default, int(grants.get(cid, {}).get('input_limit', grants.get('*', {}).get('input_limit', default))))
    extra = conditional_extra(directory, cid, grants=grants)
    if extra:
        counts = generation_counts(directory) if counts is None else counts
        if counts.get(cid, 0) < int(extra['limit']):
            return max(base, int(extra.get('input_limit', 0)))
    return base


def recheck_limit(directory: Path, cid: str, *, grants=None):
    grants = read(directory/'repair_budget_grants.json', {}) if grants is None else grants
    base = grants.get(cid, {}).get('recheck_limit', grants.get('*', {}).get('recheck_limit'))
    extra = conditional_extra(directory, cid, grants=grants)
    return max(int(base or 0), int(extra['recheck_limit'])) if extra.get('recheck_limit') is not None else base


def mark_extra_prepared(directory: Path, cids):
    """Only the host calls this after accepting a concrete edit/input/retake."""
    if not cids:
        return
    path = directory/'repair_budget_grants.json'
    if not any(conditional_extra(directory, cid) for cid in cids):
        return
    with (directory/'repair_budget_grants.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        grants = read(path, {})
        changed = False
        for cid in cids:
            extra = conditional_extra(directory, cid, grants=grants)
            if extra and not extra.get('prepared'):
                extra.update(prepared=True, prepared_at=time.strftime('%F %T'))
                changed = True
        if changed:
            atomic_write_json(path, grants)


def extra_take_ready(directory: Path, cid: str) -> bool:
    extra = conditional_extra(directory, cid)
    return bool(extra.get('prepared') and generation_counts(directory).get(cid, 0) < int(extra['limit']))


def activate_extra_take(directory: Path, cid: str) -> bool:
    """Called after the normal final-request/reference preflight, immediately before sampling."""
    with (directory/'repair_budget_grants.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory/'repair_budget_grants.json'
        grants = read(path, {})
        extra = conditional_extra(directory, cid, grants=grants)
        if not extra.get('prepared') or generation_counts(directory).get(cid, 0) >= int(extra['limit']):
            return False
        if extra.get('activated'):
            return True
        limit = int(extra['limit'])
        common = grants.get('*', {})
        if common.get('reason') and int(common.get('limit', limit)) < limit:
            # The wildcard is a ceiling. Preserve each other clip's effective
            # limit before lifting it for this one reviewed authorization.
            clips = read(directory/'clip_plan.json', {}).get('clips', [])
            limits = {c['clip_id']: generation_limit(directory, c['clip_id'], grants=grants) for c in clips}
            for key, previous in limits.items():
                entry = grants.setdefault(key, {})
                entry.update(limit=previous, reason=entry.get('reason') or 'Preserve existing clip allowance')
            common.update(limit=limit, reason='Ceiling for specifically reviewed extra takes; other clip limits retained')
        grants.setdefault(cid, {}).update(limit=max(limit, generation_limit(directory, cid, grants=grants)),
                                         reason=extra['reason'])
        extra.update(activated=True, activated_at=time.strftime('%F %T'),
                     preflight='normal final H3 request and actual-reference checks passed')
        atomic_write_json(path, grants)
        return True


def generated_counts(record: dict) -> dict[str, int]:
    """Only actual integrated-route takes spend budget; rereading one take counts once."""
    by_clip = {}
    for trial in record.get('trials', []):
        if not trial.get('managed'):
            continue
        for render in trial.get('renders', []):
            for cid, row in render.get('clips', {}).items():
                for take in row.get('generated_takes', []):
                    by_clip.setdefault(cid, set()).add((take['video'], tuple(take['take'])))
    prior = (record.get('prior_generation_counts') or {}).get('clips') or {}
    return {cid: int(prior.get(cid, 0)) + len(by_clip.get(cid, set())) for cid in set(prior) | set(by_clip)}


def load(directory: Path) -> dict:
    return read(directory / HISTORY_DIR / "history.json", {"trials": [], "observations": {}})


def save(directory: Path, history: dict):
    atomic_write_json(directory / HISTORY_DIR / "history.json", history)


def generation_request(directory: Path, output: Path, request: dict, *, expected_plan: str,
                       expected_notes: dict, fresh_task: bool = False) -> dict:
    """Own a new submission, preserving the owner of a task being resumed."""
    previous = read(output.parent / "request.json", {})
    task = read(output.with_suffix(output.suffix + ".task.json"), {})
    metadata = {GENERATION_FIELD, "clip_id", "attempt", "workflow"}
    same = {k: v for k, v in previous.items() if k not in metadata} == {
        k: v for k, v in request.items() if k not in metadata}
    if not fresh_task and task.get("task_id") and same:
        # A legacy pending task predates ownership tracking. Finishing it is
        # cache recovery, not a new submission by the current managed trial.
        return {**request, **({GENERATION_FIELD: copy.deepcopy(previous[GENERATION_FIELD])}
                            if previous.get(GENERATION_FIELD) else {})}
    owner = next((trial for trial in reversed(load(directory).get("trials", []))
                  if trial.get("managed") and request["clip_id"] in trial.get("clips", [])
                  and trial.get("expected_plan") == expected_plan
                  and trial.get("expected_notes") == expected_notes), None)
    return {**request, **({GENERATION_FIELD: {"trial": owner["id"], "clip_id": request["clip_id"]}} if owner else {})}


def _owned_trial(record: dict, cid: str, owner: dict) -> int | None:
    cid = owner.get("clip_id", cid)
    return next((trial["id"] for trial in record.get("trials", [])
                 if trial.get("managed") and trial.get("id") == owner.get("trial")
                 and cid in trial.get("clips", [])), None)


def complete_generation(directory: Path, cid: str, video: Path, *, duration: float | None = None) -> dict | None:
    """Save the actual take before ASR; this does not write repair history."""
    from novel_manga.review.storage import take_identity
    request_path = video.parent / "request.json"
    request = read(request_path, {})
    owner = request.get(GENERATION_FIELD) or {}
    if _owned_trial(load(directory), cid, owner) is None:
        return None
    current = take_identity(video) if video.is_file() else None
    if not current or current[1] <= 0:
        return None
    task_id = read(video.with_suffix(video.suffix + ".task.json"), {}).get("task_id")
    if owner.get("take") and current != owner.get("file_take", owner["take"]):
        # Re-downloading the same saved task is the same generated take. A
        # manual replacement without that task evidence is not a new receipt.
        if not task_id or task_id != owner.get("task_id"):
            return None
    completed = {**owner, "video": owner.get("video", str(video)),
                 "take": owner.get("take", current), "file_take": current,
                 **({"task_id": task_id} if task_id else {}),
                 **({"duration": duration} if duration is not None else {})}
    if completed != owner:
        atomic_write_json(request_path, {**request, GENERATION_FIELD: completed})
    return completed


def owned_generations(directory: Path, record: dict | None = None) -> dict[int, dict[str, list[dict]]]:
    """Read confirmed owned material, including completion interrupted before ASR."""
    from novel_manga.review.storage import take_identity
    record = load(directory) if record is None else record
    found = {}
    paths = [(path, False) for path in (directory / "work" / "clips").glob("*/attempt_*/request.json")]
    paths += [(path, True) for path in (directory / HISTORY_DIR / "takes").glob("*/*/request.json")]
    for request_path, archived in paths:
        request = read(request_path, {})
        owner = request.get(GENERATION_FIELD) or {}
        cid = owner.get("clip_id", request.get("clip_id"))
        trial = _owned_trial(record, cid, owner)
        if trial is None:
            continue
        video = request_path.with_name("clip.mp4")
        current = take_identity(video) if video.is_file() else None
        if not current or current[1] <= 0:
            continue
        if owner.get("take"):
            physical = owner.get("file_take", owner["take"])
            # copy_complete preserves size/mtime, while its inode changes.
            if (current[1:] != physical[1:] if archived else current != physical):
                continue
        elif archived:
            continue
        fact = {"video": owner.get("video", str(video)), "take": owner.get("take", current),
                **({"duration": owner["duration"]} if "duration" in owner else {})}
        key = (fact["video"], tuple(fact["take"]))
        found.setdefault(trial, {}).setdefault(cid, {})[key] = fact
    return {trial: {cid: list(takes.values()) for cid, takes in clips.items()} for trial, clips in found.items()}


def generation_counts(directory: Path, record: dict | None = None) -> dict[str, int]:
    """Budget also includes owned completions awaiting the report/history write."""
    record = load(directory) if record is None else record
    counts = generated_counts(record)
    recorded = {}
    for trial in record.get("trials", []):
        if trial.get("managed"):
            for render in trial.get("renders", []):
                for cid, row in render.get("clips", {}).items():
                    recorded.setdefault(cid, set()).update((take["video"], tuple(take["take"]))
                                                          for take in row.get("generated_takes", []))
    pending = {}
    for clips in owned_generations(directory, record).values():
        for cid, takes in clips.items():
            pending.setdefault(cid, set()).update((take["video"], tuple(take["take"])) for take in takes)
    for cid, takes in pending.items():
        counts[cid] = counts.get(cid, 0) + len(takes - recorded.get(cid, set()))
    return counts


def copy_complete(source: Path, target: Path):
    partial = target.with_name(target.name + ".partial")
    shutil.copy2(source, partial)
    os.replace(partial, target)


def archived_take(directory: Path, cid: str, video: Path) -> dict | None:
    """Keep media and its request before the renderer renames/prunes an old take."""
    if not video.is_file():
        return None
    complete_generation(directory, cid, video)
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
                       "verdict": verify.get("verdict"), "evidence": verify.get("evidence") or row.get("story_issue") or row.get('identity_issue') or row.get('defect_issue', ''),
                       "instruction": verify.get("instruction", row.get("feedback", "")),
                       "errors": sorted(set([key for key in ERROR_FIELDS if verify.get(key)] + list(verify.get('error_kinds') or []))),
                       **({"repair_advice": verify["repair_advice"]} if verify.get("repair_advice") else {}),
                       **({"confirmed_issue": row["confirmed"]["issue"]} if row.get("confirmed") and row.get("tier") == "must_fix" else {})}
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


def adopt_reviewed_run(directory: Path, report: dict | None = None) -> dict:
    """Carry completed standalone reviewed renders into the existing repair budget once."""
    state = load(directory)
    if 'prior_generation_counts' in state:
        return state['prior_generation_counts']['clips']
    report = report if report is not None else read(directory / 'thin_media_report.json', {})
    review = report.get('quality_review') or {}
    first = review.get('first_pass_generated')
    if first is None:
        return {}
    counts = {cid: int(n) for cid, n in first.items() if int(n) > 0}
    if review.get('automatic_corrections'):
        for row in report.get('clips', []):
            n = sum(bool(t.get('generated_this_run')) for t in row.get('attempts', []))
            if n:
                counts[row['clip_id']] = counts.get(row['clip_id'], 0) + n
    state['prior_generation_counts'] = {'source': 'completed reviewed render', 'clips': counts}
    save(directory, state)
    return counts


def begin_trial(directory: Path, clip_ids: set[str], method: str, *, after_plan: dict | None = None,
                after_notes: dict | None = None, changes: dict | None = None) -> int:
    from novel_manga.application.review.store import current_takes
    from novel_manga.application.profiles import plan_fingerprint
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
    errors = sorted(set(rows[0].get("errors", [])) & set(rows[1].get("errors", [])))
    if rows[0].get("confirmed_issue") and rows[0]["confirmed_issue"] == rows[1].get("confirmed_issue"):
        errors.append("confirmed_picture")
    return errors


def record_render(directory: Path, report: dict):
    if not (directory / HISTORY_DIR / "history.json").is_file():
        return
    history = load(directory)
    if not history["trials"]:
        return
    from novel_manga.review.storage import take_identity
    owned = owned_generations(directory, history)
    changed = False
    for trial in history['trials']:
        if trial is not history['trials'][-1] and not trial.get('managed'):
            continue
        matches = (trial['expected_plan'] == report.get('clip_plan_fingerprint')
                   and trial['expected_notes'] == (report.get('review_feedback') or {}))
        completions = owned.get(trial['id'], {})
        if not matches and not completions:
            continue
        clips = {}
        for c in report.get('clips', []) if matches else []:
            if c['clip_id'] not in trial['clips']:
                continue
            generated = {}
            seconds = sum(float(a.get('duration') or 0) for a in c.get('attempts', [])
                          if a.get('generated_this_run') and (not a.get('video') or _owned_trial(
                              history, c['clip_id'], read(Path(a['video']).parent / "request.json", {}).get(GENERATION_FIELD) or {}) is None))
            for attempt in [*c.get('attempts',[]),c.get('selected') or {}]:
                video = attempt.get('video')
                if attempt.get('generated_this_run') and video:
                    owner = read(Path(video).parent / "request.json", {}).get(GENERATION_FIELD) or {}
                    if _owned_trial(history, c['clip_id'], owner) is not None:
                        continue  # attributed to its original trial below
                    take = take_identity(Path(video))
                    if take:
                        key = (video, tuple(take))
                        generated[key] = {'video':video,'take':take}
            clips[c['clip_id']] = {'selected':c.get('selected'),'error':c.get('error'),
                                  'completed_generated_seconds':seconds,
                                  'generated_takes':list(generated.values())}
        for cid, takes in completions.items():
            row = clips.setdefault(cid, {'selected': None, 'error': None,
                                         'completed_generated_seconds': 0, 'generated_takes': []})
            row['generated_takes'].extend({k: take[k] for k in ('video', 'take')} for take in takes)
            row['completed_generated_seconds'] += sum(float(take.get('duration') or 0) for take in takes)
        record = {'clips':clips,'candidate_video':(report.get('assembly') or {}).get('final_video') if matches else None}
        if not trial['renders'] or trial['renders'][-1] != record:
            trial['renders'].append(record)
            changed = True
    if changed:
        save(directory,history)


def refresh_prepared_plan(directory: Path, before_plan: dict, after_plan: dict, notes: dict, *, after_notes: dict | None = None):
    """A deterministic reference cleanup still belongs to the pending repair."""
    from novel_manga.application.profiles import plan_fingerprint
    if not (directory/HISTORY_DIR/'history.json').is_file():
        return
    before,after=plan_fingerprint(before_plan),plan_fingerprint(after_plan)
    history=load(directory);changed=False
    for trial in history['trials']:
        if (trial.get('managed') and not trial.get('renders') and trial.get('expected_plan')==before
                and trial.get('expected_notes')==notes):
            trial['expected_plan']=after
            if after_notes is not None:
                trial['expected_notes']=copy.deepcopy(after_notes)
            changed=True
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
