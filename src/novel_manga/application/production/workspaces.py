"""Route standard batch stages to existing episode books without moving their media."""
from __future__ import annotations

import copy
import os
import time
from contextlib import contextmanager
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.preparation.store import unfilmed_video_clips
from .common import pid_alive

PREPARATION_POLICY = 'clip-preflight-v1'


def stamp(row):
    directory = Path(row['episode_dir'])
    plan = read_json(directory/'clip_plan.json', {})
    paths = {directory/name for name in ('clip_plan.json', 'chapter_script.json', 'review_feedback.json',
                                         'segments.json','identity_context.json')}
    if (directory/'render_risks_accepted.json').is_file():
        paths.add(directory/'render_risks_accepted.json')
    paths.update(Path(row['novel_dir'])/name for name in ('story_bible.json','profile.json','series_assets/phases.json'))
    for clip in plan.get('clips', []):
        for ref in clip.get('references', []):
            if ref.get('path'):
                paths.update(Path(row[key])/ref['path'] for key in ('novel_dir', 'source_novel_dir'))
    return {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in sorted(paths)}


def busy(row):
    try: pid=int((Path(row['source_episode_dir'])/'.render.lock').read_text().strip() or 0)
    except (OSError,ValueError): return False
    return bool(pid and pid_alive(pid))


def current(row):
    saved = read_json(Path(row['episode_dir'])/'batch-production.json', {})
    if not saved or saved.get('inputs') != stamp(row):
        return None
    if saved.get('status') == 'needs_preparation':
        return None
    if (saved.get('policy') != PREPARATION_POLICY and saved.get('status') != 'complete'
            and read_json(Path(row['episode_dir'])/'clip_plan.json', {}).get('clips')):
        return None
    if saved.get('status') == 'rendering' and not pid_alive(int(saved.get('worker_pid') or 0)):
        return None
    if saved.get('status')=='waiting_assets' and not asset_waits(row,read_json(Path(row['episode_dir'])/'clip_plan.json',{})):
        return None
    return saved


def pending_production(ctx):
    """A repaired first-pass input belongs to the standard producer again."""
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    if not plan.get('clips') or not unfilmed_video_clips(ctx.episode_dir,plan):
        return False
    row={key:str(getattr(ctx,key)) for key in
         ('novel_dir','episode_dir','source_novel_dir','source_episode_dir')}
    receipt=current(row)
    return receipt is None or receipt.get('status') in {'ready','rendering','waiting_assets'}


def save(row, status, **details):
    directory=Path(row['episode_dir']);run=Path(row['run_dir'])
    plan=read_json(directory/'clip_plan.json',{})
    if plan.get('clips'):
        prepared=read_json(run/'prepared.json',{})
        prepared.setdefault('repair_round_start',len(read_json(run/'tool-repair-decisions.json',[])))
        prepared.update(ready=status in {'ready','rendering','complete'},native_preparation_pending=status not in {'ready','rendering','complete'},
                        method='standard batch production')
        atomic_write_json(run/'prepared.json',prepared)
        atomic_write_json(run/'sandbox/input/clip_plan.json',plan)
        atomic_write_json(run/'sandbox/input/chapter_script.json',read_json(directory/'chapter_script.json',{}))
    result = {'status': status, 'policy': PREPARATION_POLICY, 'at': time.time(), 'inputs': stamp(row), **details}
    atomic_write_json(Path(row['episode_dir'])/'batch-production.json', result)
    return result


@contextmanager
def lease(row):
    path = Path(row['source_episode_dir'])/'.render.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try: owner = int(path.read_text().strip() or 0)
        except ValueError: owner = 0
        if owner and pid_alive(owner):
            yield False
            return
        path.unlink()
    with path.open('x') as stream:
        stream.write(str(os.getpid()))
    try:
        yield True
    finally:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def bootstrap(row):
    from novel_manga.application.agents.episode.context import EpisodeContext
    from novel_manga.application.agents.episode.inputs import prepare_inputs, _copy
    from novel_manga.application.configuration import project_root
    target=read_json(Path(row['run_dir'])/'inputs.json',{}).get('target_seconds')
    ctx = EpisodeContext(project_root(), Path(row['source_novel_dir']), Path(row['source_episode_dir']),
        Path(row['run_dir']), Path(row['novel_dir']), Path(row['episode_dir']),
        Path(row['run_dir'])/'sandbox', row['input_mode'], tuple(target) if target else None)
    prepare_inputs(ctx)
    _copy(ctx.source_novel_dir/f"lean/extract/ch-{row['episode']:03d}.json",
          ctx.novel_dir/f"lean/extract/ch-{row['episode']:03d}.json")


def local_batch(parent, chapter):
    if not getattr(parent, 'workspaces', None):
        return parent
    if chapter not in parent._local_batches:
        row = parent.workspaces[str(chapter)]
        args = copy.copy(parent.args)
        args.novel_dir = row['novel_dir']
        args.workspace_index = None
        args.first_pass = True
        args.no_recurring_cards = True
        child = parent.batch_factory(args)
        child.rows = parent.rows
        child.env.update(parent.env)
        child.env['NOVEL_RESOURCE_WAIT_FILE'] = str(Path(row['run_dir'])/'resource-waits.jsonl')
        child.lock_directory = Path(row['source_episode_dir'])
        child.workspace = row
        parent._local_batches[chapter] = child
    return parent._local_batches[chapter]


def asset_waits(row, plan):
    from .asset_preparation import queued_asset_paths
    manifest = os.environ.get('NOVEL_ASSET_PREPARATION_MANIFEST')
    if not manifest:
        return []
    return queued_asset_paths(read_json(Path(manifest), []), plan, Path(row['source_novel_dir']),
        Path(row['novel_dir']), state=read_json(Path(manifest).with_name('state.json'), {}))


def prepare(parent, chapter):
    row = parent.workspaces[str(chapter)]
    if parent.args.dry_run:
        return {'status': 'would_prepare'}
    with lease(row) as owned:
        if not owned:
            return {'status': 'busy'}
        saved = current(row)
        if saved and saved['status'] in {'ready', 'blocked', 'complete', 'waiting_assets'}:
            return saved
        try:
            bootstrap(row)
            batch = local_batch(parent, chapter)
            batch.plan(chapter)
            directory = Path(row['episode_dir'])
            plan = read_json(directory/'clip_plan.json', {})
            if not plan.get('clips'):
                return save(row, 'blocked', error=batch.rows[chapter].get('note') or 'Native planning produced no clip plan')
            pending = unfilmed_video_clips(directory, plan)
            if not pending:
                return save(row, 'complete', ready_clips=[])
            wanted = {**plan, 'clips': [c for c in plan['clips'] if c['clip_id'] in pending]}
            waiting = {c['clip_id']: waits for c in wanted['clips']
                       if (waits := asset_waits(row, {**plan, 'clips': [c]}))}
            from novel_manga.application.preparation.readiness import inspect_episode, reference_issues
            from novel_manga.application.profiles import h3_prompt_outdated
            from .clip_preparation import preflight
            _, blocked = inspect_episode(directory, assets=False)
            blocked = {cid: reasons for cid, reasons in blocked.items() if cid in pending}
            previous = read_json(directory/'batch-production.json', {})
            if previous.get('policy') == PREPARATION_POLICY and previous.get('inputs') == stamp(row):
                # Rendering one ready clip must not buy another attempt at unchanged failed inputs.
                blocked.update({cid: why for cid, why in previous.get('blocked', {}).items()
                                if cid in pending and cid not in previous.get('waiting_assets', {})})
            blocked.update({cid: ['shared assets pending: ' + ', '.join(waits)] for cid, waits in waiting.items()})
            unchecked = pending - blocked.keys()
            ready = [];asset_error = None
            notes = read_json(directory/'review_feedback.json', {})
            candidates = sorted((c for c in plan['clips'] if c['clip_id'] in unchecked), key=lambda c:(
                bool(reference_issues(c, directory.parent)),
                h3_prompt_outdated(c, str(notes.get(c['clip_id']) or ''), strict=True), c['clip_id']))
            for clip in candidates:
                cid = clip['clip_id']
                if cid in blocked:
                    unchecked.discard(cid)
                    continue
                unchecked.discard(cid)
                try:
                    try:
                        batch.prepare_cards(chapter, clip_ids={cid})
                    except (RuntimeError, TimeoutError, OSError) as error:
                        asset_error = f'{type(error).__name__}: {error}'
                    batch.h3_ready(chapter, clip_ids={cid})
                    blocked.update(batch.rows[chapter].get('h3_errors', {}))
                    unchecked.difference_update(blocked)
                    if cid in blocked:
                        continue
                    clip = next(c for c in read_json(directory/'clip_plan.json')['clips'] if c['clip_id'] == cid)
                    missing = reference_issues(clip, directory.parent)
                    if missing:
                        raise ValueError('; '.join(missing))
                    preflight(directory, clip)
                    ready.append(cid)
                    break  # Publish this executable clip now; do not wait for the chapter's remaining checks.
                except Exception as error:
                    blocked[cid] = [f'{type(error).__name__}: {error}']
            status = 'ready' if ready else 'waiting_assets' if waiting and set(blocked) <= set(waiting) else 'blocked'
            return save(row, status, ready_clips=ready, unchecked_clips=sorted(unchecked), blocked=blocked,
                        waiting_assets=waiting, asset_error=asset_error)
        except (Exception,SystemExit) as error:
            return save(row, 'blocked', error=f'{type(error).__name__}: {error}')


def chapter(row):
    from .runs import clip_plan_mode
    directory = Path(row['episode_dir'])
    plan = read_json(directory/'clip_plan.json', {})
    pending = unfilmed_video_clips(directory, plan)
    done = bool(plan.get('clips')) and not pending
    receipt = current(row) or {}
    accepted = read_json(Path(row['run_dir'])/'final-candidate/manifest.json', {}).get('passed') is True
    owned = busy(row)
    prepared = len(set(receipt.get('ready_clips') or []) & pending)
    return {'n': row['episode'], 'planned': bool(plan.get('clips')), 'done': done,
            'accepted': accepted, 'unreviewed': done and not accepted, 'runs': 0,
            'blocked': not done and receipt.get('status') in {'blocked', 'waiting_assets'},
            'ready': receipt.get('status') == 'ready' and bool(prepared) and not owned,
            'ready_clips': prepared if not owned and receipt.get('status') == 'ready' else 0,
            'in_process_clips': prepared if owned else 0,
            'blocked_clips': len(set(receipt.get('blocked') or {}) & pending),
            'mode': clip_plan_mode(plan)}


def render_first(batch, n):
    """The standard batch calls the normal renderer, with one first take and no film review."""
    from .execution import render_command
    row = batch.workspace
    if batch.args.dry_run:
        batch.rows[n]['render'] = 'would render first pass'
        return
    with lease(row) as owned:
        if not owned:
            batch.rows[n]['render'] = 'locked'
            return
        if chapter(row)['done']:
            batch.rows[n]['render'] = 'first_pass_complete'
            return
        ready = current(row)
        if not ready or ready['status'] != 'ready':
            batch.rows[n]['render'] = 'waiting_preparation'
            return
        selected = sorted(set(ready.get('ready_clips') or []) & unfilmed_video_clips(Path(row['episode_dir'])))
        if not selected:
            return
        command = render_command(batch, n) + ['--first-pass', '--max-attempts', '1', '--first-clips', ','.join(selected)]
        save(row, 'rendering', ready_clips=selected, worker_pid=os.getpid(), blocked=ready.get('blocked', {}),
             unchecked_clips=ready.get('unchecked_clips', []), waiting_assets=ready.get('waiting_assets', {}))
        code, problem = batch.run(command, Path(row['episode_dir'])/'render.log')
        status = 'complete' if chapter(row)['done'] else 'blocked'
        report = read_json(Path(row['episode_dir'])/'thin_media_report.json', {})
        blocked = dict(ready.get('blocked', {}))
        for clip in report.get('clips', []):
            if clip.get('clip_id') in selected and clip.get('error'):
                blocked[clip['clip_id']] = [clip['error'], *clip.get('blocked', [])]
        remaining = unfilmed_video_clips(Path(row['episode_dir']))
        unchecked = set(ready.get('unchecked_clips', [])) & remaining
        stale = {r['clip_id'] for r in report.get('clips', []) if r.get('clip_id') in selected
                 and 'prepared request is stale' in str(r.get('error', ''))}
        unchecked.update(stale & remaining)
        for cid in stale:
            blocked.pop(cid, None)
        for cid in set(selected) & remaining - stale:
            blocked.setdefault(cid, [problem or f'renderer exited {code} without producing a first video'])
        if unchecked:
            status = 'needs_preparation'
        elif status == 'blocked' and blocked and set(blocked) <= set(ready.get('waiting_assets') or {}):
            status = 'waiting_assets'
        save(row, status, render_exit=code, error=problem if status == 'blocked' else '',
             blocked=blocked, waiting_assets=ready.get('waiting_assets', {}),
             unchecked_clips=sorted(unchecked), remaining_clips=sorted(remaining))
        batch.rows[n].update(render='first_pass_complete' if status == 'complete' else
                            'first_pass_partial' if status in {'needs_preparation','waiting_assets'} else 'first_pass_blocked', note=problem)
