"""Shared posture model replies; unrelated planner tests still exercise the real ledger boundary."""
import json


def person(pose='未写明', *, name='席勒', end='未写明', where='', entry='continue', transition=''):
    return {'name': name, 'entry': entry,
            'start': {'posture': pose, 'where': where, 'quote': pose if pose != '未写明' else where},
            'end': {'posture': end, 'where': '', 'quote': end if end != '未写明' else ''},
            'transition': transition}


def answer(index, pose='未写明', *, boundary='continuous', persons=None, **kw):
    return {'index': index, 'boundary': boundary, 'reason': '按本镜与原文时间和动作交接',
            'people': persons if persons is not None else [person(pose, **kw)]}


def unspecified_reply(parts, schema, **kwargs):
    inputs = json.loads(parts[0]['text'].split('\n')[-1])
    return {'stages': [answer(s['index'], boundary='reset',
                             persons=[person(name=n, entry='reset') for n in s['in_frame']])
                       for s in inputs['shots']]}
