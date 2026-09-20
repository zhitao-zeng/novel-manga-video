"""Full source → written scenes → method-specific shots → grounded review.

No media submission, production IO or task scheduling. The owning chapter flow
persists the returned screenplay, directions and request evidence.
"""
from __future__ import annotations

import copy
import json
import time
import httpx

from novel_manga.planning.methods.scenes import (
    screenplay_schema, screenplay_prompt, screenplay_input, normalize_screenplay, for_direction,
    scene_revision_schema, apply_scene_revision, SCENE_REVISION_PROMPT,
)
from novel_manga.planning.budget import directed_duration_problem
from novel_manga.planning.methods.direction import (
    direction_schema, direction_prompt, project_direction, review_schema, REVIEW_PROMPT, SCRIPT_REVIEW_PROMPT, grounded_review,
    direction_budget, DirectionIssues,
)


def generate(*, method, payload, model, endpoints, headers, post, max_tokens, scene_tokens,
             timeout, seed, notes, ctx, scene_script=None):
    from novel_manga.planning.methods.errors import IncompleteOutlineError
    start = time.monotonic()
    ctx.method_artifacts = {}
    ctx.story_blueprint = {}
    records = []
    shared = screenplay_input(payload, notes)
    direction_context = {k: v for k, v in shared.items() if k not in
                         {'segments', 'quoted_lines_that_must_be_kept', 'identity_context',
                          'previous_volumes_recap', 'previous_chapters_recap', 'previous_episode_ending'}}

    with httpx.Client(timeout=timeout, trust_env=False) as client:
        def ask(name, instruction, data, schema, budget, normalize, *, attempts=2):
            previous, errors, cause = None, [], None
            # One bounded correction of malformed/incomplete output, with the
            # actual draft attached. There is no unbounded "quality" reroll.
            for attempt in range(attempts):
                body = {'model': model, 'temperature': 0.3, 'max_tokens': budget,
                        **({'seed': seed} if seed is not None else {}),
                        'chat_template_kwargs': {'enable_thinking': False},
                        'response_format': {'type': 'json_schema', 'json_schema':
                            {'name': name, 'strict': True, 'schema': schema}},
                        'messages': [{'role': 'system', 'content': instruction},
                                     {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]}
                if attempt:
                    body['messages'].append({'role': 'user', 'content': '修正这些交接错误，保持其余内容：'
                        + '; '.join(errors) + '\n待修稿：' + str(previous or '')})
                token = f'{len(records)+1:02d}_{name}'
                elapsed = time.monotonic()
                ctx.method_artifacts[token + '_request'] = copy.deepcopy(body)
                response = post(client, endpoints, headers, body)
                ctx.method_artifacts[token + '_response'] = copy.deepcopy(response)
                choice = response['choices'][0]
                previous = choice['message'].get('content') or ''
                row = {'stage': name, 'attempt': attempt+1, 'finish_reason': choice.get('finish_reason'),
                       'usage': response.get('usage', {}), 'seconds': round(time.monotonic()-elapsed, 2)}
                records.append(row)
                try:
                    if choice.get('finish_reason') != 'stop':
                        raise ValueError('模型没有完整输出：' + str(choice.get('finish_reason')))
                    result = normalize(json.loads(previous))
                except (ValueError, KeyError, TypeError) as error:
                    cause = error
                    errors = [str(error)]
                    row['errors'] = errors
                    continue
                row['errors'] = []
                return result
            raise IncompleteOutlineError(records, cause=cause)

        def write_scenes(correction=None):
            return ask('scene_screenplay', screenplay_prompt(method),
                       shared if correction is None else {**shared, 'revision': correction},
                       screenplay_schema(payload), scene_tokens,
                       lambda result: normalize_screenplay(result, payload, method))

        def revise_scenes(script, findings):
            if any(i['scene_id'] == 'episode' for i in findings):
                return write_scenes({'issues': findings, 'screenplay': script})
            selected = {i['scene_id'] for i in findings}
            return ask('scene_screenplay_revision', SCENE_REVISION_PROMPT,
                       {**shared, 'screenplay': script, 'replace_scene_ids': sorted(selected), 'issues': findings},
                       scene_revision_schema(payload, selected), scene_tokens,
                       lambda data: apply_scene_revision(script, data, selected, payload, method))

        def direct(script, correction=None, selected=None, base=None):
            draft = None
            def normalize(data):
                nonlocal draft
                candidate = copy.deepcopy(data)
                if selected is not None:
                    if set(candidate['directions']) != selected:
                        raise ValueError('局部修订只能输出已点名场景，且不能遗漏')
                    candidate = {**base, 'directions': {**base['directions'], **candidate['directions']}}
                draft = candidate
                projected = project_direction(script, candidate)
                seconds = sum(s['duration_seconds'] for c in projected['clips'] for s in c['stages'])
                maximum = payload['planning_budget'].get('episode_max_seconds')
                problem = directed_duration_problem(seconds, maximum)
                if problem:
                    raise ValueError(problem)
                return candidate
            # A calculable error in one scene must not regenerate the other
            # scenes. Malformed JSON or a whole-episode budget issue has no
            # usable local scope and retains one bounded full correction.
            for attempt in range(2):
                data = {**direction_context, 'screenplay': for_direction(script),
                        'dialogue_budget': direction_budget(script),
                        **({'revision': correction} if correction else {})}
                if selected is not None:
                    data['replace_scene_ids'] = sorted(selected)
                instruction = direction_prompt(method)
                if selected is not None:
                    instruction += '本次只返回replace_scene_ids的导演稿，其余场景由程序原样保留；按revision纠错并使用dialogue_budget。'
                try:
                    return ask('scene_direction', instruction, data,
                               direction_schema(script, payload, method, scene_ids=selected),
                               max_tokens, normalize, attempts=1)
                except IncompleteOutlineError as error:
                    if attempt:
                        raise
                    problems = records[-1]['errors']
                    if isinstance(error.cause, DirectionIssues) and draft is not None:
                        selected = error.cause.scene_ids & {s['scene_id'] for s in script['scenes']}
                        selected = selected or None
                        base = draft
                        problems = error.cause.problems
                    correction = {'issues': problems, 'director': draft}

        def review(script, direction=None):
            part = 'script' if direction is None else 'shots'
            retained = {}
            def normalize(data):
                problems = []
                for item in data['issues']:
                    try:
                        bound = grounded_review({'issues': [item]}, script, direction, payload, part)['issues'][0]
                    except ValueError:
                        problems.append(f"{item.get('item_id')}: 编号不在当前材料中，请只修编号，不撤掉其他有依据的问题")
                    else:
                        retained[(bound['item_id'], bound['upstream_id'])] = bound
                if problems:
                    raise ValueError('; '.join(problems))
                # A citation-format retry is not a fresh review of a changed
                # draft. It cannot erase already grounded findings with [].
                return {'issues': list(retained.values())}
            data = ({**shared, 'screenplay': {k: v for k, v in script.items() if k != 'source_segments'}}
                    if direction is None else {**direction_context,
                        'screenplay': for_direction(script),
                        'bound_shots': project_direction(script, direction)['clips']})
            return ask('screenplay_review' if direction is None else 'scene_review',
                       SCRIPT_REVIEW_PROMPT if direction is None else REVIEW_PROMPT,
                       data, review_schema(script, part, direction),
                       3500, normalize)

        script = normalize_screenplay(scene_script, payload, method) if scene_script is not None else write_scenes()
        if scene_script is not None:
            ctx.method_artifacts['scene_script_reused'] = copy.deepcopy(script)
        script_verdict = review(script)
        if script_verdict['issues']:
            script = revise_scenes(script, script_verdict['issues'])
            script_verdict = review(script)
        ctx.method_artifacts['scene_screenplay_candidate'] = copy.deepcopy(script)
        if script_verdict['issues']:
            records.append({'stage': 'screenplay_review', 'errors': [i['issue'] for i in script_verdict['issues']]})
            raise IncompleteOutlineError(records)
        direction = direct(script)
        verdict = review(script, direction)
        first_verdict = copy.deepcopy(verdict)
        if verdict['issues']:
            correction = {'issues': verdict['issues'], 'screenplay': for_direction(script), 'director': direction}
            selected = None if any(i['scene_id'] == 'episode' for i in verdict['issues']) else {i['scene_id'] for i in verdict['issues']}
            direction = direct(script, correction, selected, base=direction)
            verdict = review(script, direction)
        raw = project_direction(script, direction)
        ctx.story_blueprint = {**script, 'direction': direction,
                               'script_review': {**script_verdict, 'completed': True},
                               'review': {**verdict, 'completed': True}, 'initial_review': first_verdict}
    analysis = json.dumps(ctx.story_blueprint, ensure_ascii=False)
    totals = {key: sum((r.get('usage') or {}).get(key, 0) or 0 for r in records)
              for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return json.dumps(raw, ensure_ascii=False), {
        'finish_reason': 'stop', 'usage': totals, 'method_stages': records,
        'analysis': analysis, 'outline_complete': True, 'outline_mode': 'scenes:' + method.key,
        'story_method': {**method.describe(), 'workflow': script['version']},
        'semantic_quality_review': 'passed' if not verdict['issues'] else 'needs_revision',
        'scene_script_reused': scene_script is not None,
        'seed': seed, 'seconds': round(time.monotonic()-start, 2),
    }
