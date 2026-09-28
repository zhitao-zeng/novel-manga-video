"""Resolve a failed candidate against actual cards and current frames before spending a retake."""
from __future__ import annotations
import copy
import json

from novel_manga.llm import client
from novel_manga.application.review import cast_video, evidence
from novel_manga.review.prompts import shot_contract, story_block

POLICY = 'visual-adjudication-v3-observed-facts'
ERROR_FACTS = {
    'same_person_twice': '同一具体人物同时出现两个独立身体；衣服错、换装、独立空甲都不能证明分身',
    'extra_person': '出现计划不允许的额外人物，但不能确定是同一人物复制',
    'extra_object': '独立物件数量超出本镜要求，不把穿戴物另算一个身体',
    'appearance': '同一人物或物件的衣物款式、明显跨色系配色、眼镜等稳定外观不符',
    'state': '违反本镜明确的开合或动作状态，不能沿用参考卡默认姿态',
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
        if row['result'] == 'confirmed' and (set(facts) != set(ERROR_FACTS)
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
    if unresolved:
        out['error'] = 'visual evidence is insufficient to resolve every candidate issue'
    return out


def outdated(verdict):
    record = (verdict.get('verify') or {}).get('adjudication') or {}
    return bool(record.get('confirmed') and record.get('policy') != POLICY)


def check_inputs(clip, work_dir, verdict):
    """A frame judgment cannot waive a conflicting request/card pair by treating the bad request as truth."""
    if not clip.get('prompt_h3') or not any(r.get('role') != 'voice' for r in clip.get('references', [])):
        return verdict
    from novel_manga.application.preparation.request_check import current_request
    checked = current_request(work_dir.parents[2], clip)
    record = (verdict.get('verify') or {}).get('adjudication') or {}
    # Reconstruct the visual decision before applying the current input scope. This retires an
    # old false input blocker without clearing a confirmed visual defect on the same take.
    out = apply(verdict, record, record['concerns']) if record.get('checks') else copy.deepcopy(verdict)
    out['verify']['request_check'] = checked
    if checked.get('consistent') is not True or checked.get('problems'):
        detail = '；'.join(checked.get('problems') or ['拍摄请求与实际参考图不一致'])
        instruction = '统一当前请求与实际参考。参考图观察：' + '；'.join(checked.get('observations', []))
        out['verify'].update(request_conflict=True, verdict='obvious',
                             evidence='；'.join(x for x in (out['verify'].get('evidence'), detail) if x),
                             instruction=instruction)
        out.update(severity='fail', identity_ok=False, identity_issue=detail, feedback=instruction, scripted=False)
    return out


def review(clip, video, bible, work_dir, verdict):
    if verdict.get('severity') != 'fail':
        return verdict
    concerns = list(dict.fromkeys(str(verdict.get(k) or '').strip() for k in
                                 ('identity_issue', 'story_issue', 'defect_issue') if verdict.get(k)))
    if not concerns:
        concerns = [str(verdict.get('feedback') or '候选判断为失败，核对是否有可见错误')]
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
              '同一人物只是穿错衣服，只标appearance，不能标same_person_twice或lead_face_swapped。'
              '分身必须在同一帧证明两个属于同一个人的独立身体；参考卡的多视图不属于视频里的人数。'
              '这里只判断可见画面；请求与参考是否矛盾由独立的输入检查判断，不能把纯文字疑点冒充已看见的视觉错误。'
              '有明确穿脱、面罩变化则按剧情，不能要求状态永远等同卡面。'
              '不复述旧错误；dismissed时为空。不增设原文/镜头没有要求的条件，不写推测或自我辩论。\n'
            + '错误事实定义：' + json.dumps(ERROR_FACTS, ensure_ascii=False) + '\n'
            + json.dumps([{'id': i, 'candidate': c} for i, c in enumerate(concerns)], ensure_ascii=False))
    parts.append({'type': 'text', 'text': text})
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['checks'].update(minItems=len(concerns), maxItems=len(concerns))
    schema['properties']['checks']['items']['properties']['id']['enum'] = list(range(len(concerns)))
    answer = cast_video.ask(parts, schema, 'clip_visual_adjudication')
    return check_inputs(clip, work_dir, apply(verdict, answer, concerns))
