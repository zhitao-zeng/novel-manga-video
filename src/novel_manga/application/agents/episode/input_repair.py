"""Source-backed input repairs through existing cards, compilation and publication."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import subprocess
import sys

from novel_manga.application.identity.phases import LOOK_FIELDS, chapter_of
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError, UnsupportedInput, CompileConflict, validate_resume
from .input_records import (INPUT_LIMIT, SOURCE_REVIEW_POLICY, _phase_groups, effects,
                           remaining_input_attempts, input_receipt, source_review_recoverable,
                           latest_input_records)
from .input_records import asset_configuration_recoverable

from .input_contracts import normalize_arguments, expand_shared_scope, SHOT_NAMES, SHOT_FIELDS
from .input_compilation import compile_input
from .input_candidate import prepare_candidate






def repair_status(ctx):
    states = {}
    records = [(p, read_json(p, {})) for p in sorted(
        (ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json'), key=lambda p: p.stat().st_mtime)]
    for path, record in records:
        cid = record.get('clip_id')
        if not cid:
            continue
        row = {key: record.get(key) for key in ('status', 'step', 'error', 'error_kind', 'resume_count', 'revision_count', 'changed')}
        if record.get('error_details'):row['error_details']=record['error_details']
        if record.get('decision') and (source_review_recoverable(record) or record.get('status') in {'checking', 'published'} or (
                record.get('status') == 'incomplete' and int(record.get('resume_count', 0)) < 1)):
            row['resume_action'] = {'clip_id': cid, 'action': 'input', 'resume': True,
                                    'reason': 'Continue the saved input repair using its existing assets'}
        if (not record.get('published') and record.get('status') in {'rejected', 'incomplete'}
                and int(record.get('revision_count', len(record.get('previous_decisions', [])))) < 1):
            row['revision_available'] = True
            row['next_action'] = 'Correct the rejected ownership/source fields once; do not repeat the same proposal or draw assets for an unsupported state.'
        decision = record.get('decision') or {}
        if not decision and not record.get('published') and record.get('status') in {'checking','incomplete'}:
            row['revision_available'] = int(record.get('revision_count',len(record.get('previous_decisions',[])))) < 1
            row['next_action'] = ('The saved input has no complete decision to resume. Submit one full source-backed '
                                  'input proposal using the existing revision allowance.' if row['revision_available'] else
                                  'The saved decision is missing and the revision allowance is used; stop this input.')
        if (record.get('status') in {'checking','incomplete'} and decision and not record.get('published')
                and record.get('step') != 'draft_assets'):
            try:
                normalize_arguments(decision)
            except RepairArgumentError as error:
                row.pop('resume_action',None)
                row['argument_error'] = str(error)
                row['revision_available'] = int(record.get('revision_count',len(record.get('previous_decisions',[])))) < 1
                row['next_action'] = ('Correct these arguments once using the existing revision allowance; do not resume the same malformed request.'
                                      if row['revision_available'] else 'The saved arguments are invalid and the revision allowance is used; stop this unresolved input.')
        states[cid] = row
    for cid, remaining in remaining_input_attempts(ctx, states, records=records).items():
        row = states[cid]
        row['remaining_attempts'] = remaining
        if row.get('status') == 'tool_error':
            row['next_action'] = ('This historical tool_error is not counted as an input attempt. '
                                  'Use the current allowances, not the old blocked message.')
            pending = [r for _, r in records if r.get('clip_id') == cid
                       and r.get('status') in {'checking','incomplete','published','rejected'}]
            if pending and not pending[-1].get('published'):
                previous = pending[-1]
                row['revision_available'] = int(previous.get('revision_count',len(previous.get('previous_decisions',[])))) < 1
            if remaining > 0 or row.get('revision_available'):
                row['next_action'] += ' Submit a complete source-backed input proposal; the host will reuse the existing allowance or its one revision.'
        record=next(r for _,r in reversed(records) if r.get('clip_id')==cid)
        if asset_configuration_recoverable(ctx,record):
            row['resume_action']={'clip_id':cid,'action':'input','resume':True,
                'reason':'Continue the unchanged saved candidate after the image configuration fix'}
            row['next_action']='The image configuration failure was fixed. Resume this exact candidate once; preserve source, completed assets and all input/video counts.'
    return states


def source_check(source, quote, shots, phases, locations=None, scoped_shots=None, *, characters=None):
    from novel_manga.llm.client import ask_json, obj
    from novel_manga.review.endpoints import judge_settings
    prompt = ('核对原文与一份局部输入修复候选。判断修改后的入镜人物、群演、听者、地点、光线、外貌、穿戴和姿态衔接是否有原文依据，'
              '只允许原文已有事件及归属的纠正，不得新增动作事件、机位或时长。speaker_map只能纠正原文能明确核实的旧说话人错误；无证据不能换说话人。'
              '只有shots显式包含turn_texts时，允许对超出发声时长的改编对白缩写；必须保留关键问答、决定、否定、条件、因果、数字含义及说话人，不能只因非逐字引用而拒绝。其他对白不得更改。'
              '若shots显式包含turns，则本次申请是语义修复：可按原文纠正、删除错误改编对白，补回必要问答，纠正说话者及内心/公开发声方式。'
              '必须保留原文关键事实、人物知识边界、立场和因果；不能把猜测变成事实，不能让旁人听见原文中的内心想法。'
              '不得用旧错误设定否定原文明确事实，也不得凭引用真实就默认修改正确。'
              '允许纠正旧稿错误的演员绑定和声景发声暗示、登记原文已有但素材库缺失的人物；不得虚构原文没有的人物或事件。'
              'characters是缺失人物素材设计；群体必须保留原文人数和各自身份，不能用无关主角替代，不把群体换装误当单人的状态。'
              '人物与穿戴归属必须对应；常规美术设计可合理补足，不能改变明确的室内外位置。'
              'locations 是无人场景参考资产的说明，不是成片镜头；参考图无人不代表成片无人，成片入镜人物以 affected_shots 为准。'
              '资产名称是用于区分地点的标签，不要求逐字出现在原文；窗户、材质等常规美术细节只在与明确原文冲突时否定。'
              '仅核对候选实际修改的字段及其直接依赖，不因未修改的旧稿其他问题否决这项局部修复。'
              '原文未逐字描述的正常衣料声、呼吸声、站位、长袍长度、发型等美术实现不是新增剧情；'
              '只拦截明确事实矛盾，不因“未明确说到”推定矛盾。称谓或同义地点标签也不等于换了地点。'
              '不支持或与原文相反的修改给出具体problems；所有修改有依据才consistent=true。'
              'problems仅填写确定的事实矛盾，不能填写“有依据”“符合要求”等肯定说明；没有矛盾必须problems=[]。\n'
              + json.dumps({'source': source, 'source_quote': quote, 'shots': shots,
                            'phases': phases, 'locations': locations or {}, 'characters': characters or {},
                            'affected_shots': scoped_shots or []}, ensure_ascii=False))
    result = ask_json([{'type': 'text', 'text': prompt}],
        obj({'consistent': {'type': 'boolean'}, 'problems': {'type': 'array', 'items': {'type': 'string'}}}),
        name='episode_input_source', max_tokens=1200, timeout=180, settings=judge_settings())
    if result.get('consistent') is not True or result.get('problems'):
        # A judge may put support for the candidate in its problems array, or
        # contradict its own boolean. Confirm actual defects once, not the prose
        # shape, against the original source and the same scoped candidate.
        confirmation = ask_json([{'type': 'text', 'text': prompt + '\n'
            '现在独立核实这份初审结论，初审文字不是事实。只返回实际成立的原文与候选冲突。'
            '剔除肯定说明、合法缩写、旧稿未改字段和无证据推断；不能因初审不通过而维持拒绝。'
            '每项conflicts必须给逐字source_quote和具体problem，说明候选哪个字段与原文哪项事实相反。'
            '如果全部有据或只有肯定说明，conflicts=[]；无确定矛盾不补造问题。\n'
            + json.dumps({'initial': result}, ensure_ascii=False)}],
            obj({'conflicts': {'type': 'array', 'items': obj({
                'source_quote': {'type': 'string'}, 'problem': {'type': 'string'}})}}),
            name='episode_input_source_confirmation', max_tokens=1200, timeout=180, settings=judge_settings())
        conflicts = confirmation['conflicts']
        if any(not c['source_quote'].strip() or c['source_quote'] not in source
               or not c['problem'].strip() for c in conflicts):
            raise ValueError('input source confirmation lacks a verbatim source quote or concrete conflict')
        result = {'consistent': not conflicts, 'problems': [c['problem'] for c in conflicts],
                  'initial': result, 'confirmation': confirmation}
        if conflicts:
            error = UnsupportedInput('input correction lacks source support: ' + '; '.join(result['problems']))
            error.review = result
            raise error
    return result


from .input_assets import local_phases


def build_phases(ctx, names):
    from novel_manga.application.agents.episode.session import SKILL_SOURCE
    script = SKILL_SOURCE.parents[1]/'scripts/build_phase_cards.py'
    log = ctx.run_dir/'input-assets.log'
    if not names:
        return
    def build(name):
        # Each character keeps its own primary -> companion dependencies. One
        # slow card must not consume the entire other characters' time window.
        with log.open('a') as stream:
            result = subprocess.run([sys.executable, str(script), '--novel-dir', str(ctx.novel_dir),
                '--only', name, '--chapter', str(chapter_of(ctx.episode_dir)),
                '--expressions'], stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f'image helper exited {result.returncode}')
    failures = {}
    with ThreadPoolExecutor(max_workers=min(3, len(names))) as pool:
        pending = {pool.submit(build, name): name for name in sorted(names)}
        for future in as_completed(pending):
            try:
                future.result()
            except (OSError, ValueError) as error:
                failures[pending[future]] = str(error)
    if failures:
        detail = '; '.join(f'{name}: {reason}' for name, reason in sorted(failures.items()))
        raise ValueError(f'input phase images failed ({detail}); completed images retained; inspect {log.name}')


def copy_registered_assets(ctx, names):
    """Copy existing alternative scenes/props; do not redraw the book's library."""
    from .inputs import _catalog
    refs = []
    manifest = read_json(ctx.source_novel_dir/'series_assets/manifest.json', {})
    for group, role in (('characters', 'character'), ('locations', 'location'), ('props', 'prop')):
        for row in manifest.get(group, []):
            name = str(row.get('name') or '')
            if name not in names and name.split('：', 1)[0] not in names:
                continue
            for field in ('primary_image', 'secondary_image'):
                if row.get(field):
                    refs.append({'role': role, 'name': name.split('：', 1)[0] if role == 'location' else name,
                                 'asset_id': row['asset_id'], 'path': row[field]})
    _catalog(ctx, refs)


def refresh_inputs(ctx, plan, script):
    from .inputs import _catalog, _references
    refs, missing, _ = _catalog(ctx, list(_references(plan)))
    if missing:
        raise ValueError('repaired references missing: ' + ', '.join(str(r['path']) for r in missing))
    current = read_json(ctx.run_dir/'catalog.json', [])
    keyed = {(r['path'], r['role'], r['name']): r for r in current}
    keyed.update({(r['path'], r['role'], r['name']): r for r in refs})
    catalog = list(keyed.values())
    atomic_write_json(ctx.run_dir/'catalog.json', catalog)
    atomic_write_json(ctx.work_dir/'input/catalog.json', {'assets': catalog})
    atomic_write_json(ctx.work_dir/'input/clip_plan.json', plan)
    atomic_write_json(ctx.work_dir/'input/chapter_script.json', script)
    shutil.copy2(ctx.novel_dir/'story_bible.json', ctx.work_dir/'input/story_bible.json')
    phases = ctx.novel_dir/'series_assets/phases.json'
    if phases.is_file():
        shutil.copy2(phases, ctx.work_dir/'input/phases.json')


def repair(ctx, row, *, check_only=False):
    from novel_manga.application.repair.publication import publish_candidate

    directory = ctx.episode_dir
    validate_resume(row)
    if row.get('resume') is True:
        latest = latest_input_records(directory).get(row['clip_id'])
        saved = latest[1] if latest else {}
        if (not saved.get('decision') or not (saved.get('status') in {'checking', 'incomplete', 'published'}
                                             or source_review_recoverable(saved))):
            raise ValueError('no incomplete input repair is available to resume')
        row = {**saved['decision'], 'reason': row.get('reason') or saved.get('reason') or 'Resume saved input repair'}
    row = normalize_arguments(row)
    plan = read_json(directory/'clip_plan.json')
    script = read_json(directory/'chapter_script.json')
    row = expand_shared_scope(row, plan)
    cid = row['clip_id']
    target = next(c for c in plan['clips'] if c['clip_id'] == cid)
    scope = row.get('clip_ids')
    if scope is not None and (cid not in scope
            or any(key not in {c['clip_id'] for c in plan['clips']} for key in scope)):
        raise RepairArgumentError('clip_ids must include the target and only current episode clips')
    source = (ctx.run_dir/'source.txt').read_text(encoding='utf-8')
    quote = str(row.get('source_quote') or '').strip()
    if not quote or quote not in source:
        raise ValueError('input repair requires a verbatim source_quote from input/source.txt')
    previous_inputs = sorted((directory/'work/input_repairs').glob(f'{cid}-*.json'),
                             key=lambda p: p.stat().st_mtime)
    matching = next(((path, record) for path in reversed(previous_inputs)
                     if (record := read_json(path, {})).get('decision')
                     and (effects(record['decision']) == effects(row)
                          or (record.get('submitted_decision') and effects(record['submitted_decision']) == effects(row)))), None)
    if matching and matching[1].get('published'):
        # Publication already applied speaker/cast changes. Finish its bookkeeping
        # against the current plan, without replaying guards on the old identities.
        path, recorded = matching
        if check_only:return {'valid':True,'resume_published':True}
        refresh_inputs(ctx, plan, script)
        changed = recorded.get('changed', []) if recorded.get('status') != 'prepared' else []
        recorded.update(status='prepared', step='complete')
        recorded.pop('error', None)
        recorded.pop('error_kind', None)
        atomic_write_json(path, recorded)
        return {'changed': changed, 'accepted': [], 'input_repaired': [cid], 'blocked': {}, 'reused': True}
    addresses = set(target.get('shot_indexes') or [])
    media = read_json(directory/'thin_media_report.json', {})
    blocked_inputs = set(media.get('blocked_clips') or {}) | set(media.get('failed_clips') or [])
    for clip in plan['clips']:
        if clip['clip_id'] in blocked_inputs:
            addresses.update(clip.get('shot_indexes') or [])
    # A shared costume/location correction must be able to correct the source fields
    # in the same dependent clips that this operation will recompile.
    raw_phases = row.get('phases') or {}
    affected_people = set(raw_phases.get('characters') or {}) if isinstance(raw_phases, dict) else set()
    affected_locations = set(row.get('locations') or {})
    for clip in plan['clips']:
        if any((ref.get('role') == 'character' and ref.get('name') in affected_people)
               or (ref.get('role') == 'location' and ref.get('name') in affected_locations)
               for ref in clip.get('references', [])):
            addresses.update(clip.get('shot_indexes') or [])
    if scope is not None:
        addresses = {i for clip in plan['clips'] if clip['clip_id'] in scope for i in clip.get('shot_indexes', [])}
    prepared = prepare_candidate(ctx, row, plan, script, target, addresses, scope, matching)
    case = prepared.compilation
    row = case.row
    candidate_script = case.candidate_script
    shots = case.shots
    patches = case.patches
    targets = case.targets
    counts = case.counts
    phases = case.phases
    locations = case.locations
    characters = case.characters
    submitted_row = prepared.submitted_row
    chapter = prepared.chapter
    bible = prepared.bible
    base_ids = prepared.base_ids
    reused = prepared.reused
    if check_only:
        input_receipt(ctx,cid,row,check_only=True)
        return {'valid':True,'clip_ids':sorted(targets),'allowed_shot_indexes':sorted(addresses)}
    receipt, record = input_receipt(ctx, cid, row)
    if row != submitted_row:
        record['submitted_decision'] = submitted_row
    if record.get('status') in {'prepared', 'checked_existing_wearing'}:
        refresh_inputs(ctx, plan, script)
        return {'changed': [], 'accepted': [], 'input_repaired': [cid], 'blocked': {}, 'reused': True}
    if record.get('published'):
        refresh_inputs(ctx, plan, script)
        record.update(status='prepared', step='complete')
        atomic_write_json(receipt, record)
        return {'changed': record.get('changed', []), 'accepted': [], 'input_repaired': [cid], 'blocked': {}}
    phase_path = None
    old_phases = None
    bible_path = ctx.novel_dir/'story_bible.json'
    old_bible = bible_path.read_bytes()
    metadata_paths = [ctx.novel_dir/'entity/entities.json', ctx.work_dir/'input/story_bible.json']
    old_metadata = {path: path.read_bytes() if path.exists() else None for path in metadata_paths} if characters else {}
    published = False
    stage = 'source'
    def progress(step):
        nonlocal stage
        stage = step
        record.update(status='checking', step=step)
        record.pop('error', None)
        atomic_write_json(receipt, record)
    try:
        if not record.get('source'):
            record['source_review_policy'] = SOURCE_REVIEW_POLICY
            atomic_write_json(receipt, record)
            affected_indexes={i for c in plan['clips'] if c['clip_id'] in targets for i in c.get('shot_indexes',[])}
            scoped_shots = [shots[i] for i in sorted(affected_indexes)]
            record['source'] = source_check(source, quote, patches, phases, locations, scoped_shots,
                                            **({'characters': characters} if characters else {}))
            atomic_write_json(receipt, record)
        if characters:
            from .draft_assets import _asset
            from .input_assets import register_metadata
            progress('character_assets')
            assets = [_asset(ctx, 'character', name, design) for name, design in characters.items()]
            record['assets'] = [a['id'] for a in assets]
            atomic_write_json(receipt, record)
            register_metadata(ctx, assets)
            bible = read_json(bible_path)
        phase_path = local_phases(ctx) if phases or locations else None
        old_phases = phase_path.read_bytes() if phase_path and phase_path.exists() else None
        if phase_path:
            progress('assets')
            ledger = read_json(phase_path, {'policy': 'phase-cards-v1', 'characters': {}})
            ledger.setdefault('policy', 'phase-cards-v1')
            ledger.setdefault('characters', {})
            suffix = receipt.stem + (f"-resume{record['asset_revision']}" if record.get('asset_revision') else '')
            for name, entries in phases.items():
                phase = {**entries[0], 'from': chapter, 'to': chapter,
                         'asset_id': reused.get(name) or f'{base_ids[name]}-agent-{directory.name}-{suffix}'}
                ledger['characters'][name] = [phase, *[p for p in ledger['characters'].get(name, [])
                                                       if p.get('asset_id') != phase['asset_id']]]
            if locations:
                ledger.setdefault('locations', {})
                bible.setdefault('locations', [])
                for name, description in locations.items():
                    names_in_bible = [text.split('：', 1)[0].strip() for text in bible['locations']]
                    if name not in names_in_bible:
                        bible['locations'].append(name+'：'+description)
                        names_in_bible.append(name)
                    base = f'location_{names_in_bible.index(name)+1:03d}'
                    phase = {'from': chapter, 'to': chapter, 'label': name, 'description': description,
                             'base_asset': base, 'asset_id': f'{base}-agent-{directory.name}-{suffix}'}
                    ledger['locations'][name] = [phase, *[p for p in ledger['locations'].get(name, [])
                                                         if p.get('asset_id') != phase['asset_id']]]
                atomic_write_json(bible_path, bible)
            atomic_write_json(phase_path, ledger)
            if phases:
                copy_registered_assets(ctx, set(phases) | {p.get('wears') for es in phases.values() for p in es})
                build_phases(ctx, set(phases))
            if locations:
                from .input_assets import build_locations
                build_locations(ctx, set(locations))
        copy_registered_assets(ctx, {str(shots[i].get('location') or '') for i in addresses})
        compiled = compile_input(case, receipt, record, progress)
        if compiled.proposal is None:
            published = True
            return {'changed': [], 'accepted': [], 'input_repaired': [cid], 'blocked': {}}
        proposal, candidate, changed = compiled.proposal, compiled.plan, compiled.changed
        progress('publish')
        publish_candidate(directory, proposal, 'episode_agent_input', changed)
        published = True
        record.update(status='published', published=True, changed=changed)
        atomic_write_json(receipt, record)
        repair_history = history.load(directory)
        repair_history['trials'][-1]['managed'] = True
        history.save(directory, repair_history)
        progress('refresh')
        refresh_inputs(ctx, candidate, candidate_script)
        record.update(status='prepared', step='complete')
        atomic_write_json(receipt, record)
        return {'changed': changed, 'accepted': [], 'input_repaired': [cid], 'blocked': {}}
    except Exception as error:
        if isinstance(error,CompileConflict):record['error_details']=error.details
        if isinstance(error, UnsupportedInput) and hasattr(error, 'review'):
            record['source_review'] = error.review
        kind = ('source_rejected' if isinstance(error, UnsupportedInput) else
                'tool_error' if isinstance(error, (KeyError, AttributeError, TypeError)) else
                'service_interrupted' if isinstance(error, (OSError, TimeoutError, subprocess.TimeoutExpired)) else
                'input_validation')
        record.update(status='rejected' if kind == 'source_rejected' else
                      'tool_error' if kind == 'tool_error' else 'incomplete',
                      step=stage, error=f'{type(error).__name__}: {error}', error_kind=kind)
        atomic_write_json(receipt, record)
        raise
    finally:
        if phase_path and not published:
            if old_phases is None:
                phase_path.unlink(missing_ok=True)
            else:
                phase_path.write_bytes(old_phases)
        if not published and (locations or characters):
            bible_path.write_bytes(old_bible)
        if not published:
            for path, content in old_metadata.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
