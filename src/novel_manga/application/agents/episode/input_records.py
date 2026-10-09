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


def asset_configuration_recoverable(ctx, record):
    """One unchanged continuation for the observed pre-request image-port failure."""
    if (record.get('status') != 'incomplete' or record.get('step') != 'assets'
            or record.get('published') or record.get('asset_configuration_recovery')
            or (record.get('source') or {}).get('consistent') is not True
            or 'image helper exited' not in str(record.get('error', ''))):
        return False
    names=set((_phase_groups(record.get('decision') or {}).get('characters') or {}))
    if not names:
        return False
    from novel_manga.llm.resources import _endpoint
    base=os.environ.get('NOVEL_LOCAL_QWEN_IMAGE_URL','')
    try:
        if base:_endpoint(base)
    except ValueError:
        return False
    log=ctx.run_dir/'input-assets.log'
    if not log.is_file():return False
    prefix=f'-agent-{ctx.episode_dir.name}-{record["clip_id"]}-'
    latest={}
    for line in log.read_text().splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if (isinstance(row,dict) and row.get('name') in names
                and prefix in str(row.get('asset_id',''))):
            latest[row['name']]=row
    return names==set(latest) and all(
        'model resource endpoint has an invalid port' in str(row.get('status',''))
        and row.get('seconds') == 0 for row in latest.values())


def source_review_recoverable(record):
    """Only a saved rejection from before defect confirmation needs rechecking."""
    return (record.get('status') == 'rejected' and record.get('step') == 'source'
            and bool(record.get('decision')) and not record.get('published')
            and int(record.get('source_review_policy', 1)) < SOURCE_REVIEW_POLICY)


def posture_protocol_recoverable(record):
    """One same-candidate continuation after replacing the roster-echo protocol."""
    error = str(record.get('error', ''))
    return (record.get('status') == 'incomplete' and record.get('step') == 'compile'
            and not record.get('published') and not record.get('posture_protocol_recovery')
            and bool(record.get('decision')) and (record.get('source') or {}).get('consistent') is True
            and '姿态台账镜' in error and '入镜名单不符' in error)


def latest_input_records(episode_dir):
    records = {}
    for path in sorted((Path(episode_dir)/'work/input_repairs').glob('clip_*-*.json'),
                       key=lambda p: (p.stat().st_mtime_ns, p.name)):
        record = read_json(path, {})
        if record.get('clip_id'):
            records[record['clip_id']] = (path, record)
    return records


def _phase_groups(row):
    phases = copy.deepcopy(row.get('phases') or {})
    characters = phases.get('characters') if isinstance(phases, dict) else None
    if isinstance(characters, dict):
        for name, value in characters.items():
            if isinstance(value, dict):
                characters[name] = [value]
    return phases


def effects(row):
    result = {'shots': sorted(row.get('shots') or [], key=lambda shot: shot['index']),
              'phases': _phase_groups(row), 'locations': row.get('locations') or {}}
    if row.get('characters'):
        result['characters'] = row['characters']
    if row.get('clip_ids'):
        result['clip_ids'] = sorted(row['clip_ids'])
    if isinstance(result['phases'], dict) and not any(result['phases'].values()):
        result['phases'] = {}
    if not any(result.values()):
        result['clip_id'] = row.get('clip_id')  # a wearing check without edits belongs to this clip
    return result


def remaining_input_attempts(ctx, clip_ids, *, records=None):
    if records is None:
        records = [(p, read_json(p, {})) for p in (ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json')]
    grants = read_json(ctx.episode_dir/'repair_budget_grants.json', {})
    counts = history.generation_counts(ctx.episode_dir) if any(
        history.conditional_extra(ctx.episode_dir, cid, grants=grants) for cid in clip_ids) else {}
    return {cid: max(0, history.input_limit(ctx.episode_dir, cid, default=INPUT_LIMIT, grants=grants, counts=counts)
                - sum(r.get('status') != 'tool_error'
                for p, r in records if r.get('clip_id') == cid or p.name.startswith(cid+'-')))
            for cid in clip_ids}


def input_receipt(ctx, cid, row, *, image_review_policy=None, check_only=False):
    directory = ctx.episode_dir/'work/input_repairs'
    paths = sorted(directory.glob('clip_*-*.json'), key=lambda p: p.stat().st_mtime)
    records = [(p, read_json(p, {})) for p in paths]
    trials = history.load(ctx.episode_dir).get('trials', [])
    for path, record in records:
        if record.get('status') == 'prepared' and not record.get('decision'):
            for trial in reversed(trials):
                decision = (trial.get('changes') or {}).get(record.get('clip_id'))
                if trial.get('method') == 'episode_agent_input' and isinstance(decision, dict):
                    record['decision'] = decision
                    break
    matches = [(p, r) for p, r in records if r.get('decision')
               and r.get('status') != 'tool_error' and effects(r['decision']) == effects(row)]
    for path, record in reversed(matches):
        if record.get('status') in {'prepared', 'checked_existing_wearing'}:
            return path, record
    if matches and all(r.get('status') == 'rejected' for _, r in matches):
        path, record = matches[-1]
        if source_review_recoverable(record):
            latest = latest_input_records(ctx.episode_dir).get(record['clip_id'])
            if latest is None or latest[0] != path:
                raise ValueError('source recovery cannot replace a newer input receipt')
            record['previous_source_review'] = {'policy': record.get('source_review_policy', 1),
                'error': record.get('error'), 'review': record.get('source_review')}
            record.pop('source', None)
            # Reserve before the judge call: restart never renews this recovery.
            record.update(status='checking', source_review_policy=SOURCE_REVIEW_POLICY)
            if not check_only:atomic_write_json(path, record)
            return path, record
        if (image_review_policy is not None and record.get('step') == 'draft_assets'
                and (record.get('source') or {}).get('consistent') is True
                and int(record.get('image_review_policy', 1)) < image_review_policy):
            # The source decision and its existing input allowance are retained.
            # Only a corrected picture judge may revisit an older image verdict.
            record['previous_image_review'] = {'policy': record.get('image_review_policy', 1),
                                               'error': record.get('error')}
            record.update(status='checking', image_review_policy=image_review_policy)
            if not check_only:atomic_write_json(path, record)
            return path, record
        raise ValueError('the same input correction was rejected by source review')
    pending = [(p, r) for p, r in (matches or records)
               if r.get('status') in {'checking', 'incomplete', 'published', 'rejected'}
               and (matches or r.get('clip_id') == cid)]
    if (pending and effects(pending[-1][1].get('decision') or {}) != effects(row)
            and (pending[-1][1].get('published') or (
                int(pending[-1][1].get('revision_count',len(pending[-1][1].get('previous_decisions',[])))) >= 1
                and ('input_limit' in read_json(ctx.episode_dir/'repair_budget_grants.json',{}).get(cid,{})
                     or history.conditional_extra(ctx.episode_dir,cid).get('input_limit'))
                and remaining_input_attempts(ctx,[cid],records=records)[cid] > 0))):
        # A published correction can only resume its own remaining bookkeeping.
        # An explicit clip input grant can fund a different candidate after the
        # old revision is spent. Default revision/continuation limits still stop.
        pending = []
    if pending:
        path, record = pending[-1]
        if effects(record.get('decision') or {}) != effects(row):
            revisions = int(record.get('revision_count', len(record.get('previous_decisions', []))))
            if revisions >= 1:
                raise ValueError('input repair revision allowance used; keep the unresolved candidate')
            record['revision_count'] = revisions + 1
            record.setdefault('previous_decisions', []).append(record.get('decision'))
            record.pop('source', None)
            record['asset_revision'] = record['revision_count']
        elif asset_configuration_recoverable(ctx, record):
            # Reserve before launching the helper. Preserve the original input,
            # revision and resume counts; this is not another content proposal.
            record['asset_configuration_recovery']={'at':time.strftime('%F %T'),
                'previous_error':record.get('error'), 'reason':'Image endpoint configuration repaired'}
            record['status']='checking'
        elif posture_protocol_recoverable(record):
            record['posture_protocol_recovery'] = {'at': time.strftime('%F %T'),
                'previous_error': record.get('error'), 'reason': 'Posture addresses are now schema-owned keys'}
            record['status'] = 'checking'
        elif record.get('status') not in {'checking', 'published'}:
            # A process restart continues the same unfinished operation; only an
            # operation that returned a failure spends the continuation allowance.
            if int(record.get('resume_count', 0)) >= 1:
                raise ValueError('input repair continuation allowance used; keep the incomplete candidate')
            record['resume_count'] = int(record.get('resume_count', 0)) + 1
        record['decision'] = copy.deepcopy(row)
        if not check_only:atomic_write_json(path, record)
        return path, record
    own = [(p, r) for p, r in records if r.get('clip_id') == cid or p.name.startswith(cid+'-')]
    if remaining_input_attempts(ctx, [cid], records=records)[cid] <= 0:
        raise ValueError('input repair allowance used; keep the unresolved source/input failure')
    attempt = max((int(p.stem.rsplit('-', 1)[1]) for p, _ in own), default=0) + 1
    path = directory/f'{cid}-{attempt}.json'
    record = {'status': 'checking', 'clip_id': cid, 'reason': row['reason'],
              'source_quote': row['source_quote'], 'decision': copy.deepcopy(row), 'resume_count': 0}
    if not check_only:atomic_write_json(path, record)
    return path, record
