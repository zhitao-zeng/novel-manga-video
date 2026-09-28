"""Execute explicitly reviewed continuity; no files, model calls or movement keyword guesses."""
from __future__ import annotations

import copy

POLICY = 'posture-states-v2-continuity'
POSTURES = ('站', '坐', '蹲', '跪', '躺', '悬空', '走动', '未写明')
SAID = dict(zip(POSTURES[:-1], ('站着', '坐着', '蹲着', '跪着', '躺着', '悬在空中', '在走动')))


def people(shot):
    return list(dict.fromkeys(shot.get('in_frame', shot.get('characters') or []) or []))


def resolve(rows, answers, previous=None):
    """Copy only model-confirmed continuous states. Unknown/reset boundaries discard transient state."""
    last = copy.deepcopy(previous or {})
    stages, issues = {}, []
    for shot, answer in zip(rows, answers):
        index = shot['index']
        boundary = answer['boundary']
        problems = []
        if boundary == 'unknown':
            problems.append('前后镜是否连续不明确：' + answer['reason'] + '；请明确是否跳时、回忆或重新入场及本镜起始姿态')
        if boundary != 'continuous':
            last.clear()
        start, end = {}, {}
        for person in answer['people']:
            name = person['name']
            before = last.get(name, {})
            carry = boundary == 'continuous' and person['entry'] == 'continue'
            begin, finish = person['start'], person['end']
            stated = begin['posture'] != '未写明'
            if person['entry'] == 'unknown':
                problems.append(f'{name}的入场/承接不明确，请明确本镜起点，不可直接沿用离场前的姿态')
            pose = begin['posture'] if stated else before.get('posture') if carry else None
            where = begin['where']
            if not where and carry and pose == before.get('posture'):
                where = before.get('where', '')
            final_pose = finish['posture'] if finish['posture'] != '未写明' else pose
            final_where = finish['where'] or (where if final_pose == pose else '')
            # A transition is attributed to this person by the reader, not detected by finding
            # somebody else's "站起" anywhere in the shot. Check its quote only when a pose changes;
            # lifting a cup need not become a pose gate just because the reader also reported it.
            changed_at_start = carry and before.get('posture') and stated and pose != before['posture']
            changed_inside = pose and final_pose and pose != final_pose
            if (changed_at_start or changed_inside) and person['transition']:
                text = '；'.join(str(shot.get(k) or '') for k in ('visual_prompt', 'motion_prompt', 'end_state'))
                if person['transition'] not in text:
                    issues.append({'index': index, 'kind': 'reading',
                                   'detail': f'{name}的姿态改变证据不在本镜中，transition应逐字引用本人的改变动作'})
            elif not person['transition']:
                if changed_at_start:
                    problems.append(f'{name}上一镜结束时{before["posture"]}，本镜开始时{pose}，缺少明确的姿态交接')
                if changed_inside:
                    problems.append(f'{name}本镜从{pose}变成{final_pose}，未写明相应动作')
            start[name] = {'posture': pose, 'where': where, 'stated': stated,
                           'inherited_from': before.get('index') if carry and not stated and pose else None}
            end[name] = {'posture': final_pose, 'where': final_where, 'index': index}
            last[name] = end[name]
        stages[str(index)] = {'boundary': boundary, 'reason': answer['reason'],
                              'start': start, 'end': end, 'reading': copy.deepcopy(answer)}
        issues.extend({'index': index, 'detail': problem} for problem in problems)
    return stages, issues, last


def phrase(shot):
    """Only fill omitted posture, never overwrite or replay an authored change in a later cut."""
    record = shot.get('_posture') or {}
    start, end = record.get('start', {}), record.get('end', {})
    part, _ = shot.get('split_part') or (1, 1)
    notes = []
    for name in people(shot):
        state = start.get(name, {})
        pose = state.get('posture')
        if pose not in SAID or state.get('stated', True):
            continue
        if part > 1 and (end.get(name) or {}).get('posture') != pose:
            continue
        notes.append(name + SAID[pose] + (f'（{state["where"]}）' if state.get('where') else ''))
    return '；'.join(notes)
