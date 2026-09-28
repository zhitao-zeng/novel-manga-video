"""Check the final request against the exact images it will send, before using a video slot."""
from __future__ import annotations
import json
from pathlib import Path

from novel_manga.application.configuration import h3_translation_endpoint
from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.util import read_json, atomic_write_json

POLICY = 'request-reference-v4-current-input-only'
STABLE_ASPECTS = {'identity', 'appearance', 'wearing', 'object'}
SCHEMA = obj({'observations': {'type': 'array', 'items': {'type': 'string', 'maxLength': 240}},
              'findings': {'type': 'array', 'items': obj({
                  'basis': {'type': 'string', 'enum': ['reference', 'request']},
                  'aspect': {'type': 'string', 'enum': ['identity', 'appearance', 'wearing', 'object',
                             'action', 'state', 'visibility', 'space', 'sound', 'lighting', 'style', 'camera']},
                  'picture': {'type': 'integer', 'minimum': 0},
                  'lines': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1}},
                  'subject_specific': {'type': 'boolean'}, 'conflict': {'type': 'boolean'},
                  'reason': {'type': 'string', 'maxLength': 240}})}})


def interpret(answer, lines, picture_count):
    if 'findings' not in answer:
        raise ValueError('request check did not return scoped findings')
    problems, ignored = [], []
    for finding in answer['findings']:
        if not finding['conflict']:
            continue
        # A reference card supplies stable identity/design, not a pose or camera. Generic style
        # and shot-size preferences are not a reason to rewrite an authored scene in this gate.
        if (finding['aspect'] in {'style', 'camera'} or not finding['subject_specific']
                or (finding['basis'] == 'reference' and finding['aspect'] not in STABLE_ASPECTS)):
            ignored.append(finding)
            continue
        if not finding['lines'] or any(type(i) is not int or not 1 <= i <= len(lines) for i in finding['lines']):
            raise ValueError('request conflict does not identify actual request lines')
        if finding['basis'] == 'reference' and not 1 <= finding['picture'] <= picture_count:
            raise ValueError('reference conflict does not identify a submitted image')
        problems.append(f"{finding['reason']}（请求行{','.join(map(str, finding['lines']))}"
                        + (f"；图{finding['picture']}" if finding['basis'] == 'reference' else '') + '）')
    return {'policy': POLICY, 'observations': answer.get('observations', []), 'consistent': not problems,
            'problems': problems, 'findings': answer['findings'], 'out_of_scope': ignored}


def request_consistency(clip: dict, novel_dir: Path, *, request=None) -> dict:
    """The images are input evidence, not character descriptions substituted for the images."""
    metadata = [r for r in clip.get('references', []) if r.get('role') != 'voice']
    paths = ([Path(p) for p in request['references']] if request is not None
             else [Path(novel_dir) / r['path'] for r in metadata])
    parts, legend = [], []
    for i, path in enumerate(paths, 1):
        ref = metadata[i - 1] if i <= len(metadata) else {}
        parts.append(image_part(path, 1024))
        legend.append({'picture': i, 'name': ref.get('name'), 'role': ref.get('role'),
                       'view': ref.get('view'), 'wearers': ref.get('wearers', [])})
    english = str(request['prompt'] if request is not None else clip.get('prompt_h3', ''))
    # These are explicit compiler-owned style/metadata fields, not character instructions. The
    # actual generation request stays untouched. A generic "fabric surfaces" style sentence
    # cannot be evidence that the armoured subject was asked to change into cloth.
    style = str(clip.get('h3_style_line') or '').strip()
    lines = [('中文', s) for s in str(clip.get('prompt', '')).splitlines()
             if s.strip() and not s.startswith(('【生成目标】', '【视觉语法】', '画面呈现', '【不要】'))]
    lines += [('英文', s) for s in english.splitlines() if s.strip() and not (style and style in s)]
    prompt = ('核对实际即将发给视频模型的请求与参考图，不评价尚未生成的视频，不改台词。'
              '先在observations逐图记录可见的衣物款式、主色、配饰、物件类型，再比较本镜文字。'
              '角色的稳定外观以实际角色参考为准；没有明确换装情节却让白衬衫加马甲的人穿深色便装或西装领带，属于输入冲突。'
              '有明确穿戴关系和对应装备参考时可覆盖基础衣物；明确的穿脱、面罩开合按本镜事件执行，'
              '不能把参考卡上的坐站、机位、面罩默认状态强加给视频；头部被裁切不算缺脸。'
              '场景参考可有空间，不能因为人物参考卡背景有椅子就要求每镜坐着。'
              '核对同一个人是否同时在过肩前景和背景、画外与画内是否冲突、起止状态与动作是否矛盾、'
              '穿戴物是否被额外当成一个身体、取消动作后是否还留有对应动作声。'
              '同款机甲既被穿戴又作为剧情明确要求的独立空甲是合法的，按本镜人数和物件用途判断。'
              '只报告清楚的矛盾或未落实的纠正，不把同色系明暗、风格化纹理或看不清当成不同服装。'
              '每条findings必须明确依据：basis=reference是参考图与文字，basis=request是请求文字内部。'
              'aspect区分身份/衣着/穿戴/物件与动作、姿态、可见性、空间、声音、光线、风格、景别。'
              '先确定subject_specific：句子是否真的要求这个主体这样穿/这样做；通用风格词不是给每个主体改材质，'
              '例如plain fabric surfaces只形容织物，不表示把金属机甲改成布衣。'
              '参考卡手插兜与剧本摸下巴不是矛盾；卡面站立与本镜坐姿也不是矛盾。'
              '泛称国漫/动画与具体美漫画风、特写占满画面与通用景别建议不属于本检查的阻塞项。'
              'picture填相关图号，纯文字冲突填0；lines选择下列真实请求行编号，reason简述该主体的具体冲突，'
              '不要抄长段落或编造原句。没有矛盾则findings为空。\n'
              '图例：' + json.dumps(legend, ensure_ascii=False)
              + '\n只根据本次图片与下列当前请求行判断，不推测旧版本内容。\n请求行：\n'
              + '\n'.join(f'[{i}] {lang} {s}' for i, (lang, s) in enumerate(lines, 1)))
    parts.append({'type': 'text', 'text': prompt})
    import copy
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['findings']['items']['properties']['lines']['items']['maximum'] = max(1, len(lines))
    schema['properties']['findings']['items']['properties']['picture']['maximum'] = len(paths)
    answer = ask_json(parts, schema, name='repair_request_consistency', max_tokens=1600,
                      settings=h3_translation_endpoint())
    return interpret(answer, lines, len(paths))


def cached_consistency(novel_dir, work, clip, request):
    """Same request and reference contents reuse this expensive check; a seed change alone does not rerun it."""
    inputs = {'policy': POLICY, 'chinese': clip.get('prompt', ''), 'english': request['prompt'],
              'references': request['references'], 'reference_sha256': request.get('reference_sha256'),
              'roles': [{k: r.get(k) for k in ('name', 'role', 'view', 'wearers')}
                        for r in clip.get('references', []) if r.get('role') != 'voice']}
    path = work / 'request_checks' / (clip['clip_id'] + '.json')
    saved = read_json(path, {})
    answer = saved.get('answer') if saved.get('inputs') == inputs else None
    # This revision narrows the old gate. An unchanged input which already passed its broader
    # check needs no additional model call; old failures must be re-evaluated under the new scope.
    old_inputs, old_answer = saved.get('inputs', {}), saved.get('answer', {})
    if (answer is None and old_inputs.get('policy') in {'request-reference-v1', 'request-reference-v2-scoped-evidence',
                                                     'request-reference-v3-scoped-evidence'}
            and {**old_inputs, 'policy': POLICY} == inputs
            and old_answer.get('consistent') is True and not old_answer.get('problems')):
        answer = {**old_answer, 'policy': POLICY, 'reused_from': old_inputs['policy']}
        atomic_write_json(path, {'inputs': inputs, 'answer': answer})
    if answer is None:
        answer = request_consistency(clip, novel_dir, request=request)
        atomic_write_json(path, {'inputs': inputs, 'answer': answer})
    return answer


def current_request(directory, clip):
    """Diagnose an already rendered request using the same input check as new submissions."""
    from novel_manga.media.common import reference_digests
    refs = tuple(directory.parent / r['path'] for r in clip.get('references', []) if r.get('role') != 'voice')
    request = {'prompt': clip.get('prompt_h3', ''), 'references': [str(p) for p in refs],
               'reference_sha256': reference_digests(refs)}
    return cached_consistency(directory.parent, directory / 'work', clip, request)


def before_generation(ctx, clip, request):
    answer = cached_consistency(ctx.novel_dir, ctx.work, clip, request)
    if answer.get('consistent') is not True or answer.get('problems'):
        problems = answer.get('problems') or ['请求与参考图未得到一致结论']
        ctx._blocked_clips[clip['clip_id']] = ['request: ' + p for p in problems]
        raise ValueError('H3 request/reference mismatch: ' + '；'.join(problems))
    return answer
