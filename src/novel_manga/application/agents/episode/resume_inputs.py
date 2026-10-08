"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import os
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json

from .session import SESSION_FILE


def pending_input_continuations(ctx):
    """Continue already-approved interrupted work once, before opening another CLI."""
    from .input_repair import effects, repair_status
    from .input_records import latest_input_records, source_review_recoverable
    available = repair_status(ctx)
    script = read_json(ctx.episode_dir/'chapter_script.json', {})
    shots = {s.get('index', i): s for i, s in enumerate(script.get('shots', []), 1)}
    decisions, seen = [], []
    first_pending = None
    if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
        from novel_manga.application.preparation.store import unfilmed_video_clips
        first_pending = unfilmed_video_clips(ctx.episode_dir)
    for path, record in latest_input_records(ctx.episode_dir).values():
        if first_pending is not None and record.get('clip_id') not in first_pending:
            continue
        resume = available.get(record.get('clip_id'), {}).get('resume_action')
        timing_fix = (ctx.input_mode == 'source' and record.get('step') == 'compile'
                      and 'input repair changed protected timing or shot addresses' in str(record.get('error') or ''))
        if not resume:
            continue
        reserved_source = record.get('step') == 'source' and bool(record.get('previous_source_review'))
        if not (source_review_recoverable(record) or reserved_source) and (
                (record.get('source') or {}).get('consistent') is not True or not (
                    record.get('step') == 'assets'
                    or str(record.get('step') or '').startswith(('translate:', 'check:')) or timing_fix)):
            continue
        decision = copy.deepcopy(record.get('decision') or {})
        patches = [{'index': p['index'], **{k:v for k,v in p.items() if k != 'index'
                    and shots.get(p['index'], {}).get(k) != v}} for p in decision.get('shots') or []]
        decision['shots'] = [p for p in patches if len(p) > 1]
        change = effects(decision)
        if change in seen:
            continue
        decisions.append(resume)
        seen.append(change)
    if decisions:
        atomic_write_json(ctx.work_dir/'output/resume-inputs.json', {'decisions': decisions})
    return bool(decisions)


def stale_request_blocks(ctx):
    from novel_manga.application.preparation.request_check import POLICY
    report = read_json(ctx.episode_dir/'thin_media_report.json', {})
    for cid, problems in (report.get('blocked_clips') or {}).items():
        if not any(str(p).startswith('request:') for p in problems):
            continue
        saved = read_json(ctx.episode_dir/f'work/request_checks/{cid}.json', {})
        if ((saved.get('inputs') or {}).get('policy') not in (None, POLICY)
                and (saved.get('answer') or {}).get('consistent') is False):
            return True
    return False


def pending_structured_repairs(ctx):
    """Reuse a saved proposal rejected solely by the former missing-field bug."""
    from .requests import compose_repair
    from .contracts import validate_patch
    rounds = read_json(ctx.run_dir/'tool-repair-decisions.json', [])
    if not rounds:
        return False
    rejected = {cid for cid, reason in rounds[-1].get('blocked', {}).items() if reason == "'prompt_h3'"}
    if not rejected:
        return False
    source = ctx.work_dir/'output/repair.json'
    draft = read_json(source, {})
    if not isinstance(draft, dict) or not isinstance(draft.get('decisions'), list):
        return False  # Let the Agent correct its saved draft; never replay malformed output.
    clips = {c['clip_id']: c for c in read_json(ctx.episode_dir/'clip_plan.json', {}).get('clips', [])}
    evidence = {r['clip']['clip_id']: r for r in read_json(ctx.work_dir/'input/latest-review.json', {}).get('targets', [])}
    media = read_json(ctx.episode_dir/'thin_media_report.json', {})
    takes = {r['clip_id']: (r.get('selected') or {}).get('video') for r in media.get('clips', [])}
    selected = []
    for row in draft['decisions']:
        cid = row.get('clip_id')
        old = evidence.get(cid, {})
        if (cid not in rejected or row.get('action') != 'edit' or not isinstance(row.get('h3'), dict)
                or cid not in clips or old.get('clip') != clips[cid] or old.get('input_block')):
            continue
        video = Path(takes[cid]) if takes.get(cid) else None
        if video and video.exists() and video.stat().st_mtime_ns > source.stat().st_mtime_ns:
            continue  # the saved proposal predates the current take
        try:
            validate_patch(clips[cid], compose_repair(clips[cid], row))
        except (ValueError, KeyError, TypeError):
            continue
        selected.append(row)
    if selected:
        atomic_write_json(ctx.work_dir/'output/resume-structured-repairs.json', {'decisions': selected})
    return bool(selected)


def fill_missing_requests(ctx, *, tools, timeout=300):
    """Prepare the normal model draft before asking an Agent to handle exceptions."""
    from .local_preparation import active
    if active(ctx):
        return None  # The native local preparation gate runs before video.
    if ctx.input_mode == 'source':
        if (ctx.run_dir/'prepared.json').is_file():
            return None  # current adopted requests, not a stale initial draft, own continuations
        if read_json(ctx.work_dir/'input/manifest.json', {}).get('preparation_exception'):
            return None  # The failed normal candidate already belongs to the exception Agent.
        if not read_json(ctx.work_dir/'input/manifest.json', {}).get('ready'):
            return None
        from .authoring import propose
        result = propose(ctx, timeout=timeout)
        if (ctx.work_dir/'output/episode.json').is_file():
            checked = tools.dispatch('check', {})
        else:
            checked = {'ok': False, 'valid': False, 'error': result.get('error', 'source draft unavailable')}
        atomic_write_json(ctx.work_dir/'input/source-draft-check.json', checked)
        return checked
    if ctx.input_mode != 'script' or (ctx.work_dir / SESSION_FILE).exists():
        return
    from .contracts import validate_existing_request
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.story.h3 import positive_direction_issues
    path = ctx.work_dir / 'output/requests.json'
    draft = read_json(path, {})
    info_path = ctx.work_dir / 'input/request-draft.json'
    info = read_json(info_path, {})
    if not info or not isinstance(draft.get('prompts'), dict):
        return
    plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    targets = [c for c in plan.get('clips', []) if c.get('kind') == 'video'
               and not draft['prompts'].get(c['clip_id']) and not c.get('prompt_h3')
               and c['clip_id'] not in info.get('draft_attempted', [])]
    if not targets:
        return

    def one(clip):
        candidate, problems = copy.deepcopy(clip), []
        try:
            convert(candidate, tries=1, episode_dir=ctx.episode_dir, problems=problems)
            if not candidate.get('prompt_h3'):
                raise ValueError('; '.join(problems) or 'initial English draft incomplete; Agent must resolve it')
            validate_existing_request(clip, candidate['prompt_h3'])
            return candidate['prompt_h3'], None
        except (ValueError, KeyError, OSError) as error:
            return None, str(error)

    info.setdefault('draft_attempted', []).extend(c['clip_id'] for c in targets)
    atomic_write_json(info_path, info)
    with ThreadPoolExecutor(max_workers=min(2, len(targets))) as pool:
        jobs = {pool.submit(one, clip): clip['clip_id'] for clip in targets}
        for future in as_completed(jobs):
            cid = jobs[future]
            prompt, error = future.result()
            current = read_json(path)
            # A user-supplied or Agent-authored request always keeps its ownership.
            if current['prompts'].get(cid):
                continue
            if prompt:
                current['prompts'][cid] = prompt
                atomic_write_json(path, current)
                info['reusable'].append(cid)
                info['needs_work'].pop(cid, None)
                if advice := positive_direction_issues(prompt):
                    info['style_warnings'][cid] = advice
            else:
                info['needs_work'][cid] = error
            atomic_write_json(info_path, info)
    manifest_path = ctx.work_dir / 'input/manifest.json'
    manifest = read_json(manifest_path)
    manifest['request_draft'] = info
    atomic_write_json(manifest_path, manifest)
