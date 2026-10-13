"""Check the final request against the exact images it will send, before using a video slot."""
from __future__ import annotations
import json
from pathlib import Path

from novel_manga.application.configuration import h3_request_check_endpoint
from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.util import read_json, atomic_write_json
from novel_manga.review.contracts import WATCHABLE_CRITERIA

POLICY = 'request-reference-v10-casting-watchable'
STABLE_ASPECTS = {'identity', 'appearance', 'wearing', 'object'}
SCHEMA = obj({'observations': {'type': 'array', 'items': {'type': 'string', 'maxLength': 240}},
              'findings': {'type': 'array', 'items': obj({
                  'basis': {'type': 'string', 'enum': ['reference', 'request']},
                  'aspect': {'type': 'string', 'enum': ['identity', 'appearance', 'wearing', 'object',
                             'action', 'state', 'visibility', 'space', 'sound', 'lighting', 'style', 'camera']},
                  'picture': {'type': 'integer', 'minimum': 0},
                  'lines': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1}},
                  'subject_specific': {'type': 'boolean'},
                  'relation': {'type': 'string', 'enum': ['contradiction', 'underspecified', 'compatible']},
                  'reason': {'type': 'string', 'maxLength': 240}})}})


class RequestCheckIncomplete(ValueError):
    """No semantic verdict exists when the independent scope check is unresolved."""

    def __init__(self, result):
        super().__init__('request conflict scope review is incomplete; preserve the input unchanged')
        self.result = result


def casting_context(clip):
    return {'cast': list(clip.get('cast') or []), 'extras': list(clip.get('extras') or [])}


def interpret(answer, lines, picture_count, *, references=()):
    scope = answer.get('scope_check') or {}
    if not isinstance(answer.get('findings'), list):
        raise ValueError('request check did not return scoped findings')
    incomplete = bool(scope.get('error')) or any(r.get('scope') == 'uncertain' for r in scope.get('checks', []))
    scopes = {r.get('id'): r.get('scope') for r in scope.get('checks', [])}
    problems, ignored, unresolved, warnings = [], [], [], []
    location_pictures = {r['picture'] for r in references if r.get('role') == 'location'}
    for index, finding in enumerate(answer['findings']):
        if finding.get('relation') not in {'contradiction', 'underspecified', 'compatible'}:
            raise ValueError('request check did not classify the relation between current facts')
        if finding['relation'] != 'contradiction':
            continue
        if scope.get('error') or scopes.get(index) == 'uncertain':
            unresolved.append(finding)
            continue
        # A reference card supplies stable identity/design, not a pose or camera. Generic style
        # and shot-size preferences are not a reason to rewrite an authored scene in this gate.
        if (finding.get('scope_recheck') == 'out_of_scope'
                or finding['aspect'] in {'style', 'camera'} or not finding['subject_specific']
                or (finding['basis'] == 'reference' and finding['aspect'] not in STABLE_ASPECTS
                    and not (finding['aspect'] == 'space' and finding['picture'] in location_pictures))):
            ignored.append(finding)
            continue
        if not finding['lines'] or any(type(i) is not int or not 1 <= i <= len(lines) for i in finding['lines']):
            raise ValueError('request conflict does not identify actual request lines')
        if finding['basis'] == 'reference' and not 1 <= finding['picture'] <= picture_count:
            raise ValueError('reference conflict does not identify a submitted image')
        if (finding.get('scope_recheck') == 'advisory' and finding['basis'] == 'reference'
                and finding['aspect'] in {'wearing', 'appearance'}):
            warnings.append(finding)
            continue
        problems.append(f"{finding['reason']}（请求行{','.join(map(str, finding['lines']))}"
                        + (f"；图{finding['picture']}" if finding['basis'] == 'reference' else '') + '）')
    result = {'policy': POLICY, 'observations': answer.get('observations', []), 'consistent': not problems,
              'problems': problems, 'findings': answer['findings'], 'out_of_scope': ignored, 'warnings': warnings}
    if incomplete:
        result.update(complete=False, consistent=False if problems else None,
                      unresolved=unresolved, scope_check=scope)
        raise RequestCheckIncomplete(result)
    return result


def recheck_scope(answer, lines, legend, *, casting=None):
    """Verify what a reported conflict compares before treating its labels as authoritative."""
    candidates = [(i, row) for i, row in enumerate(answer.get('findings', []))
                  if row.get('relation') == 'contradiction']
    if not candidates:
        return answer
    prompt = (WATCHABLE_CRITERIA + '复核输入检查器报告的冲突范围，不重新判断图片内容，也不审查尚未生成的视频。'
              '报告的aspect/basis标签可能标错，必须依据reason描述的两项事实和原始请求行重新判断。'
              '角色参考只提供稳定身份、身体结构和服装设计；站坐、手部姿势、手中临时道具、背景、'
              '没有正在抽烟或喝酒，都不能限制导演安排的新动作。角色图站着而分镜坐着是out_of_scope，'
              '不是appearance冲突；图里没有临时手持道具不等于导演不能让角色拿起它。'
              '普通场景参考图提供地形、建筑和固定结构，不是成片的全量物体清单或带人物的起始帧。'
              '导演在场景中安排人群、临时篝火、食物等可移动物件，不因参考图里没有就构成矛盾。'
              '临时群众和无专用参考图的道具允许用正文描述，不要求每个对象都注册成Subject。'
              'fully_preserved按主体定义中的参考用途解释，不把背景图缺少群众当成禁止群众入镜。'
              '场景建筑或室内室外的明确冲突、剧情所必需的服饰缺失、物件设计相反仍是in_scope。'
              '仅reference的wearing/appearance中不影响身份、动作、剧情的轻微服装/配饰差异填advisory，保留差异作为提示。'
              '性别、物种、明显换脸和身体结构不属于轻微外观差异，不能填advisory。'
              'basis=request需要两条实际请求约束互相矛盾；身份定义没写坐姿不是坐姿指令的反面。'
              '按阶段先后允许换姿势与移动，不把不同时间的状态强制同时发生。'
              '编译名单提供身份上下文，不能仅凭体型、性别或衣着相似认定同人；同人位置矛盾需要同一Subject、同名或明确同人依据。'
              '无法确定范围则uncertain，不擅自放行。每个id回答一次，给出理由。\n'
              + json.dumps({'references': legend, 'casting': casting or {}, 'request_lines': lines,
                            'findings': [{'id': i, **row} for i, row in candidates]}, ensure_ascii=False))
    schema = obj({'checks': {'type': 'array', 'items': obj({
        'id': {'type': 'integer'}, 'scope': {'type': 'string', 'enum': ['in_scope', 'out_of_scope', 'advisory', 'uncertain']},
        'reason': {'type': 'string', 'maxLength': 240}})}})
    try:
        checked = ask_json([{'type': 'text', 'text': prompt}], schema,
            name='request_conflict_scope', max_tokens=1200, timeout=180, settings=h3_request_check_endpoint(), json_object=True)
        rows = checked.get('checks') or []
        if len(rows) != len(candidates) or {r.get('id') for r in rows} != {i for i, _ in candidates}:
            return {**answer, 'scope_check': {'error': 'incomplete scope recheck'}}
    except Exception as error:
        return {**answer, 'scope_check': {'error': type(error).__name__}}
    import copy
    revised = copy.deepcopy(answer)
    for row in rows:
        if row['scope'] in {'out_of_scope', 'advisory'}:
            revised['findings'][row['id']]['scope_recheck'] = row['scope']
    revised['scope_check'] = checked
    return revised


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
             if not english and s.strip() and not s.startswith(('【生成目标】', '【视觉语法】', '画面呈现', '【不要】'))]
    lines += [('英文', s) for s in english.splitlines() if s.strip() and not (style and style in s)]
    prompt = (WATCHABLE_CRITERIA + '核对实际即将发给视频模型的请求与参考图，不评价尚未生成的视频，不改台词。'
              '最终英文是实际执行的画面和声音要求，中文仅提供剧情和身份背景；不要用过期的中文机位/状态否定已确认的英文修正。'
              '先在observations逐图记录可见的衣物款式、主色、配饰、物件类型，再比较本镜文字。'
              '角色的稳定外观以实际角色参考为准；本镜具体衣物、主色、配饰要求与图中事实明确不兼容且没有换装情节时，才是输入冲突。'
              '有明确穿戴关系和对应装备参考时可覆盖基础衣物；明确的穿脱、面罩开合按本镜事件执行，'
              '不能把参考卡上的坐站、机位、面罩默认状态强加给视频；头部被裁切不算缺脸。'
              '场景参考可有空间，不能因为人物参考卡背景有椅子就要求每镜坐着。'
              '普通场景图仅约束地形、建筑和固定结构；导演可添加剧情所需的群众、篝火、食物等临时物体，'
              '也可安排当前光线变化。参考图没有这些内容不表示禁止出现。'
              'Subject标签用于绑定参考主体，不是所有可入镜对象的封闭名单；正文可描述无专用参考图的临时群众。'
              '编译名单提供身份上下文，不是不可质疑的真值；不能仅凭体型、性别或衣着相似认定同人。'
              '核对同一个人是否同时在过肩前景和背景、画外与画内是否冲突、起止状态与动作是否矛盾、'
              '穿戴物是否被额外当成一个身体、取消动作后是否还留有对应动作声。'
              '同款机甲既被穿戴又作为剧情明确要求的独立空甲是合法的，按本镜人数和物件用途判断。'
              '逐项区分relation：contradiction是两项当前事实不能同时成立；underspecified是泛称、缺少细节、看不清或仅存在可能忽略参考的风险；'
              'compatible是两项事实可以同时成立。只让明确矛盾阻挡，不把缺少细节当成相反要求。'
              '便装/casual clothes没有列出白衬衫、马甲不表示脱掉或替换它们，若其他行要求保留参考服装应结合全文理解。'
              '本镜明确给出的具体服装款式或主色不能被通用的“保留参考服装”句抵消；先比较该具体要求与图片事实，'
              '不要因为图中有部分深色衣物，就把整套深色服装或深色西装外套都当成已满足。'
              '明确要求西装外套、领带而参考无该服饰，或明确要求的主体衣物主色与参考不兼容，才可报告衣着矛盾；'
              '不能仅因未提及马甲或衬衫，把深色便装判错，必须指出具体不兼容的颜色或款式。'
              '同色系明暗、风格化纹理不当成不同服装。'
              '每条findings必须明确依据：basis=reference是参考图与文字，basis=request是请求文字内部。'
              'aspect区分身份/衣着/穿戴/物件与动作、姿态、可见性、空间、声音、光线、风格、景别。'
              '先确定subject_specific：句子是否真的要求这个主体这样穿/这样做；通用风格词不是给每个主体改材质，'
              '例如plain fabric surfaces只形容织物，不表示把金属机甲改成布衣。'
              '参考卡手插兜与剧本摸下巴不是矛盾；卡面站立与本镜坐姿也不是矛盾。'
              '泛称国漫/动画与具体美漫画风、特写占满画面与通用景别建议不属于本检查的阻塞项。'
              'picture填相关图号，纯文字冲突填0；lines选择下列真实请求行编号，reason写明双方实际事实及其关系，'
              'contradiction的理由须指出不能同时成立的具体属性，不能只说未提及、描述不完整或可能忽略，'
              '不要抄长段落或编造原句。没有矛盾则findings为空。\n'
              '图例：' + json.dumps(legend, ensure_ascii=False)
              + '\n编译人物名单：' + json.dumps(casting_context(clip), ensure_ascii=False)
              + '\n只根据本次图片与下列当前请求行判断，不推测旧版本内容。\n请求行：\n'
              + '\n'.join(f'[{i}] {lang} {s}' for i, (lang, s) in enumerate(lines, 1)))
    parts.append({'type': 'text', 'text': prompt})
    import copy
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['findings']['items']['properties']['lines']['items']['maximum'] = max(1, len(lines))
    schema['properties']['findings']['items']['properties']['picture']['maximum'] = len(paths)
    answer = None
    try:
        answer = ask_json(parts, schema, name='repair_request_consistency', max_tokens=1600,
                          settings=h3_request_check_endpoint(), json_object=True)
        answer = recheck_scope(answer, lines, legend, casting=casting_context(clip))
        result = interpret(answer, lines, len(paths), references=legend)
        if 'scope_check' in answer:
            result['scope_check'] = answer['scope_check']
        return result
    except RequestCheckIncomplete:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise RequestCheckIncomplete({'policy': POLICY, 'complete': False, 'consistent': None,
            'problems': [], 'raw_answer': answer, 'protocol_error': str(error)}) from error


def cached_consistency(novel_dir, work, clip, request, *, require_cached=False):
    """Same request and reference contents reuse this expensive check; a seed change alone does not rerun it."""
    inputs = {'policy': POLICY, 'casting': casting_context(clip), 'chinese': clip.get('prompt', ''), 'english': request['prompt'],
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
                                                     'request-reference-v3-scoped-evidence',
                                                     'request-reference-v4-current-input-only', 'request-reference-v5-explicit-contradictions',
                                                     'request-reference-v7-scope-recheck', 'request-reference-v8-scene-roles', 'request-reference-v9-watchable'}
            and {**old_inputs, 'casting': old_inputs.get('casting', {'cast': [], 'extras': []}), 'policy': POLICY} == inputs
            and old_answer.get('complete') is not False and not (old_answer.get('scope_check') or {}).get('error')
            and not any(r.get('scope') == 'uncertain' for r in (old_answer.get('scope_check') or {}).get('checks', []))
            and old_answer.get('consistent') is True and not old_answer.get('problems')
            and not any(f.get('basis') == 'reference' and f.get('aspect') == 'space'
                        and f.get('relation') == 'contradiction'
                        for f in old_answer.get('findings', []))):
        answer = {**old_answer, 'policy': POLICY, 'reused_from': old_inputs['policy']}
        atomic_write_json(path, {'inputs': inputs, 'answer': answer})
    if answer is None:
        if require_cached:
            raise ValueError('prepared request is stale; return clip to standard preparation')
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
    answer = cached_consistency(ctx.novel_dir, ctx.work, clip, request,
                                require_cached=ctx.options.prepared_requests_only)
    if answer.get('consistent') is not True or answer.get('problems'):
        problems = answer.get('problems') or ['请求与参考图未得到一致结论']
        ctx.state.blocked_clips[clip['clip_id']] = ['request: ' + p for p in problems]
        raise ValueError('H3 request/reference mismatch: ' + '；'.join(problems))
    return answer
