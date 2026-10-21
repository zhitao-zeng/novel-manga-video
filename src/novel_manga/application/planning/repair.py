"""Hand native planning failures to the existing Agent, then validate normally."""
from __future__ import annotations

import json
from pathlib import Path
import time
import httpx

from novel_manga.application.agents.sandbox import SandboxRefused
from novel_manga.application.agents.episode.session import run_session, SESSION_FILE
from novel_manga.llm.resources import model_slot
from novel_manga.llm import usage
from novel_manga.util import atomic_write_json, read_json


def service_failure(note):
    return any(word in note for word in (
        'HTTPStatusError', 'ReadTimeout', 'ConnectError', 'IncompleteResponse', 'JSON truncated', 'ReviewIncomplete', 'NOVEL_MODEL_', 'model resource ', 'streamed error from '))


def same_proposal(left, right):
    if left == right:
        return True
    if (isinstance(left, dict) and isinstance(right, dict)
            and left.get('kind') == right.get('kind') and left.get('kind') in {'identity', 'identity_source'}
            and isinstance(left.get('correction'), dict) and isinstance(right.get('correction'), dict)):
        from novel_manga.application.identity.semantic_review import same_candidate
        a, b = left.get('correction') or {}, right.get('correction') or {}
        return (set(a.get('removed_source_ids') or []) == set(b.get('removed_source_ids') or [])
                and same_candidate(a, b))
    return False


def current_context(batch, chapter):
    from novel_manga.application.identity.store import load_book
    from novel_manga.application.identity.flow import reading_aliases
    from novel_manga.application.identity.semantic_review import binding_context
    from novel_manga.story.source_identity import clean_reading, source_actor_bindings
    directory = batch.episode_dir(chapter)
    segments = read_json(directory/'segments.json', [])
    book = load_book(directory.parent)
    answer = read_json(directory/'identity_source_reading.json', {}).get('answer') or {'actors': []}
    cleaned = clean_reading(answer, segments)[0]
    aliases = reading_aliases(directory.parent, chapter)
    bindings = source_actor_bindings(cleaned, book.catalog, aliases)
    mapped = {'mentions': [{'source_actor': sid, 'entity_id': eid} for sid, eid in sorted(bindings.items())]}
    identity = binding_context(directory, answer, book.catalog, segments, mapped)
    ids = {row['id'] for row in identity['catalogue']}
    names = {row['name'] for row in identity['catalogue']}
    reading = read_json(directory.parent/'reading_cast.json', {})
    source_text = '\n'.join(row['text'] for row in segments)
    candidate_text = json.dumps(read_json(directory/'planning_attempt.json', {}).get('candidate'), ensure_ascii=False)
    from novel_manga.story.reading_facts import FACTS_FIELD
    return {'segments': segments,
        'source': batch.chapter(chapter).source_text if hasattr(batch, 'chapter') else segments,
        'profile': read_json(directory.parent/'profile.json', {}),
        'notes': getattr(batch, 'notes', {}).get(str(chapter), getattr(batch, 'notes', {}).get('*')),
        'identity': identity,
        'bindings': mapped['mentions'],
        'source_actors': answer.get('actors', []),
        'designs': [dict(row, forms=sorted(row['forms'])) for eid, row in book.catalog.entities.items() if eid in ids],
        'types': {name: value for name, value in book.types.items() if name in names or name in ids},
        'phases': {name: value for name, value in book.catalog.phases.items() if name in names or name in ids},
        'locations': [row for row in book.catalog.bible.get('locations', [])
                      if str(row).split('：', 1)[0] in source_text+candidate_text],
        'source_facts': {name: value for name, value in (reading.get(FACTS_FIELD) or {}).items() if name in names}}


def resume_failure(batch, chapter):
    """Use a saved failure before buying another ordinary authoring invocation."""
    directory = batch.episode_dir(chapter)
    if (directory/'clip_plan.json').is_file():
        return False
    from .retention import POLICY
    pending = read_json(directory/'planning_attempt.json', {})
    review = pending.get('chapter_review') or {}
    segments = read_json(directory/'segments.json', [])
    stored = (pending.get('planning_input') or {}).get('segments') or []
    same_source = (bool(stored) and [(s.get('segment_id'),s.get('text')) for s in stored]
                   == [(s.get('segment_id'),s.get('text')) for s in segments])
    if (same_source and isinstance(pending.get('candidate'),dict) and isinstance(pending.get('outline'),dict)
            and (review.get('policy') != POLICY or review.get('verdict') == 'incomplete')):
        # A corrected review contract may accept the existing draft. Validate it
        # normally before asking its original Agent to edit; do not renew that
        # Agent's activity or buy another initial authoring attempt.
        candidate = directory/'planning-review-candidate.json'
        atomic_write_json(candidate, {'draft':pending['candidate'],'outline':pending['outline']})
        batch.plan(chapter,candidate=candidate)
        return True
    saved = read_json(directory/'planning-repair.json', {})
    if saved:
        applicable = saved.get('context') == current_context(batch, chapter)
        failure = saved.get('inputs', {}).get('failure', '')
    else:
        failed = read_json(directory/'planning_failed.json', {})
        failure = ' | '.join(failed.get('errors') or [])
        applicable = bool(failed) and not service_failure(failure)
    if applicable:
        batch.rows[chapter].update(plan='failed', note=failure)
    return applicable


def failure_inputs(batch, chapter):
    directory = batch.episode_dir(chapter)
    review = read_json(directory/'identity_binding_review.json', {})
    source = read_json(directory/'identity_source_reading.json', {})
    from novel_manga.story.source_identity import clean_reading
    segments = read_json(directory/'segments.json', [])
    cleaned, _, unresolved = clean_reading(source.get('answer') or {'actors': []}, segments)
    from novel_manga.application.identity.store import load_book
    from novel_manga.application.identity.flow import reading_aliases
    from novel_manga.story.source_identity import map_source_reading
    source_invalid = False
    try:
        map_source_reading(cleaned, load_book(directory.parent).catalog, segments, reading_aliases(directory.parent, chapter))
    except ValueError:
        source_invalid = True
    source_failure = ('identity actors lack grounded names' in batch.rows[chapter].get('note', '')
                      or source_invalid or any(row.get('presence') in {'on_stage', 'voice'} for row in unresolved))
    identity = not source_failure and bool(review.get('inputs') and (
        not review.get('approved') or batch.rows[chapter].get('repair_stage') == 'identity'))
    current = read_json(directory/'planning_attempt.json', {})
    if not current:
        attempts = read_json(directory/'planning_failed.json', {}).get('attempts') or []
        latest = attempts[-1] if attempts else {}
        number = latest.get('attempt')
        if isinstance(number, int):
            current = {'planning_input': read_json(directory/f'request_attempt_{number:02d}.json', {})}
            if latest.get('stage') != 'outline':
                current.update(candidate=read_json(directory/f'response_attempt_{number:02d}.raw.json', None),
                               outline=read_json(directory/f'analysis_attempt_{number:02d}.txt', None))
    steps = review.get('steps') or []
    latest = next((s for s in reversed(steps) if s.get('kind') == 'review'), {})
    identity_inputs = review.get('inputs')
    if identity and latest.get('candidate') is not None:
        from novel_manga.application.identity.semantic_review import candidate_view
        identity_inputs = {**identity_inputs, 'candidate': candidate_view(latest['candidate'])}
    planning_failure = read_json(directory/'planning_failed.json', {})
    for attempt in planning_failure.get('attempts', []):
        # Preserve unresolved evidence on disk, without turning it into a request
        # to rewrite content. Only confirmed errors are repair targets.
        attempt.pop('review_unfinished', None)
    return {'kind': 'identity_source' if source_failure else 'identity' if identity else 'draft',
        'source_segments': read_json(directory/'segments.json', []),
        'failure': batch.rows[chapter].get('note', ''),
        'planning_failure': planning_failure,
        'identity_inputs': identity_inputs if identity else None,
        'source_reading': source if source_failure else None,
        'evidence_repair': read_json(directory/'identity_evidence_repair.json', {}) if source_failure else None,
        'last_judgment': next((s.get('answer') for s in reversed(steps) if s.get('kind') == 'review'), None),
        'validation_error': next((s.get('error') for s in reversed(steps) if s.get('kind') == 'validation_error'), None),
        'candidate': current.get('candidate') if not identity and not source_failure else None,
        'outline': current.get('outline') if not identity and not source_failure else None,
        'planning_input': current.get('planning_input') if not identity and not source_failure else None}


def repair_chapter(batch, chapter, *, config, existing=False, record_path=None):
    directory = batch.episode_dir(chapter)
    if (directory/'clip_plan.json').is_file() and not existing:
        return {'status': 'kept'}
    note = batch.rows[chapter].get('note', '')
    if batch.planning_stop.is_set() or service_failure(note):
        return {'status': 'service_blocked', 'reason': note}
    record_path = Path(record_path) if record_path is not None else directory/'planning-repair.json'
    saved = read_json(record_path, {})
    context = current_context(batch, chapter)
    inputs = failure_inputs(batch, chapter)
    same = (saved.get('context') == context
            and saved.get('inputs', {}).get('kind') == inputs['kind']
            and (not existing or saved.get('inputs', {}).get('failure') == note))
    if same:
        inputs = saved['inputs']
    if not inputs['source_segments']:
        return {'status': 'held', 'reason': 'Original source segments are missing'}
    if same and saved.get('status') == 'held' and (
            'feedback' in saved or (saved.get('proposal') or {}).get('kind') not in {'draft', 'identity', 'identity_source'}):
        return saved['result']
    run = f'{batch.novel_id}-planning-{chapter:04d}'
    folder = Path(config['runs_root'])/run
    initial = saved.get('initial_seconds', config.get('timeout_seconds', 5400))
    used = saved.get('used_seconds', 0)
    if saved and not same:
        with record_path.with_suffix('.history.jsonl').open('a') as history:
            history.write(json.dumps(saved, ensure_ascii=False)+'\n')
    record = {**(saved if same else {}), 'context': context, 'inputs': inputs,
              'initial_seconds': initial, 'used_seconds': used}
    if existing or isinstance(inputs.get('candidate'), dict):
        record.setdefault('repair_target', inputs.get('failure', note))
    proposal = saved.get('proposal') if same else None

    def save(status, **fields):
        record.update(status=status, **fields)
        atomic_write_json(record_path, record)

    feedback = saved.get('feedback') if same else None
    if same and saved.get('status') == 'held':
        feedback = {'reason': saved.get('result', {}).get('reason', inputs['failure']),
                    'proposal': proposal}
        proposal = None
    previous = feedback.get('proposal') if feedback else None
    prompt = (
        '这是正常分镜规划失败后的局部修复任务。只读 input/planning-failure.json 中的原文、当前候选和具体失败。'
        '使用现有 episode-production 技能的原文归属与修订原则；本任务只交付规划修订，不生成图片、H3请求或视频。'
        '不要补全全书、修改原文、写入通过状态或重置预算。只修会影响剧情理解、实际演员、对白或身体数量的问题。'
        '只修改错误定位的镜头和必要联动，其余镜头与合理改编保留。完整JSON仅为交付格式，不是重写整章。'
        '原稿缺少后续关键剧情时，保留已有正确镜头并追加缺失部分；局部修复不限制为修改已有镜号。'
        '交付完整的当前候选，不能用“未随本次提交”替代明确要求补齐的后续剧情。'
        '写 output/repair.json，JSON格式如下：'
        'kind=identity时，返回 {"kind":"identity","correction":{"actors":[仅修改条目],'
        '"bindings":[仅修改条目],"removed_source_ids":[]}}；source_id 保持，指称和编号必须有当前原文依据。'
        'kind=identity_source时，返回 {"kind":"identity_source","correction":{"actors":[仅修订的原文主体]}}，'
        '修正source_reading和evidence_repair暴露的指称/段落错误，保留source_id，宿主仍检查原文归属并复审绑定。'
        'kind=draft时返回 {"kind":"draft","draft":完整修订后的原生分镜JSON,"outline":对应提纲对象}，沿用planning_input约束。'
        '宿主会走原源文/身份审核和原生规划采用；你的输出只是待审候选。无法修复则写具体原因，不伪造结果。\n')

    def finish(status, reason=''):
        result = {'status': status, 'reason': reason, 'run': run}
        save(status, result=result, context=current_context(batch, chapter))
        return result

    while True:
        try:
            if batch.planning_stop.is_set():
                return finish('service_blocked', 'Phanthy credits exhausted')
            if proposal is None:
                if record.get('status') == 'agent_running':
                    return finish('held', 'Previous Agent attempt has no completed receipt; retain its budget and inspect that attempt')
                # A sparse repair continues the last checked draft, including
                # earlier native corrections that have not yet been adopted.
                inputs = failure_inputs(batch, chapter)
                record['inputs'] = inputs
                remaining = initial-used
                if int(remaining) <= 0:
                    return finish('held', 'Original planning repair activity budget exhausted')
                agent_inputs = inputs
                if 'repair_target' in record:
                    agent_inputs = {**inputs, 'repair_target': record['repair_target']}
                edit_saved_draft = inputs['kind'] == 'draft' and isinstance(inputs.get('candidate'), dict)
                if edit_saved_draft:
                    # The Agent edits the saved draft, rather than copying a long
                    # JSON document and accidentally dropping unchanged scenes.
                    atomic_write_json(folder/'output/repair.json', {'kind': 'draft',
                        'draft': inputs['candidate'], 'outline': inputs['outline']})
                    agent_inputs = {k: v for k, v in agent_inputs.items()
                                    if k not in {'candidate', 'outline', 'planning_input'}}
                    agent_inputs.update(candidate_file='output/repair.json',
                                        profile=getattr(batch, 'profile', {}))
                    current_request = inputs.get('planning_input') or {}
                    for key in ('available_characters', 'anonymous_offscreen_speakers'):
                        if key in current_request:
                            agent_inputs[key] = current_request[key]
                atomic_write_json(folder/'input/planning-failure.json', agent_inputs)
                atomic_write_json(folder/'input/current-task.json', {'mode': 'planning_repair',
                    'phase': 'planning', 'source': 'input/planning-failure.json', 'expected_output': 'output/repair.json'})
                atomic_write_json(folder/'input/manifest.json', {'production_mode': 'planning',
                    'input_mode': inputs['kind'], 'media_generation': False})
                current_prompt = prompt
                if edit_saved_draft:
                    current_prompt += ('宿主已把当前完整候选放在 output/repair.json。读取后直接用Edit局部修改该文件，'
                        '不要重抄整份JSON，不要改input目录。原稿其余内容保留；历史写作请求不再是本轮改写要求。')
                if feedback:
                    atomic_write_json(folder/'input/planning-feedback.json', feedback)
                    current_prompt += ('上次候选未通过宿主检查。先读 input/planning-feedback.json 的具体错误和原候选，'
                        '在同一会话内继续修订；input/planning-failure.json 是当前失败阶段和实际材料。'
                        '修正后重写 output/repair.json，不原样重交，不申请或重置额度。')
                (folder/'prompt.txt').write_text(current_prompt, encoding='utf-8')
                save('waiting_model', run=run, feedback=feedback)
                with usage.scope(novel=batch.novel_id, episode=chapter, stage='planning_repair'), model_slot(
                        config['base_url'], cancelled=batch.planning_stop.is_set):
                    # Waiting for a model slot is not Agent activity.
                    remaining = initial-used
                    save('agent_running', started_at=time.time())
                    began = time.monotonic()
                    try:
                        attempt = run_session(folder, current_prompt, config=config, timeout=int(remaining),
                            resume=(folder/SESSION_FILE).is_file(), expected_output='repair.json', enable_tools=False)
                        from novel_manga.llm.native_usage import record_attempt
                        record['usage'] = record_attempt(attempt.directory, run_dir=folder,
                            model=config['model'], url=config['base_url'])
                    finally:
                        used += time.monotonic()-began
                        record['used_seconds'] = used
                        atomic_write_json(record_path, record)
                record['attempt'] = str(attempt.directory)
                if attempt.exit_code != 0:
                    diagnostics = '\n'.join((attempt.directory/name).read_text(errors='replace')[-32000:]
                        for name in ('stderr.log', 'session.jsonl') if (attempt.directory/name).is_file())
                    if '402' in diagnostics and ('credit' in diagnostics.lower() or 'Payment Required' in diagnostics):
                        batch.planning_stop.set()
                        return finish('service_blocked', 'Phanthy credits exhausted')
                    return finish('service_blocked', 'Planning repair Agent did not finish: '+str(attempt.exit_code))
                if 'repair.json' not in attempt.produced:
                    return finish('held', 'Planning repair Agent produced no new correction')
                proposal = read_json(folder/'output/repair.json', {})
                if previous is not None and same_proposal(proposal, previous):
                    return finish('held', 'Agent repeated the rejected candidate without a correction')
                save('candidate', proposal=proposal)
            if proposal.get('kind') != inputs['kind']:
                raise ValueError('Agent proposal does not match the failed planning stage')
            if inputs['kind'] in {'identity', 'identity_source'}:
                from novel_manga.application.identity.flow import resolve_chapter
                if not record.get('identity_adopted'):
                    with usage.scope(novel=batch.novel_id, episode=chapter, stage='planning_repair_validation'):
                        if inputs['kind'] == 'identity_source':
                            resolve_chapter(directory, source_proposal={'inputs': inputs['source_reading'],
                                                                       'correction': proposal['correction']})
                        else:
                            resolve_chapter(directory, proposal={'inputs': inputs['identity_inputs'],
                                                                 'correction': proposal['correction']})
                    save('validated', identity_adopted=True)
                batch.rows[chapter].pop('repair_stage', None)
                pending = read_json(directory/'planning_attempt.json', {})
                if existing or isinstance(pending.get('candidate'), dict):
                    if not isinstance(pending.get('candidate'), dict) or not isinstance(pending.get('outline'), dict):
                        return finish('held', 'Existing draft or outline missing; do not restart authoring')
                    path = directory/'planning-repair-draft.json'
                    atomic_write_json(path, {'draft': pending['candidate'], 'outline': pending['outline']})
                    batch.plan(chapter, candidate=path)
                else:
                    batch.plan(chapter)
            else:
                path = directory/'planning-repair-draft.json'
                atomic_write_json(path, {'draft': proposal['draft'], 'outline': proposal.get('outline', inputs['outline'])})
                batch.plan(chapter, candidate=path)
            if (directory/'clip_plan.json').is_file() and (not existing or batch.rows[chapter].get('plan') == 'planned'):
                batch.rows[chapter].pop('note', None)
                return finish('planned')
            reason = batch.rows[chapter].get('note') or 'Normal planner produced no clip plan'
            if service_failure(reason) or batch.planning_stop.is_set():
                return finish('service_blocked', reason)
            raise ValueError(reason)
        except Exception as error:
            if getattr(getattr(error, 'response', None), 'status_code', None) == 402:
                batch.planning_stop.set()
            if isinstance(error, (httpx.HTTPError, TimeoutError, SandboxRefused, InterruptedError)) or service_failure(str(error)):
                return finish('service_blocked', f'{type(error).__name__}: {error}')
            reason = f'{type(error).__name__}: {error}'
            batch.rows[chapter]['note'] = reason
            feedback = {'reason': reason, 'proposal': proposal}
            record.setdefault('checks', []).append(feedback)
            # Preserve each checked candidate; the next turn receives the current
            # failed stage (identity may have succeeded before drafting failed).
            inputs = failure_inputs(batch, chapter)
            previous = proposal
            proposal = None
            record.pop('identity_adopted', None)
            save('feedback', feedback=feedback, proposal=None, inputs=inputs,
                 context=current_context(batch, chapter))
