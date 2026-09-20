"""An actual scene screenplay, before any camera or generation container is chosen."""
from __future__ import annotations

import copy
from .evidence import source_refs
from novel_manga.story.fields import turn_field, dialogue_instructions


VERSION = "scene-screenplay-v2"


def screenplay_input(payload, notes=""):
    """Source coordinates and render containers must not become writing quotas."""
    result = copy.deepcopy(payload)
    result.pop('story_method', None)
    result.get('production_profile', {}).pop('story_method', None)
    for key in ('policy', 'requirements', 'quoted_lines_that_must_be_kept'):
        result.pop(key, None)
    budget = payload['planning_budget']
    result['planning_budget'] = {k: budget[k] for k in
                                 ('episode_target_seconds', 'episode_max_seconds') if k in budget}
    result['planning_budget']['single_shot_max_seconds'] = budget['clip_seconds'][1]
    if notes:
        result['director_notes'] = notes
    return result


def obj(fields):
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


def text(description=""):
    return {"type": "string", "description": description}


def array(items, minimum=0, maximum=None):
    return {"type": "array", "minItems": minimum, "items": items,
            **({'maxItems': maximum} if maximum is not None else {})}


def screenplay_schema(payload):
    evidence = {"type": "string", "maxLength": 180,
                "description": "从完整原文逐字复制连续短句，可跨相邻区段边界，不得跳句拼接或改写"}
    source_id = {'type': 'string', 'enum': [s['segment_id'] for s in payload['segments']],
                 'description': '引文覆盖的任一区段；跨区段的完整定位由程序补齐'}
    turn = turn_field(payload['available_characters'], payload.get('anonymous_offscreen_speakers', []),
                      ['visible_dialogue', 'offscreen_dialogue', 'chat_message', 'singing'])
    unit = obj({"source_segment_id": source_id, "source_quote": evidence,
                "action": text("这一段实际发生的、可演的连续行为；不是情节概括"),
                "turns": array(turn, maximum=8), "sfx": text("这一段的具体声音事实，不含对白；无需声音时留空")})
    return obj({
        "video_title": text(), "hook": text(), "summary": text(),
        "episode_contract": obj({k: text() for k in ('goal', 'obstacle', 'outcome', 'exit_state')}),
        "scenes": array(obj({
            "location": {"type": "string", "enum": payload['available_locations']},
            "time": text("具体的故事时间，如第一天傍晚；一场只在一个连续时空"),
            "transition": {"type": "string", "enum": ['opening', 'continuous', 'scene_change', 'time_jump', 'flashback', 'return_present']},
            "purpose": text("本场让观众理解或感受到的具体变化"),
            "entry_state": text("本场开始时已经成立的人物、位置、持物与知识"),
            "units": array(unit, 1, 24),
            "exit_state": text("本场实际表演后成立的可见状态；不能新增未发生的动作"),
        }), 1, 32),
        "omissions": array(obj({"source_segment_id": source_id,
                                 "reason": text("合并说明省略的议论及理由，不逐句列清单，不得省掉核心事件和结尾")}), maximum=len(payload['segments'])),
    })


def screenplay_prompt(method):
    strategy = method.screenwriting or (
        "这一遍使用共同小说改编步骤，只负责可读的分场剧本。随后才由指定导演方法处理摄影、表演与剪辑。"
        "从原文保留人物的追求、事实、因果、关键发现及结局，把心理叙述转成观众可理解的动作与声音。")
    return (
        "你是小说改编编剧。这一遍只写完整分场剧本，不写分镜、不写视频模型提示词。\n" + strategy + "\n"
        "从第一行读到最后一行，区分客观事实、人物猜测、回忆和作者议论。"
        "原文章节片段只是引用坐标，不是场次配额：同一场可以用多个片段，一个片段也可以拆成多个场。"
        "场数和每场的units数量由实际事件决定；不要每区段机械写一场或两段。\n"
        "先确定整集的因果和情绪走向，再逐场写正文。units是场景正文的连续段落，action写实际行为，"
        "turns写实际说出的话，sfx写实际听到的声音。动作、台词、反应在同一场中交错发生。"
        "不要用'意识到'、'确认'、'回忆'或'决定'代替让观众理解这件事的实际表达。"
        "需要留下来的信息必须进入正文，不能只写在提纲说明里。\n"
        "一场只有一个连续地点和时间；睡觉到次日、几天跋涉、闪回和返回现实必须分场。"
        "允许带明确标记的冷开场或回忆，原文发生顺序与讲述顺序可以不同，但因果、知识和持物不能改变。"
        "重要的取得、转移、放下、接触各发生一次；不要为了复述同一区段重复动作。\n"
        "source_quote必须逐字复制完整原文中支撑正文的连续短句，可以跨相邻区段边界；"
        "source_segment_id选择引文实际覆盖的任一区段，程序补齐其他区段，不能跳句拼接或拿无关句冒充依据。"
        "原文的普通议论、重复解释和食谱来历可以简化，在omissions合并说明理由，每个区段至多一条；不要逐句罗列，完整主线与结尾不能省略。"
        "对白可在不改变事实和人物关系的前提下改写成口语，不必照抄小说大段自语；"
        "说话人和发声方式必须明确，画外声音不等于现场人物。没有对白就turns=[]，不要强迫开口。"
        "不得凭空给家人编造具体回忆台词、新增人物或后续情节。\n"
        "当前人物造型、动画媒介、画幅按production_profile和story_bible；与造型冲突的非关键动作"
        "改用完成同一叙事功能的可行动作，不能改人物造型。动物、物件保留真实身份。"
        "光线随原文时间变化，不把参考场景的白天当作所有场次的时间。\n"
        "按给定目标时长组织主线，先删重复解释而不是必需事件；此处不要把一场戏当成一个15秒素材。"
        "交稿前从观众角度通读：身体/物件状态接得上，重要发现有表达，情绪有前因，结尾确实到达原文结尾。"
        + dialogue_instructions() + "只输出Schema JSON。"
    )


def normalize_screenplay(data, payload, method):
    # A saved blueprint may also contain an old director/review. Those must not
    # leak into a new direction pass of the same screenplay.
    if data.get('source_segments') is not None and data['source_segments'] != payload['segments']:
        raise ValueError('复用的分场稿不属于当前完整原文')
    result = {k: copy.deepcopy(data[k]) for k in
              ('video_title', 'hook', 'summary', 'episode_contract', 'scenes', 'omissions')}
    result.update(version=VERSION, method_id=method.key,
                  source_segments=copy.deepcopy(payload['segments']),
                  clip_seconds_max=payload['planning_budget']['clip_seconds'][1],
                  episode_seconds_max=payload['planning_budget'].get('episode_max_seconds'))
    issues = []
    covered = set()
    if not isinstance(result.get('scenes'), list) or not result['scenes']:
        raise ValueError('完整分场剧本没有scenes')
    for i, scene in enumerate(result['scenes'], 1):
        scene['scene_id'] = f'SC{i:03d}'
        if scene.get('location') not in payload['available_locations']:
            issues.append(f"{scene['scene_id']}: 未绑定地点")
        if not scene.get('time') or not scene.get('units'):
            issues.append(f"{scene['scene_id']}: 缺少时间或正文")
        if i == 1:
            # Opening is a presentation boundary, not a creative decision that
            # merits regenerating a whole screenplay. Story time stays explicit.
            scene['transition'] = 'opening'
        for j, unit in enumerate(scene.get('units', []), 1):
            unit['unit_id'] = f"{scene['scene_id']}-U{j:02d}"
            refs = source_refs(unit.get('source_quote', ''), payload['segments'], unit.get('source_segment_id', ''))
            if not refs:
                issues.append(f"{unit['unit_id']}: 引文不在完整原文中")
            elif unit.get('source_segment_id') not in {r['segment_id'] for r in refs}:
                issues.append(f"{unit['unit_id']}: 引文存在，但source_segment_id定位不符；实际覆盖"
                              + ', '.join(r['segment_id'] for r in refs))
            unit['source_refs'] = refs
            covered.update(r['segment_id'] for r in refs)
            if not unit.get('action') and not unit.get('turns') and not unit.get('sfx'):
                issues.append(f"{unit['unit_id']}: 空正文")
            if unit.get('turns'):
                from novel_manga.planning.normalization import normalize_turns
                from novel_manga.planning.context import PlannerContext
                context = PlannerContext(anonymous_speakers=payload.get('anonymous_offscreen_speakers', []),
                                         aliases=payload.get('name_aliases', {}))
                names = payload['available_characters']
                turn_errors, warnings = [], []
                normalized, _ = normalize_turns({'turns': unit['turns'], 'motion_prompt': unit['action']},
                    [], names, unit['unit_id'], context, turn_errors, warnings)
                issues.extend(str(e) for e in turn_errors)
                unit['turns'] = normalized
            for k, turn in enumerate(unit['turns'], 1):
                turn['turn_id'] = f"{unit['unit_id']}-T{k:02d}"
                if not turn.get('text', '').strip():
                    issues.append(f"{turn['turn_id']}: 空台词")
    for omission in result.get('omissions', []):
        # An omission is an editorial explanation for a source section, not a
        # new quotation. Its locator comes from the input, not model retyping.
        refs = [{'segment_id': s['segment_id'], 'source_quote': s['text']} for s in payload['segments']
                if s['segment_id'] == omission.get('source_segment_id')]
        omission.pop('source_quote', None)
        omission['source_refs'] = refs
        if not refs or not omission.get('reason', '').strip():
            issues.append('省略项缺少原文依据或理由')
        covered.update(r['segment_id'] for r in refs)
    missing = {s['segment_id'] for s in payload['segments']} - covered
    if missing:
        issues.append('未处理原文区段：' + ', '.join(sorted(missing)))
    if issues:
        raise ValueError('; '.join(issues))
    return result


SCENE_REVISION_PROMPT = (
    "本次仅替换replace_scene_ids指定的场景，不重新写整集。replacements以原scene_id为键，"
    "每个值是该场替换后的完整场景列表，允许一场拆成多场，程序按原位置合并。"
    "一场只能有一个连续时空；若问题涉及跨夜、跨日或地点跳跃，必须拆为独立场景并写清转场，"
    "不能只改time标签、在action里加'次日'或照搬意见中的模糊替代说法。"
    "只修issues指出的问题，不进行新一轮改编。拆场时分配原有动作和台词，只补必要的衔接。"
    "除指出的缺失外，不补入原场未安排的其他情节。除指出的对白错误外，原台词的文字、说话人及发声方式原样迁移，不新增或扩写台词。"
    "保留原场承担的主要事件、来源和结尾；source_quote逐字复制完整原文中的连续短句，可跨相邻区段。"
    "不要返回未点名场景、全片标题或新提纲；不要重复已在相邻场景完成的动作。"
)


def scene_revision_schema(payload, scene_ids):
    scene = screenplay_schema(payload)['properties']['scenes']['items']
    return obj({'replacements': obj({sid: array(scene, 1, 32) for sid in sorted(scene_ids)})})


def apply_scene_revision(script, revision, scene_ids, payload, method):
    replacements = revision['replacements']
    if set(replacements) != set(scene_ids) or not set(scene_ids) <= {s['scene_id'] for s in script['scenes']}:
        raise ValueError('分场修订只能替换已点名的场景，且不能遗漏')
    if any(not scenes for scenes in replacements.values()):
        raise ValueError('不能通过删除整场来消除问题')
    data = copy.deepcopy(script)
    data['scenes'] = [new for scene in script['scenes']
                      for new in replacements.get(scene['scene_id'], [scene])]
    return normalize_screenplay(data, payload, method)


def render_screenplay(script):
    lines = [f"# {script['video_title']} · 分场剧本", '', script['summary'], '']
    for scene in script['scenes']:
        lines += [f"## {scene['scene_id']} · {scene['location']} · {scene['time']}", '',
                  f"转场：{scene['transition']}；本场作用：{scene['purpose']}", '',
                  '进入状态：' + scene['entry_state'], '']
        for unit in scene['units']:
            lines += [f"{unit['unit_id']}　{unit['action']}", '']
            for t in unit['turns']:
                mode = {'offscreen_dialogue': '画外', 'visible_dialogue': '现场',
                        'chat_message': '屏幕消息', 'singing': '无词哼唱'}.get(t['delivery_mode'], t['delivery_mode'])
                lines += [f"{t['speaker_name']}（{mode}）：{t['text']}", '']
            if unit['sfx']:
                lines += ['声音：' + unit['sfx'], '']
        lines += ['离开状态：' + scene['exit_state'], '']
    return '\n'.join(lines)


def for_direction(script):
    """The director sees the established screenplay, not a second source novel."""
    result = {k: copy.deepcopy(script[k]) for k in ('video_title', 'hook', 'summary', 'episode_contract')}
    result['scenes'] = [{**{k: copy.deepcopy(s[k]) for k in
                            ('scene_id', 'location', 'time', 'transition', 'purpose', 'entry_state', 'exit_state')},
                        'units': [{k: copy.deepcopy(u[k]) for k in ('unit_id', 'action', 'turns', 'sfx')}
                                  for u in s['units']]}
                       for s in script['scenes']]
    return result
