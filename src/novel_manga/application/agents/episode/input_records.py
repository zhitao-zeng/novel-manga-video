"""Shared input-candidate receipts and bounded continuation accounting."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json

INPUT_LIMIT = 2
SOURCE_REVIEW_POLICY = 2


from .input_recovery_policy import asset_configuration_recoverable, source_review_recoverable, posture_protocol_recoverable


def latest_input_records(episode_dir):
    records = {}
    for path in sorted((Path(episode_dir)/'work/input_repairs').glob('clip_*-*.json'),
                       key=lambda p: (p.stat().st_mtime_ns, p.name)):
        record = read_json(path, {})
        if record.get('clip_id') and record.get('operation') != 'upstream_asset':
            records[record['clip_id']] = (path, record)
    return records


from .input_effects import _phase_groups, effects


def remaining_input_attempts(ctx, clip_ids, *, records=None):
    if records is None:
        records = [(p, read_json(p, {})) for p in (ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json')]
    grants = read_json(ctx.episode_dir/'repair_budget_grants.json', {})
    counts = history.generation_counts(ctx.episode_dir) if any(
        history.conditional_extra(ctx.episode_dir, cid, grants=grants) for cid in clip_ids) else {}
    return {cid: max(0, history.input_limit(ctx.episode_dir, cid, default=INPUT_LIMIT, grants=grants, counts=counts)
                - sum(r.get('status') != 'tool_error'
                for p, r in records if r.get('operation')!='upstream_asset' and (r.get('clip_id') == cid or p.name.startswith(cid+'-'))))
            for cid in clip_ids}


def input_records(ctx):
    records = [(p, read_json(p, {})) for p in sorted(
        (ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json'),
        key=lambda p: (p.stat().st_mtime_ns, p.name))]
    records = [(p,r) for p,r in records if r.get('operation')!='upstream_asset']
    trials = history.load(ctx.episode_dir).get('trials', [])
    for _, record in records:
        if record.get('status') == 'prepared' and not record.get('decision'):
            for trial in reversed(trials):
                decision = (trial.get('changes') or {}).get(record.get('clip_id'))
                if trial.get('method') == 'episode_agent_input' and isinstance(decision, dict):
                    record['decision'] = decision
                    break
    return records


def _standard_input_route(ctx, cid, row=None, *, records=None, image_review_policy=None):
    """Select the legal receipt operation without changing a candidate or its counts.

    row=None asks which route a different source-backed proposal would use.
    Both status and input_receipt use this same allowance decision.
    """
    records = input_records(ctx) if records is None else records
    matches = [(p, r) for p, r in records if row is not None and r.get('decision')
               and r.get('status') != 'tool_error' and effects(r['decision']) == effects(row)]
    for path, record in reversed(matches):
        if record.get('status') in {'prepared', 'checked_existing_wearing'}:
            return 'reuse', path, record
    if matches and all(r.get('status') == 'rejected' for _, r in matches):
        path, record = matches[-1]
        if source_review_recoverable(record):
            own = [(p, r) for p, r in records if r.get('clip_id') == record['clip_id']]
            if own and own[-1][0] != path:
                raise ValueError('source recovery cannot replace a newer input receipt')
            return 'source_recovery', path, record
        if (image_review_policy is not None and record.get('step') == 'draft_assets'
                and (record.get('source') or {}).get('consistent') is True
                and int(record.get('image_review_policy', 1)) < image_review_policy):
            return 'image_recovery', path, record
        raise ValueError('the same input correction was rejected by source review')
    pending = [(p, r) for p, r in (matches or records)
               if r.get('status') in {'checking', 'incomplete', 'published', 'rejected'}
               and (matches or r.get('clip_id') == cid)]
    different = bool(pending and (row is None or effects(pending[-1][1].get('decision') or {}) != effects(row)))
    if different:
        last = pending[-1][1]
        grant = read_json(ctx.episode_dir/'repair_budget_grants.json', {}).get(cid, {})
        if (last.get('published') or (
                int(last.get('revision_count', len(last.get('previous_decisions', [])))) >= 1
                and ('input_limit' in grant or history.conditional_extra(ctx.episode_dir, cid).get('input_limit'))
                and remaining_input_attempts(ctx, [cid], records=records)[cid] > 0)):
            pending = []
    if pending:
        path, record = pending[-1]
        if different:
            if int(record.get('revision_count', len(record.get('previous_decisions', [])))) >= 1:
                raise ValueError('input repair revision allowance used; keep the unresolved candidate')
            return 'revise', path, record
        if asset_configuration_recoverable(ctx, record):
            return 'asset_recovery', path, record
        if posture_protocol_recoverable(record):
            return 'posture_recovery', path, record
        if record.get('status') not in {'checking', 'published'} and int(record.get('resume_count', 0)) >= 1:
            raise ValueError('input repair continuation allowance used; keep the incomplete candidate')
        return 'resume', path, record
    if remaining_input_attempts(ctx, [cid], records=records)[cid] <= 0:
        raise ValueError('input repair allowance used; keep the unresolved source/input failure')
    # Quota excludes asset diagnosis, but both receipts occupy this filename space.
    own = list((ctx.episode_dir/'work/input_repairs').glob(f'{cid}-*.json'))
    attempt = max((int(p.stem.rsplit('-', 1)[1]) for p in own), default=0) + 1
    return 'new', ctx.episode_dir/'work/input_repairs'/f'{cid}-{attempt}.json', {}


def input_route(ctx,cid,row=None,*,records=None,image_review_policy=None):
    try:
        return _standard_input_route(ctx,cid,row,records=records,image_review_policy=image_review_policy)
    except ValueError:
        from .bounded_recovery import input_operation
        if row is not None and input_operation(ctx,cid,row)['allowed']:
            own=list((ctx.episode_dir/'work/input_repairs').glob(f'{cid}-*.json'))
            number=max((int(p.stem.rsplit('-',1)[1])for p in own),default=0)+1
            return 'upstream_recovery',ctx.episode_dir/'work/input_repairs'/f'{cid}-{number}.json',{}
        raise


def input_operations(ctx, cid, *, records=None):
    records = input_records(ctx) if records is None else records
    result = {mode: {'allowed': False} for mode in ('new', 'revise', 'resume')}
    try:
        route, _, _ = input_route(ctx, cid, records=records)
        result[route] = {'allowed': True}
    except ValueError as error:
        for mode in ('new', 'revise'):
            result[mode]['reason'] = str(error)
    own = [r for _, r in records if r.get('clip_id') == cid]
    latest = own[-1] if own else {}
    if latest.get('decision') and (latest.get('status') in {'checking', 'incomplete', 'published'}
                                   or source_review_recoverable(latest)):
        try:
            input_route(ctx, cid, latest['decision'], records=records)
            result['resume'] = {'allowed': True}
        except (ValueError, KeyError, TypeError) as error:
            result['resume']['reason'] = str(error)
    else:
        result['resume']['reason'] = 'no incomplete input repair is available to resume'
    from .bounded_recovery import input_operation
    grant=read_json(ctx.episode_dir/'repair_budget_grants.json',{}).get(cid,{}).get('upstream_recovery',{}).get('input')or{}
    if grant.get('candidate') and input_operation(ctx,cid,grant['candidate'])['allowed']:
        result['recovery']={'allowed':True,'candidate':grant['candidate']}
    return result


def input_receipt(ctx, cid, row, *, image_review_policy=None, check_only=False):
    route, path, record = input_route(ctx, cid, row, image_review_policy=image_review_policy)
    record = copy.deepcopy(record)
    if route == 'reuse':
        return path, record
    if route == 'source_recovery':
        record['previous_source_review'] = {'policy': record.get('source_review_policy', 1),
            'error': record.get('error'), 'review': record.get('source_review')}
        record.pop('source', None)
        record.update(status='checking', source_review_policy=SOURCE_REVIEW_POLICY)
    elif route == 'image_recovery':
        record['previous_image_review'] = {'policy': record.get('image_review_policy', 1), 'error': record.get('error')}
        record.update(status='checking', image_review_policy=image_review_policy)
    elif route == 'revise':
        record['revision_count'] = int(record.get('revision_count', len(record.get('previous_decisions', [])))) + 1
        record.setdefault('previous_decisions', []).append(record.get('decision'))
        record.pop('source', None)
        record['asset_revision'] = record['revision_count']
    elif route in {'asset_recovery', 'posture_recovery'}:
        key = 'asset_configuration_recovery' if route == 'asset_recovery' else 'posture_protocol_recovery'
        record[key] = {'at': time.strftime('%F %T'), 'previous_error': record.get('error'),
            'reason': 'Image endpoint configuration repaired' if route == 'asset_recovery' else 'Posture addresses are now schema-owned keys'}
        record['status'] = 'checking'
    elif route == 'resume' and record.get('status') not in {'checking', 'published'}:
        record['resume_count'] = int(record.get('resume_count', 0)) + 1
    elif route == 'upstream_recovery':
        from .bounded_recovery import input_operation
        permit=input_operation(ctx,cid,row,consume=not check_only)
        if not permit['allowed']:raise ValueError('input recovery was already used')
        record={'status':'checking','clip_id':cid,'reason':row['reason'],'source_quote':row['source_quote'],
                'resume_count':1,'revision_count':1,'upstream_recovery':permit['grant']}
    elif route == 'new':
        record = {'status': 'checking', 'clip_id': cid, 'reason': row['reason'],
                  'source_quote': row['source_quote'], 'resume_count': 0}
    record['decision'] = copy.deepcopy(row)
    if not check_only:
        atomic_write_json(path, record)
    return path, record
