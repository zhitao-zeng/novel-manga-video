"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations


def file_stamp(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_mtime_ns, info.st_size


def progress(ctx, status):
    paths = [ctx.work_dir / name for name in (
        'output/episode.json', 'output/requests.json', 'output/repair.json',
        'input/latest-render.json', 'input/latest-review.json', 'input/latest-finish.json')]
    paths.extend(ctx.run_dir / name for name in ('prepared.json', 'tool-repair-decisions.json'))
    # These small JSON outputs carry actual progress. Equal-size edits can keep
    # the same timestamp, and touching an unchanged draft is not progress.
    outputs = []
    for path in paths:
        try:
            outputs.append(path.read_bytes())
        except FileNotFoundError:
            outputs.append(None)
    job = status.get('job') or {}
    return tuple(outputs), job.get('id'), job.get('status')


def available_operations(status):
    operations = {(cid, action) for cid, actions in (status.get('repair_actions') or {}).items() for action in actions}
    operations.update((cid, 'input:' + mode) for cid, routes in (status.get('input_operations') or {}).items()
                      for mode, route in routes.items() if route.get('allowed'))
    return operations


def made_progress(ctx, before, before_status, status):
    """New job IDs and reworded rejected proposals are not productive work."""
    # A newly granted route deserves one turn to use it. The same unused route
    # on the following turn does not keep the watchdog alive indefinitely.
    if available_operations(status) - available_operations(before_status):
        return True
    job = status.get('job') or {}
    prior = before_status.get('job') or {}
    outcome = job.get('result') or {}
    if job.get('id') != prior.get('id') and job.get('status') in {'completed', 'failed'}:
        if (outcome.get('changed') or outcome.get('media_changed') or outcome.get('assets_added')
                or set(outcome.get('input_repaired') or []) - set(outcome.get('input_reused') or [])):
            return True
        if set(outcome.get('keep_checks') or []) & set(outcome.get('accepted') or []):
            return True
        if outcome.get('argument_errors'):
            return False
        if job.get('status') == 'failed' or outcome.get('ok') is False:
            # A new failure explanation is evidence, not a repaired input.
            # Partial published changes were handled above, before this branch.
            return False
        if job.get('action') == 'apply' and 'changed' in outcome:
            return False
        if job.get('action') == 'doctor':
            return before[0] != progress(ctx, status)[0]
        if job.get('action') == 'review':
            return before[0] != progress(ctx, status)[0] or outcome != (prior.get('result') or {})
        if job.get('action') == 'render' and 'generation_counts' in before_status and 'generation_counts' in status:
            def media_state(row):
                media = row.get('media') or {}
                return (row.get('adopted'), row['generation_counts'],
                        tuple((key, media.get(key)) for key in
                              ('status', 'quality_passed', 'technical_passed', 'failed_clips',
                               'gate_failed_clips', 'movie')),
                        sorted(media.get('blocked_clips') or {}))
            return media_state(before_status) != media_state(status)
    return before != progress(ctx, status)


def stop_details(status):
    """Retain the last concrete obstacle instead of reporting only the loop guard."""
    job = status.get('job') or {}
    outcome = job.get('result') or {}
    repairs = status.get('input_repairs') or {}
    states = {r.get('status') for r in repairs.values()}
    kind = ('argument_error' if outcome.get('argument_errors') else
            'tool_error' if 'tool_error' in states else
            'source_rejected' if 'rejected' in states else
            'input_repair_incomplete' if states & {'checking', 'incomplete', 'published'} else
            'no_effective_operation')
    return {'kind': kind, 'last_operation': job.get('action'),
            'blocked': outcome.get('blocked') or {}, 'input_repairs': repairs,
            'remaining_takes': status.get('remaining_takes') or {},
            'remaining_rechecks': status.get('remaining_rechecks') or {}}


def applied_repair(job):
    return (job.get('action') == 'apply' and job.get('status') == 'completed'
            and bool((job.get('result') or {}).get('changed')
                     or set((job.get('result') or {}).get('input_repaired') or [])
                     - set((job.get('result') or {}).get('input_reused') or [])))
