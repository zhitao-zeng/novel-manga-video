"""Resolve a failed candidate against actual cards and current frames before spending a retake."""
from __future__ import annotations
import copy
import json

from novel_manga.llm import client
from novel_manga.application.review import cast_video, evidence
from novel_manga.review.prompts import shot_contract, story_block
from novel_manga.review.contracts import WATCHABLE_CRITERIA

POLICY = 'visual-adjudication-v7-movie-significance'
ERROR_FACTS = {
    'scene_missing': '剧情所需的具体场所整体消失或明显错位；装饰色差、裁切或虚化不算',
    'same_person_twice': '同一具体人物同时出现两个独立身体；衣服错、换装、独立空甲都不能证明分身',
    'extra_person': '出现本镜不允许的额外人物，但不能确定是同一人物复制。本镜明确允许的回忆、梦境或投影形象也属于允许的画面内容；不在现实在场名单或轮廓较清晰不能单独证明多出真实人物。仍须按原文和本镜事件判断身份、行为及是否影响剧情，不能借回忆标签放行未获允许的人物或分身',
    'extra_object': '独立物件数量超出本镜要求，不把穿戴物另算一个身体',
    'appearance': '外观差异改变了人物身份或具体剧情功能；普通服装、配饰、配色差异不属于阻塞错误',
    'state': '开合或动作状态差异改变了关键剧情事件或人物处境；不影响剧情含义的站坐、动作幅度、非关键小动作省略和道具局部形状差异只记提示，不能仅凭请求明确就确认错误',
    'visual_defects': '异常身体结构、附肢、变形或计划没有要求的明显视觉异常',
    'action_by_wrong_person': '应由甲完成的动作实际由乙完成',
    'actor_missing': '本镜明确应可见的人物缺席，构图裁切不算',
    'lead_face_swapped': '脸部身份确实变成另一个人，衣着错误不算换脸',
    'species_or_gender_wrong': '原文明示的物种或性别与可见画面不符',
}
SCHEMA = client.obj({
    'observations': {'type': 'array', 'items': {'type': 'string'}},
    'checks': {'type': 'array', 'minItems': 1, 'items': client.obj({
        'id': {'type': 'integer'}, 'evidence': {'type': 'string', 'maxLength': 180},
        'result': {'type': 'string', 'enum': ['confirmed', 'dismissed', 'uncertain']},
        'error_facts': client.obj({k: {'type': 'boolean', 'description': v} for k, v in ERROR_FACTS.items()}),
        'instruction': {'type': 'string', 'maxLength': 180}})}})


def apply(verdict, answer, concerns):
    answer = copy.deepcopy(answer)
    checks = answer.get('checks') or []
    if {r.get('id') for r in checks} != set(range(len(concerns))) or len(checks) != len(concerns):
        raise ValueError('visual adjudication did not answer every candidate issue exactly once')
    unresolved = any(r['result'] == 'uncertain' for r in checks)
    confirmed = [r for r in checks if r['result'] == 'confirmed']
    for row in checks:
        facts = row.get('error_facts') or {}
        required = set(ERROR_FACTS)
        # Existing cached judgments predate scene evidence; keep their confirmed facts.
        if not verdict.get('location_issue') and 'scene_missing' not in (verdict.get('verify') or {}):
            required.discard('scene_missing')
        if row['result'] == 'confirmed' and (not required.issubset(facts) or set(facts) - set(ERROR_FACTS)
                or any(type(v) is not bool for v in facts.values())):
            raise ValueError('confirmed visual issue requires explicit observed error facts')
        row['kinds'] = [k for k in ERROR_FACTS if facts.get(k) is True] if row['result'] == 'confirmed' else []
        if row['result'] == 'confirmed' and not row['kinds']:
            raise ValueError('confirmed issue has no observed error or request conflict')
    kinds = sorted({kind for r in confirmed for kind in r.get('kinds', [])})
    detail = '；'.join(r['evidence'] for r in confirmed)
    instruction = '；'.join(dict.fromkeys(r['instruction'] for r in confirmed))
    out = copy.deepcopy(verdict)
    # Retain the original evidence, but none of its dismissed flags can still trigger a retake.
    old = out.get('verify') or {}
    out['verify'] = {'verdict': 'obvious' if confirmed else 'fine',
                     'evidence': detail, 'instruction': instruction, 'error_kinds': kinds,
                     'request_conflict': False,
                     'cast_video': {**(old.get('cast_video') or {}), 'adjudicated': True},
                     'adjudication': {**answer, 'policy': POLICY, 'concerns': concerns, 'confirmed': bool(confirmed)},
                     'candidate': {k: v for k, v in old.items() if k != 'cast_video'}}
    out.update(severity='review_error' if unresolved else 'fail' if confirmed else 'pass',
               identity_ok=not bool(confirmed), identity_issue=detail,
               visual_defects=None, defect_issue='', story_ok=None, story_kind='无法判断', story_issue='',
               feedback=instruction, scripted=False)
    if 'scene_missing' in old or verdict.get('location_issue') or 'scene_missing' in kinds:
        scene_checks = [r for r in confirmed if 'scene_missing' in r['kinds']]
        out['verify']['scene_missing'] = bool(scene_checks)
        out['location_ok'] = False if scene_checks else None if unresolved else True
        out['location_issue'] = '；'.join(r['evidence'] for r in scene_checks)
        other_checks = [r for r in confirmed if set(r['kinds']) - {'scene_missing'}]
        out['identity_ok'] = not bool(other_checks)
        out['identity_issue'] = '；'.join(r['evidence'] for r in other_checks)
    if unresolved:
        out['error'] = 'visual evidence is insufficient to resolve every candidate issue'
    else:
        out.pop('error', None)
    return out


def pending_cast_note(verdict):
    """The observer's free-text finding must not disappear behind empty boolean flags."""
    cast = (verdict.get('verify') or {}).get('cast_video') or {}
    if cast.get('adjudicated'):
        return ''
    note = str(cast.get('note') or '').strip()
    # The cast question explicitly uses 无 for no issue. Any other reported
    # observation is a candidate to verify, never an automatic failure.
    return '' if note.rstrip('。.!！').strip().lower() in {'', '无', 'none'} else note


def outdated(verdict):
    record = (verdict.get('verify') or {}).get('adjudication') or {}
    return bool(pending_cast_note(verdict) or (record.get('confirmed') and record.get('policy') != POLICY))


def check_inputs(clip, work_dir, verdict, *, video=None, bible=None):
    """Retain input defects; current delivered-source acceptance is distinct from generation readiness."""
    if not clip.get('prompt_h3') or not any(r.get('role') != 'voice' for r in clip.get('references', [])):
        return verdict
    previous = verdict.get('verify') or {}
    record = previous.get('adjudication') or {}
    checks = record.get('checks') or []
    visual = apply(verdict, record, record['concerns']) if checks else copy.deepcopy(verdict)
    kinds = set((visual.get('verify') or {}).get('error_kinds') or [])
    # The clip observer can prove a detail differs; the current full-movie
    # judgment determines whether that difference actually changes the story.
    if (visual.get('severity') in {'pass','minor'} or
            (visual.get('severity') == 'fail' and kinds and kinds <= {'appearance','state'}
             and not visual.get('confirmed'))):
        from . import final_cut
        from novel_manga.util import read_json
        directory=work_dir.parents[2]
        media=read_json(directory/'thin_media_report.json',{})
        final=read_json(directory/'final_cut_review.json',{})
        if (final_cut.current(directory,media,final) and final.get('complete') is True
                and final.get('passed') is True and not final.get('issues') and not final.get('errors')):
            if visual.get('severity') == 'fail':
                visual.update(severity='minor',identity_ok=True,story_ok=True,feedback='')
                visual['verify'].update(verdict='subtle',instruction='')
            if previous.get('request_check'):
                visual['verify']['request_check']=copy.deepcopy(previous['request_check'])
                visual['verify']['request_conflict']=previous.get('request_conflict',False)
                if previous['request_check'].get('consistent') is not True:
                    visual['verify']['input_warning']='；'.join(previous['request_check'].get('problems') or ['input readiness remains separate'])
            visual.setdefault('verify',{})['movie_significance']={
                'movie':final['movie'],'policy':final['policy'],
                'result':'current approved-source movie is understandable; local observations retained'}
            return visual
    from novel_manga.application.preparation.request_check import current_request, RequestCheckIncomplete
    try:
        checked = current_request(work_dir.parents[2], clip)
    except RequestCheckIncomplete as error:
        out = copy.deepcopy(verdict)
        out.setdefault('verify', {})['request_check'] = error.result
        out.update(severity='review_error', error=f'{type(error).__name__}: {error}')
        return out
    previous = verdict.get('verify') or {}
    record = previous.get('adjudication') or {}
    old_check = previous.get('request_check') or {}
    checks = record.get('checks') or []
    released = (previous.get('request_conflict') is True
                and (old_check.get('consistent') is False or bool(old_check.get('problems')))
                and checked.get('consistent') is True and not checked.get('problems'))
    # An old frame reviewer may have excused visible wrong clothes because they matched the
    # disputed request. Narrowing the input gate cannot turn that dismissal into acceptance.
    # Recheck only those previously dismissed candidates, on this same take, with real frames.
    if released and any(row.get('result') == 'dismissed' for row in checks) and not any(
            row.get('result') == 'confirmed' for row in checks):
        if video is None or bible is None:
            raise ValueError('retiring an input blocker requires current frame adjudication')
        out = _review_frames(clip, video, bible, work_dir, verdict, record['concerns'])
    else:
        out = apply(verdict, record, record['concerns']) if checks else copy.deepcopy(verdict)
    # Reconstruct the visual decision before applying the current input scope. This retires an
    # old false input blocker without clearing a confirmed visual defect on the same take.
    out['verify']['request_check'] = checked
    if checked.get('consistent') is not True or checked.get('problems'):
        detail = '；'.join(checked.get('problems') or ['拍摄请求与实际参考图不一致'])
        # A request/card mismatch is not a movie defect when both the current
        # visual adjudication and current approved-source movie review passed.
        # This never approves those inputs for another generation.
        if out.get('severity') in {'pass', 'minor'}:
            from . import final_cut
            from novel_manga.util import read_json
            directory = work_dir.parents[2]
            media = read_json(directory/'thin_media_report.json', {})
            final = read_json(directory/'final_cut_review.json', {})
            if (final_cut.current(directory, media, final) and final.get('complete') is True
                    and final.get('passed') is True and not final.get('issues') and not final.get('errors')):
                out['verify'].update(request_conflict=True, input_warning=detail)
                return out
        instruction = '统一当前请求与实际参考。参考图观察：' + '；'.join(checked.get('observations', []))
        out['verify'].update(request_conflict=True, verdict='obvious',
                             evidence='；'.join(x for x in (out['verify'].get('evidence'), detail) if x),
                             instruction=instruction)
        out.update(severity='fail', identity_ok=False, identity_issue=detail, feedback=instruction, scripted=False)
    return out


def review(clip, video, bible, work_dir, verdict):
    note = pending_cast_note(verdict)
    if verdict.get('severity') != 'fail' and not note:
        return verdict
    concerns = list(dict.fromkeys(str(verdict.get(k) or '').strip() for k in
                                 ('identity_issue', 'story_issue', 'defect_issue', 'location_issue') if verdict.get(k)))
    if note and verdict.get('severity') != 'fail':
        concerns.append(note)
    if not concerns:
        concerns = [str(verdict.get('feedback') or '候选判断为失败，核对是否有可见错误')]
    return check_inputs(clip, work_dir, _review_frames(clip, video, bible, work_dir, verdict, concerns),
                        video=video, bible=bible)


def _review_frames(clip, video, bible, work_dir, verdict, concerns):
    """Resolve these visual candidates with frames; input classification is a separate caller step."""
    parts, facts = evidence.collect_clip_evidence(clip, video, bible, work_dir, verify=True)
    text = ('先按图例观察当前视频帧和角色参考，逐帧描述，不根据姓名猜人。图例：' + '；'.join(facts.legend)
            + story_block(clip, facts.segments) + shot_contract(clip)
            + '\n以下是待核实的候选疑点，不是事实。逐条只保留实际画面能够证明的错误；原判断部分错误时，'
              'evidence只写成立的部分及对应帧。特别注意：看不到头可能是构图裁切，灰黑色差可能是照明，'
              '机甲里是否有人不能由它外形像人推断；只露出闭合头盔不能证明第二个托尼。'
              '放大核对眼镜，不要沿用文字描述中的“没眼镜”。未给出开合要求不能按角色卡强制开合。'
              '先写observations，再逐条写evidence，最后result：confirmed确有错误，dismissed为误报，'
              'uncertain为当前帧看不清。不能把未观察到的细节当成缺失。instruction只写正确画面，'
              'error_facts逐项回答本次证据是否真的证明该类错误，未证明一律false；dismissed或uncertain时全部false。'
              '衣着差异只在影响具体身份或剧情功能时标appearance；普通造型差异在observations保留提示，但错误指控应dismissed，不能标same_person_twice或lead_face_swapped。'
              '分身必须在同一帧证明两个属于同一个人的独立身体；参考卡的多视图不属于视频里的人数。'
              '这里只判断可见画面；请求与参考是否矛盾由独立的输入检查判断，不能把纯文字疑点冒充已看见的视觉错误。'
              '稳定衣物款式和配饰以实际参考图为观测基准；便装等泛称，不是增加外套、领带的剧情依据，'
              '但缺少换装说明也不能把无关剧情的服饰差异升级为必须修复。保留实际观察，只对影响身份或具体事件的差异确认appearance。'
              '有明确穿脱、面罩变化则按剧情，不能要求状态永远等同卡面。'
              '不复述旧错误；dismissed时为空。不增设原文/镜头没有要求的条件，不写推测或自我辩论。\n'
            + WATCHABLE_CRITERIA + '\n错误事实定义：' + json.dumps(ERROR_FACTS, ensure_ascii=False) + '\n'
            + json.dumps([{'id': i, 'candidate': c} for i, c in enumerate(concerns)], ensure_ascii=False))
    parts.append({'type': 'text', 'text': text})
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['checks'].update(minItems=len(concerns), maxItems=len(concerns))
    schema['properties']['checks']['items']['properties']['id']['enum'] = list(range(len(concerns)))
    answer = cast_video.ask(parts, schema, 'clip_visual_adjudication')
    return apply(verdict, answer, concerns)
