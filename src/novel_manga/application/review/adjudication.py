"""Resolve a failed candidate against actual cards and current frames before spending a retake."""
from __future__ import annotations
import copy
import json

from novel_manga.llm import client
from novel_manga.application.review import cast_video, evidence
from novel_manga.review.prompts import shot_contract, story_block

SCHEMA = client.obj({
    'observations': {'type': 'array', 'items': {'type': 'string'}},
    'checks': {'type': 'array', 'minItems': 1, 'items': client.obj({
        'id': {'type': 'integer'}, 'evidence': {'type': 'string', 'maxLength': 180},
        'result': {'type': 'string', 'enum': ['confirmed', 'dismissed', 'uncertain']},
        'kinds': {'type': 'array', 'items': {'type': 'string', 'enum': [
            'same_person_twice', 'extra_person', 'extra_object', 'appearance', 'state',
            'visual_defects', 'action_by_wrong_person', 'actor_missing', 'lead_face_swapped']}},
        'instruction': {'type': 'string', 'maxLength': 180}})}})


def apply(verdict, answer, concerns):
    checks = answer.get('checks') or []
    if {r.get('id') for r in checks} != set(range(len(concerns))) or len(checks) != len(concerns):
        raise ValueError('visual adjudication did not answer every candidate issue exactly once')
    unresolved = any(r['result'] == 'uncertain' for r in checks)
    confirmed = [r for r in checks if r['result'] == 'confirmed']
    kinds = sorted({kind for r in confirmed for kind in r.get('kinds', [])})
    detail = '；'.join(r['evidence'] for r in confirmed)
    instruction = '；'.join(dict.fromkeys(r['instruction'] for r in confirmed))
    out = copy.deepcopy(verdict)
    # Retain the original evidence, but none of its dismissed flags can still trigger a retake.
    old = out.get('verify') or {}
    out['verify'] = {'verdict': 'obvious' if confirmed else 'fine',
                     'evidence': detail, 'instruction': instruction, 'error_kinds': kinds,
                     'cast_video': {**(old.get('cast_video') or {}), 'adjudicated': True},
                     'adjudication': {**answer, 'concerns': concerns, 'confirmed': bool(confirmed)},
                     'candidate': {k: v for k, v in old.items() if k != 'cast_video'}}
    out.update(severity='review_error' if unresolved else 'fail' if confirmed else 'pass',
               identity_ok=not bool(confirmed), identity_issue=detail,
               visual_defects=None, defect_issue='', story_ok=None, story_kind='无法判断', story_issue='',
               feedback=instruction, scripted=False)
    if unresolved:
        out['error'] = 'visual evidence is insufficient to resolve every candidate issue'
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
              'kinds只标记本次确认成立的错误类型，dismissed或uncertain时留空；不要沿用已反驳的旧分类。'
              '不复述旧错误；dismissed时为空。不增设原文/镜头没有要求的条件，不写推测或自我辩论。\n'
            + json.dumps([{'id': i, 'candidate': c} for i, c in enumerate(concerns)], ensure_ascii=False))
    parts.append({'type': 'text', 'text': text})
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['checks'].update(minItems=len(concerns), maxItems=len(concerns))
    schema['properties']['checks']['items']['properties']['id']['enum'] = list(range(len(concerns)))
    answer = cast_video.ask(parts, schema, 'clip_visual_adjudication')
    return apply(verdict, answer, concerns)
