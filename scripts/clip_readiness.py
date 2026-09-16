"""Cheap, deterministic admission checks before spending a video-generation slot.

This checks the recorded request, not artistic quality. Missing cards are checked
after the normal asset builder has had a chance to create them. Blocked requests
stay visible in render_readiness.json and become eligible again when their inputs change.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path

from novel_manga.util import atomic_write_json

REPORT = "render_readiness.json"
POLICY = "clip-readiness-v1"


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def collapsed_source_addresses(plan: dict, script: dict) -> dict[str, list[int]]:
    """Old origin-index maps collapsed already split script stages to the last one."""
    groups = defaultdict(list)
    for index, shot in enumerate(script.get('shots', []), 1):
        groups[shot.get('origin_index', shot.get('index', index))].append(shot.get('index', index))
    repaired = {}
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        occurrences = [(c, i, value) for c in plan.get('clips', []) for i,value in enumerate(c.get('shot_indexes', [])) if value in indexes]
        # Explicit part records can describe intentional splits. This repair is
        # limited to the legacy collapse where one existing source part vanished.
        if (len(occurrences) != len(indexes) or any(c.get('shot_parts') or c.get('kind') != 'video' for c,_,_ in occurrences)
                or sorted(value for _,_,value in occurrences) == sorted(indexes)):
            continue
        for (clip, position, old), new in zip(occurrences, indexes):
            if old != new:
                repaired.setdefault(clip['clip_id'], list(clip['shot_indexes']))[position] = new
    return repaired


def location_issues(clip: dict, shots: dict) -> list[str]:
    locations = {shots[i].get('location') for i in clip.get('shot_indexes', []) if i in shots}
    locations.discard(None)
    locations.discard('')
    if len(locations) > 1:
        return ['location: source stages cross locations; recut required']
    bound_locations = {r.get('name') for r in clip.get('references', []) if r.get('role') == 'location'}
    # Older location rebinding updated the reference and request but retained
    # the old display label. That alone must not cause a new video generation.
    if locations and clip.get('location') not in locations and bound_locations != locations:
        return ['location: clip location differs from its source stages']
    return []


def plan_issues(plan: dict, script: dict | None = None) -> dict[str, list[str]]:
    issues = defaultdict(list)
    sources = {s.get("index", i): s for i, s in enumerate(script.get("shots", []), 1)} if script is not None else None
    by_source = defaultdict(list)
    cap = float((plan.get("limits") or {}).get("max_clip_seconds") or 0)
    for clip in plan.get("clips", []):
        if clip.get("kind") != "video":
            continue
        cid = clip["clip_id"]
        seconds = float(clip.get("request_seconds") or 0)
        estimate = float(clip.get("seconds_estimate") or 0)
        if not math.isfinite(seconds) or seconds <= 0 or (cap and seconds > cap):
            issues[cid].append(f"duration: request {seconds:g}s outside the recorded clip limit {cap:g}s")
        elif estimate > seconds + 1e-6:
            issues[cid].append(f"duration: planned {estimate:g}s exceeds request {seconds:g}s")
        indexes = clip.get("shot_indexes") or []
        if sources is not None:
            if set(indexes) - sources.keys():
                issues[cid].append(f"source: missing stages {sorted(set(indexes) - sources.keys())}")
            problems = location_issues(clip, sources)
            if problems:
                issues[cid].extend(problems)
        parts = clip.get("shot_parts") or []
        if parts and [p.get("index") for p in parts] != indexes:
            issues[cid].append("ranges: shot_parts do not match shot_indexes")
        if not parts and len(indexes) != len(set(indexes)):
            issues[cid].append("ranges: legacy repeated stages have no recovered dialogue ranges")
        for index in set(indexes):
            spans = [p.get("part") for p in parts if p.get("index") == index]
            by_source[index].append((cid, spans))
        for part in parts:
            pair = part.get("part")
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(n, int) for n in pair) or not 1 <= pair[0] <= pair[1]:
                issues[cid].append("ranges: invalid part number or part count")
        for ref in clip.get("references", []):
            if ref.get("role") in {"character", "location"} and not ref.get("path"):
                issues[cid].append("reference: required image has no path")
    for source, owners in by_source.items():
        # Mixed legacy/explicit records cannot prove a gap. Their duration and
        # local bookkeeping are still checked above; do not guess an old cut.
        if not all(spans for _, spans in owners):
            continue
        pairs = [p for _, spans in owners for p in spans]
        if not all(isinstance(p, list) and len(p) == 2 and all(isinstance(n, int) for n in p) for p in pairs):
            continue
        totals = {p[1] for p in pairs}
        if len(totals) != 1 or sorted(p[0] for p in pairs) != list(range(1, pairs[0][1] + 1)):
            for cid, _ in owners:
                issues[cid].append(f"ranges: stage {source} has missing, repeated or inconsistent parts")
    return dict(issues)


def reference_issues(clip: dict, novel_dir: Path) -> list[str]:
    reasons = [f"asset: missing required image {ref.get('path') or '(no path)'}"
            for ref in clip.get("references", []) if ref.get("role") in {"character", "location"}
            and (not ref.get("path") or not (novel_dir / ref["path"]).is_file())]
    types = read(novel_dir / 'entity/types.json', {})
    for ref in clip.get('references', []):
        if ref.get('role') not in {'character', 'location'}:
            continue
        if ref['role'] == 'character' and types.get(ref.get('name'), {}).get('kind') == 'object':
            reasons.append(f"entity: object {ref['name']} is bound as a character")
        # Old card reviews compare against design data (including guessed sex,
        # clothing or day/night). They are diagnostics, not source-confirmed
        # identity blockers. Explicit book type corrections above are binding.
    return reasons


def may_reuse_duration_cache(directory: Path, cid: str, reasons: list[str]) -> bool:
    """Only a hint for the batch driver; the renderer verifies the exact request.

An estimate above the budget forbids a new request, but a matching video that
already passed the speech gate does not need generating again.
"""
    if not reasons or not all(reason.startswith("duration:") for reason in reasons):
        return False
    return any(read(path, {}).get("passed") and path.with_name("clip.mp4").is_file()
               and path.with_name("request.json").is_file()
               for path in (directory / "work/clips" / cid).glob("attempt_*/asr.json"))


def inspect_episode(directory: Path, *, assets: bool = False) -> tuple[dict, dict[str, list[str]]]:
    plan = read(directory / "clip_plan.json", {})
    blocked = plan_issues(plan, read(directory / "chapter_script.json"))
    if assets:
        for clip in plan.get("clips", []):
            if clip.get("kind") == "video" and clip["clip_id"] not in blocked:
                reasons = reference_issues(clip, directory.parent)
                if reasons:
                    blocked[clip["clip_id"]] = reasons
    return plan, blocked


def input_state(directory: Path, plan: dict) -> dict:
    paths = {"plan": directory / "clip_plan.json", "script": directory / "chapter_script.json"}
    paths.update({ref["path"]: directory.parent / ref["path"] for clip in plan.get("clips", [])
                  for ref in clip.get("references", []) if ref.get("role") in {"character", "location"} and ref.get("path")})
    result = {}
    for key, path in paths.items():
        stat = path.stat() if path.is_file() else None
        result[key] = [stat.st_mtime_ns, stat.st_size] if stat else None
    # Another chapter judging an unrelated card must not reopen this blocker.
    result['asset_admission'] = {c['clip_id']: [r for r in reference_issues(c, directory.parent) if r.startswith('entity:')]
                                 for c in plan.get('clips', [])}
    return result


def save_check(directory: Path, plan: dict, blocked: dict) -> None:
    atomic_write_json(directory / REPORT, {
        "policy": POLICY, "episode": directory.name, "inputs": input_state(directory, plan), "blocked_clips": blocked,
        "next_action": {cid: "restore_asset" if all(r.startswith("asset:") for r in reasons) else "repair_plan"
                        for cid, reasons in blocked.items()},
    })


def current_blocks(directory: Path) -> dict:
    record = read(directory / REPORT, {})
    if record.get("policy") != POLICY or not record.get("blocked_clips"):
        return {}
    plan = read(directory / "clip_plan.json", {})
    if record.get('inputs') != input_state(directory,plan):
        return {}
    from h3_request_checks import request_issues
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    return {cid:reasons for cid,reasons in record['blocked_clips'].items()
            if not all(r.startswith('request:') for r in reasons) or request_issues(clips.get(cid,{}))}
