"""Cheap, deterministic admission checks before spending a video-generation slot.

This checks the recorded request, not artistic quality. Missing cards are checked
after the normal asset builder has had a chance to create them. Blocked requests
stay visible in render_readiness.json and become eligible again when their inputs change.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import re
from pathlib import Path

from novel_manga.util import atomic_write_json
from novel_manga.application.identity.phases import load_phases
from novel_manga.media.card_check import people_found

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
            if ref.get("role") in {"character", "location", "prop"} and not ref.get("path"):
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
            for ref in clip.get("references", []) if ref.get("role") in {"character", "location", "prop"}
            and (not ref.get("path") or not (novel_dir / ref["path"]).is_file())]
    types = read(novel_dir / 'entity/types.json', {})
    for ref in clip.get('references', []):
        if ref.get('role') not in {'character', 'location', 'prop'}:
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
        for cid, reasons in render_risk_report(directory, plan).items():
            blocked.setdefault(cid, []).extend(reasons)
    return plan, blocked


def input_state(directory: Path, plan: dict) -> dict:
    paths = {"plan": directory / "clip_plan.json", "script": directory / "chapter_script.json"}
    paths.update({ref["path"]: directory.parent / ref["path"] for clip in plan.get("clips", [])
                  for ref in clip.get("references", []) if ref.get("role") in {"character", "location", "prop"} and ref.get("path")})
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
        "next_action": {cid: "restore_asset" if all(r.startswith("asset:") for r in reasons)
                        else "fix_asset_or_accept_risk" if all(r.startswith("risk:") for r in reasons) else "repair_plan"
                        for cid, reasons in blocked.items()},
    })


def current_blocks(directory: Path) -> dict:
    record = read(directory / REPORT, {})
    if record.get("policy") != POLICY or not record.get("blocked_clips"):
        return {}
    plan = read(directory / "clip_plan.json", {})
    if record.get('inputs') != input_state(directory,plan):
        return {}
    from novel_manga.story.h3 import request_issues
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    return {cid:reasons for cid,reasons in record['blocked_clips'].items()
            if not all(r.startswith('request:') for r in reasons) or request_issues(clips.get(cid,{}))}


# ---- pre-render risks: what the request itself shows about how the video will go wrong ----
# Each rule comes from a defect seen in rendered video (ch12 part one, 2026-09-25): a suit rendered apart
# from its wearer, a visor that opened and closed between clips, lips moving during an inner voice, exhaust
# with no flight, long silence.  One is a blocker - the wearer's card shows other clothes, which no sentence
# overrode in three clips of twenty - the rest are reported.  "Nothing was checked before rendering" was the
# failure; these read the request, not the art.
RISK_REPORT = "pre_render_check.json"
ACCEPTED = "render_risks_accepted.json"
_CN = re.compile(r"[\u4e00-\u9fff]")
_STAGE = re.compile(r"【阶段[^】]*】.*?(?=【阶段|画面呈现|$)", re.S)
_WEARS = re.compile(r"穿戴状态：([^；。]*)")
_VISOR = re.compile(r"面罩|面甲|头盔")
_FLIGHT = re.compile(r"飞|落地|降落|悬停|起飞|召唤|冲出|冲入|点火|升空")
_FIRE = re.compile(r"喷口|喷气|火光|火焰|喷射")
_SECOND = re.compile(r"(另一|第二|又一)(台|套|具|件)")
_TRANSIENT = re.compile(r"张嘴|张口|准备说话|正要|正欲|抬手|伸手|转身|起身|迈步")
_BACK = re.compile(r"(back to the camera|from behind(?!\s+(?:a|an|the|his|her|their|its|some)\b)|in the background|seen from the back|rear view)", re.I)


def _stages(clip: dict) -> list[str]:
    return _STAGE.findall(str(clip.get("prompt") or ""))


def _worn(stage: str) -> list[tuple[str, str]]:
    """(wearer, prop) pairs a compiled stage states: 穿戴状态：托尼·斯塔克穿着马克2机甲；席勒穿着……；"""
    return [(wearer.strip(), prop.strip()) for region in _WEARS.findall(stage)
            for wearer, prop in re.findall(r"([^；;：。]+?)穿着([^；;。]+)", region)]


def render_risks(clip: dict, novel_dir: Path, accepted: dict | None = None) -> tuple[list[str], list[str]]:
    """(blocking reasons, reported risks) for one clip, read from its request - never from the video."""
    accepted = accepted or {}
    # A voice reference shares its speaker's name; keyed by name it hid the card (every clip where
    # 托尼 also had a voice sample went unchecked).
    refs = {ref.get("name"): ref for ref in clip.get("references") or [] if ref.get("role") != "voice"}
    stages = _stages(clip)
    block, report = [], []
    worn = {pair for stage in stages for pair in _worn(stage)}
    # What each phase card was drawn wearing: the card the check asks for is one of these.
    drawn_wearing = {str(phase.get("asset_id")): str(phase.get("wears"))
                     for phases in load_phases(novel_dir).values() for phase in phases
                     if phase.get("asset_id") and phase.get("wears")}
    for wearer, prop in sorted(worn):
        ref = refs.get(wearer)
        if ref and ref.get("role") == "character" and drawn_wearing.get(str(ref.get("asset_id") or "")) != prop \
                and wearer not in (accepted.get("wearer_card") or []):
            block.append(f"risk: {wearer}的参考图不是穿着{prop}的样子，只靠一句话绑定（成片里三段人甲分离）；"
                         f"给{wearer}画一张穿着{prop}的阶段卡（series_assets/phases.json 的 wears），"
                         f"或在 {ACCEPTED} 里接受这个风险")
        if _VISOR.search(prop + str((refs.get(prop) or {}).get("name") or "")) or re.search(r"机甲|装甲|战甲|盔甲", prop):
            bare = [i for i, stage in enumerate(stages, 1) if f"穿着{prop}" in stage and not _VISOR.search(stage)]
            if bare:
                report.append(f"risk: 阶段{bare}穿着{prop}却没写面罩状态（成片里面罩开合乱跳）")
    for ref in clip.get("references") or []:
        path = str(ref.get("path") or "")
        found = people_found(novel_dir / path) if ref.get("role") == "location" and path else 0
        if found and path not in (accepted.get("card_people") or []):
            block.append(f"risk: 场景卡 {path} 里有{found}个人（H3 会把他们演成角色）；"
                         f"重画这张卡，或在 {ACCEPTED} 的 card_people 里接受这个风险")
    inner = [line for line in clip.get("lines") or [] if line.get("inner_monologue")
             and (refs.get(line.get("speaker_name")) or {}).get("role") == "character"
             and f"看不到{line.get('speaker_name')}的嘴" not in str(clip.get("prompt") or "")]
    if inner and clip.get('audio_delivery') != 'postmix':
        report.append(f"risk: {len(inner)}句心声，说话人在画面里（H3 心声时嘴会动）")
    for i, stage in enumerate(stages, 1):
        light = re.search(r"光源[：:]([^。]*)", stage)
        event = re.search(r"主要事件：([^。]*)", stage)
        if light and _FIRE.search(light.group(1)) and not (event and _FLIGHT.search(event.group(1))):
            report.append(f"risk: 阶段{i}光源有喷气/火光，但这一镜没有飞行")
    if _SECOND.search(str(clip.get("prompt") or "")):
        report.append("risk: 同一件道具在这一段出现第二份（易与穿戴者身上那一份混淆）")
    for stage in stages:
        start = re.search(r"(开始时|本镜起点)：([^。]*)", stage)
        if start and "结束时：" + start.group(2) in stage and _TRANSIENT.search(start.group(2)):
            report.append(f"risk: 切分段整段沿用开场的一瞬间动作：{start.group(2)[:30]}")
    seconds = float(clip.get("request_seconds") or 0) or 1.0
    speech = sum(len(_CN.findall(str(line.get("text") or ""))) / 4.0 + 0.5 for line in clip.get("lines") or []
                 if line.get("delivery_mode") in {"visible_dialogue", "offscreen_dialogue"})
    if 1 - speech / seconds > 0.45:
        report.append(f"risk: 台词约{speech:.1f}秒/{seconds:g}秒，预计静音{1 - speech / seconds:.0%}（整集门槛 35%）")
    english = str(clip.get("prompt_h3") or "")
    if english:
        body = english.split("detailed_description:", 1)[-1].split("overall_soundscape:", 1)[0]
        for shot in re.split(r"(?=\[Shot \d+\])", body):
            for n in set(re.findall(r"<Subject (\d+)> \(S\d+\) says(?! in an off-screen)", shot)):
                # The place words have to be about the speaker: a few words after its tag, in the same
                # clause.  Anywhere later in the sentence also caught "looking at <Subject 2>, who is in
                # the background" and "a door in the background" (agent ch12 clips 47 and 50).
                if re.search(rf"<Subject {n}>(?:,\s*who)?(?:'s\s+\w+)?\s+(?:[\w-]+\s+){{0,4}}?{_BACK.pattern}",
                             shot, re.I):
                    report.append(f"risk: {shot[:8]} Subject {n} 开口，却被写在后景/背对")
        outside = _CN.findall(re.sub(r"<d>.*?</d>", "", english, flags=re.S))
        if outside:
            report.append(f"risk: {len(outside)}个汉字在台词标签外（H3 会念出来）")
    return block, report


def render_risk_report(directory: Path, plan: dict) -> dict[str, list[str]]:
    """Write every clip's risks to pre_render_check.json and return only what blocks generation."""
    novel_dir = directory.parent
    accepted = read(novel_dir / ACCEPTED, {})
    rows, blocking = {}, {}
    for clip in plan.get("clips", []):
        if clip.get("kind") != "video":
            continue
        block, report = render_risks(clip, novel_dir, accepted)
        rows[clip["clip_id"]] = {"block": block, "report": report}
        if block:
            blocking[clip["clip_id"]] = block
    atomic_write_json(directory / RISK_REPORT, {"episode": directory.name, "accepted": accepted, "clips": rows})
    return blocking
