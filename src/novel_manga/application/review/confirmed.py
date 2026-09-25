"""Confirmed picture failures stay open until their replacement is checked against the finding."""
from __future__ import annotations
import copy
from pathlib import Path
from novel_manga.util import read_json
from novel_manga.llm.client import ask_json, image_part
from novel_manga.application.configuration import h3_translation_endpoint
from novel_manga.application.review.evidence import clip_frames

SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['observations', 'checks'], 'properties': {
              'observations': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
              'checks': {'type': 'array', 'minItems': 1, 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['criterion', 'scope', 'met', 'evidence'],
                  'properties': {'criterion': {'type': 'string'}, 'scope': {'type': 'string', 'enum': ['visual', 'audio', 'source']}, 'met': {'type': ['boolean', 'null']},
                                 'evidence': {'type': 'string'}}}}}}


def finding_result(answer):
    checks = [c for c in answer.get('checks') or [] if c.get('scope', 'visual') == 'visual']
    result = ('unresolved' if any(c.get('met') is False for c in checks) else
              'resolved' if checks and all(c.get('met') is True for c in checks) else 'uncertain')
    return {**answer, 'result': result, 'checked_scope': 'visual',
            'evidence': '；'.join(c.get('evidence', '') for c in checks if c.get('met') is not True)
                        or '；'.join(c.get('evidence', '') for c in checks)}


def apply_claim(verdict, claim, video, take):
    verdict = copy.deepcopy(verdict)
    check = claim.get('check') or {}
    resolved = check.get('video') == str(video) and check.get('take') == take and check.get('result') == 'resolved'
    verdict['confirmed'] = claim
    if not resolved:
        verdict.update(severity='fail', tier='must_fix', scripted=False,
                       defect_issue=claim['issue'], feedback=claim['instruction'])
        # This is confirmed evidence, including defects the broad verifier does not measure.
        verdict['verify'] = {**(verdict.get('verify') or {}), 'verdict': 'obvious',
                             'evidence': claim['issue'], 'instruction': claim['instruction']}
    return verdict



def judge_finding(clip, video, claim, work, novel_dir=None):
    """Inspect current ordered frames for the concrete acceptance condition; never auto-clear on a broad pass."""
    frames = clip_frames(Path(video), Path(work), 6)
    parts = []; seen = set()
    for ref in clip.get('references', []):
        if ref.get('role') != 'character' or ref.get('name') in seen:
            continue
        path = Path(ref['path'])
        if not path.is_absolute():
            if novel_dir is None:
                raise ValueError('relative character reference needs its novel directory')
            path = Path(novel_dir) / path
        seen.add(ref['name'])
        parts.extend([{'type':'text', 'text': '角色身份参考（不是视频帧，不计入画面人数）：'+ref['name']+
                      ('，这是该角色闭合头盔时的样子，仍是角色本人，不是无人空甲。' if ref.get('view') == 'closed' else '')},
                      image_part(path, 640)])
    for i, frame in enumerate(frames,1):
        parts.extend([{'type':'text','text':f'当前视频第{i}帧'},image_part(frame,1280)])
    parts.append({'type': 'text', 'text': (
        '前面的角色参考图只用于辨认身份与服装；只检查随后标明的6张当前视频帧。先按参考资料确认谁是谁，逐帧列出实际观察，'
        '再把下面的正向验收目标拆成checks。每条check引用目标中的要求写criterion，'
        '根据图片填写met：符合为true，不符合为false，看不清为null；evidence必须对应当前帧事实。'
        'scope标明visual（画面）、audio（声音）或source（原文）。本调用只验画面，听不到台词或无法核对原文不是画面失败，分别标audio/source和null；只在visual项中判断画面是否满足要求。'
        '不另写总判定，也不猜测旧版本出了什么错。不要添加目标没有要求的条件。'
        '角色可以只露背影、半身或戴闭合头盔；看不到脸并不表示独立空甲或额外角色。'
        '肩背属于同一身体时不多计；同一个身体在不同帧转身、改变姿势也不多计。'
        '只有同一帧里在不同位置同时出现两个独立身体/空甲，才是数量增加。'
        '动作按先后帧的起点和终点核对；目标只要求踏上台阶时，不额外要求整个人走进车厢。'
        '\n正向验收目标：' + claim['instruction'] + '\n只输出JSON。')})
    answer = ask_json(parts, SCHEMA, name='confirmed_picture_check', max_tokens=1800,
                      settings=h3_translation_endpoint())
    return finding_result(answer)


def enforce(directory, report, previous, *, verify=False):
    """Read-only unless the caller publishes the returned report. Resolved checks are bound to a take."""
    result = copy.deepcopy(report)
    plan = {c['clip_id']: c for c in read_json(directory / 'clip_plan.json', {}).get('clips', [])}
    for cid, old in (previous.get('clips') or {}).items():
        claim = copy.deepcopy(old.get('confirmed'))
        row = result.get('clips', {}).get(cid)
        if not claim or not row or not row.get('video') or not row.get('take'):
            continue
        video, take = row['video'], row['take']; check = claim.get('check') or {}
        checked = check.get('video') == video and check.get('take') == take
        if verify and not checked:
            try:
                answer = judge_finding(plan[cid], video, claim, directory / 'work/confirmed_review' / cid, directory.parent)
                claim['check'] = {**answer, 'video': video, 'take': take}
            except Exception as error:
                claim['check'] = {'result': 'uncertain', 'video': video, 'take': take,
                                  'evidence': f'检查未完成：{type(error).__name__}'}
        row = apply_claim(row, claim, video, take)
        result['clips'][cid] = row
        if row.get('severity') == 'fail':
            result.setdefault('feedback', {})[cid] = claim['instruction']
    return result
