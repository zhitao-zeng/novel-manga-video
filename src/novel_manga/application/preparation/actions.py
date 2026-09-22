"""Named preparation steps; generation policy and write order are unchanged."""
from __future__ import annotations
import novel_manga.episodes as ep_names
from novel_manga.application.configuration import project_root

from pathlib import Path
import json
import os
import subprocess
import sys
import time
from novel_manga.util import atomic_write_json
from novel_manga.planning.preparation import POLICY, needs_full_replan
from novel_manga.application.preparation.store import read, inputs, record
from novel_manga.application.preparation.audit import audit
ROOT = project_root()

def run_tool(args, timeout=1200):
    result = subprocess.run([sys.executable, *args], cwd=ROOT, timeout=timeout,
                            env={**os.environ, 'NOVEL_CLIP_SECONDS_MAX': '15'})
    if result.returncode:
        raise ValueError(f'{Path(args[0]).name} returned {result.returncode}; see episode log')


def replan(directory, issues):
    """Only a failed/missing plan or a grounded story defect triggers new planning."""
    novel = directory.parent
    meta = read(novel / 'novel.json', {})
    n = ep_names.chapter_of(directory.name)
    notes = '只修正这些已对照原文的问题，完整保留本章主要事件和真实顺序：' + json.dumps(issues, ensure_ascii=False)
    run_tool(['scripts/plan_chapter_thin.py', meta['source'], '--novel-id', novel.name,
              '--episode-index', str(n), '--bible', str(novel / 'story_bible.json'),
              '--output-root', str(novel.parent), '--tier', 'fast', '--frame', '16:9',
              '--max-redo', '1', '--timeout', '360', '--notes', notes])
    run_tool(['scripts/build_clip_plan_thin.py', '--episode-dir', str(directory),
              '--bible', str(novel / 'story_bible.json'), '--tier', 'fast', '--frame', '16:9'])


def prepare_plan(directory):
    from novel_manga.application.packing.single_card import single_card_plan
    plan_path = directory / 'clip_plan.json'
    if not plan_path.is_file():
        record(directory, 'replanning', reason='latest planning failed; no packed plan')
        replan(directory, read(directory / 'planning_failed.json', {}).get('errors', []))
    plan = read(plan_path, {})
    if float(plan.get('limits', {}).get('max_clip_seconds') or 15) != 15:
        run_tool(['scripts/build_clip_plan_thin.py', '--episode-dir', str(directory),
                  '--bible', str(directory.parent / 'story_bible.json'), '--tier', 'fast', '--frame', '16:9'])
        plan = read(plan_path, {})
    if single_card_plan(plan):
        atomic_write_json(plan_path, plan)
    return plan


def check_script(directory, plan):
    n = ep_names.chapter_of(directory.name)
    cache_path = directory / 'pre_render_story_audit.json'
    cache = read(cache_path, {})
    record(directory, 'auditing')
    if cache.get('inputs') == inputs(directory) and cache.get('policy') == POLICY:
        answer = cache['answer']
    else:
        answer = audit(directory)
        atomic_write_json(cache_path, {'policy': POLICY, 'at': time.strftime('%F %T'),
                                     'inputs': inputs(directory), 'answer': answer})
    if not answer['source_readable']:
        return None, record(directory, 'needs_source', audit=answer)
    if answer['issues']:
        record(directory, 'repairing', audit=answer)
        full = needs_full_replan(answer['issues'])
        if full:
            replan(directory, answer['issues'])
        else:
            from novel_manga.application.repair.flow import repair_episode
            targets = {}
            for clip in plan['clips']:
                found = [i for i in answer['issues'] if i['stage'] in clip.get('shot_indexes', [])]
                if found:
                    targets[clip['clip_id']] = json.dumps(found, ensure_ascii=False) + '。按原文修正台词归属和动作。'
            result = repair_episode(directory.parent, n, False, use_history=False, reframe=True,
                                    source_issues=targets, return_proposal=True)
            proposal = result.get('proposal')
            if not proposal or set(targets) - set(result.get('changed', [])):
                return None, record(directory, 'needs_repair', reason=result.get('why', 'incomplete local repair'))
            from novel_manga.repair.proposal import RepairProposal
            from novel_manga.application.repair.publication import publish_preparation
            publish_preparation(directory, RepairProposal.from_result(result))
        # A rewritten script must be checked again; success is never inferred
        # from the rewriting model or the presence of the new files.
        after = audit(directory)
        atomic_write_json(cache_path, {'policy': POLICY, 'at': time.strftime('%F %T'),
                                     'inputs': inputs(directory), 'answer': after, 'before': answer})
        if not after['source_readable'] or after['issues']:
            return None, record(directory, 'needs_source' if not after['source_readable'] else 'needs_repair', audit=after)
        answer = after
    return answer, None


def refresh_entity_bindings(directory):
    plan_path = directory / "clip_plan.json"
    # Rebind current source entity types before translating legacy requests.
    from novel_manga.application.identity.store import current_context
    from novel_manga.application.identity.context import typed_entities
    from novel_manga.application.repair.flow import rebuild_clips
    plan = read(plan_path, {})
    types = typed_entities(directory.parent, current_context(directory))
    targets = {c['clip_id'] for c in plan.get('clips', []) if set(c.get('cast', [])) & set(types)}
    if targets:
        plan, changed = rebuild_clips(directory, directory.parent / 'story_bible.json',
                                     read(directory / 'chapter_script.json', {}), plan, targets)
        if changed:
            atomic_write_json(plan_path, plan)
    return plan


def ensure_cards(directory, plan):
    missing = {r['asset_id'] for c in plan['clips'] for r in c.get('references', [])
               if r.get('role') in {'character', 'location'} and not (directory.parent / r['path']).is_file()}
    if missing:
        record(directory, 'building_cards', missing_assets=sorted(missing))
        run_tool(['scripts/build_cards_thin.py', '--novel-dir', str(directory.parent),
                  '--assets', ','.join(sorted(missing)), '--tier', 'fast', '--review'])


def translate_and_check(directory, plan, answer):
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.application.profiles import h3_prompt_outdated
    from novel_manga.story.h3 import request_issues
    from novel_manga.application.preparation.readiness import inspect_episode
    plan_path = directory / "clip_plan.json"
    record(directory, 'translating', audit=answer)
    notes = read(directory / 'review_feedback.json', {})
    for clip in plan['clips']:
        if clip.get('kind') != 'video':
            continue
        note = str(notes.get(clip['clip_id'], ''))
        if h3_prompt_outdated(clip, note):
            if not convert(clip, note=note):
                return record(directory, 'error', reason='English prompt incomplete: ' + clip['clip_id'])
            atomic_write_json(plan_path, plan)  # each completed translation survives an interruption
        if request_issues(clip):
            return record(directory, 'needs_repair', reason=request_issues(clip))
    _, blocked = inspect_episode(directory, assets=True)
    return record(directory, 'needs_repair' if blocked else 'ready', blocks=blocked, audit=answer)
