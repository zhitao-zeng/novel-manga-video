"""Method-specific direction and a loss-checked handoff to existing clips/stages."""
from __future__ import annotations

from collections import Counter
import copy
import math
from .scenes import obj, text, array, VERSION
from novel_manga.story.fields import actions_field, extras_field, cast_field
from novel_manga.planning.constants import SHOT_SCALES
from novel_manga.planning.text import spoken_turn_seconds


class DirectionIssues(ValueError):
    def __init__(self, problems):
        self.problems = problems
        self.scene_ids = {p['scene_id'] for p in problems}
        super().__init__('; '.join(p['issue'] for p in problems))


def dialogue_seconds(turns):
    return 1 + sum(spoken_turn_seconds(t['text']) for t in turns
                   if t['delivery_mode'] in {'visible_dialogue', 'offscreen_dialogue'})


def direction_budget(script):
    """Use the acceptance calculation to supply feasible per-unit dialogue groups."""
    scenes = {}
    for scene in script['scenes']:
        costs, groups = {}, []
        for unit in scene['units']:
            group, visible = [], set()
            for turn in unit['turns']:
                if turn['delivery_mode'] not in {'visible_dialogue', 'offscreen_dialogue'}:
                    continue
                costs[turn['turn_id']] = spoken_turn_seconds(turn['text'])
                speaker = {turn['speaker_name']} if turn['delivery_mode'] == 'visible_dialogue' else set()
                if group and (dialogue_seconds([*group, turn]) > script['clip_seconds_max'] or len(visible | speaker) > 1):
                    groups.append({'turn_ids': [t['turn_id'] for t in group], 'minimum_seconds': math.ceil(dialogue_seconds(group))})
                    group, visible = [], set()
                group.append(turn)
                visible |= speaker
            if group:
                groups.append({'turn_ids': [t['turn_id'] for t in group], 'minimum_seconds': math.ceil(dialogue_seconds(group))})
        scenes[scene['scene_id']] = {'turn_seconds': costs, 'suggested_groups': groups}
    return {'shot_overhead_seconds': 1, 'maximum_shot_seconds': script['clip_seconds_max'], 'scenes': scenes}


def direction_prompt(method):
    return (
        f"你按【{method.name}】把已经完成的分场剧本拆成逐镜导演稿。\n" + method.drafting + "\n"
        "输入是已核对原文的分场剧本和人物资料；剧情以分场稿为准，不重新改编原著。先读完剧本及结尾，再写整体设计和逐场镜头。"
        "场景数量、正文段落数量都不等于镜头数；每场按必要动作、信息揭示与反应拆成可拍镜头。"
        "任何一镜不得包含睡眠到醒来、跨日旅行、从生肉到烤熟等省略过程，必须有独立镜头和明确跳时。"
        "不能将这些过程写成一镜的'随后'来规避切镜。\n"
        "每镜unit_ids引用承担的正文段落；可用多镜落实一段，也可用一镜承载几段连续行为。"
        "所有units都必须由镜头落实，不能只引用它的编号却不拍主要动作。"
        "同一主要动作只能实际发生一次；细节/反应镜承接结果，不重新开始该动作。\n"
        "turn_ids选择本镜说的原稿台词，按原稿先后各落实一次，实际文字和说话人由程序复制。"
        "不改写或新增台词，不把说话内容写进sfx或event。多人对话一镜只保留一位正脸说话者，其他按原稿画外。"
        "动作段可turn_ids=[]；不需要为了有声音让人物开口。\n"
        "start_state是这个镜头动作开始前的状态，不能提前放入结果；event写连续可見过程，end_state写结果。"
        "相邻镜若连续，人物朝向、双手持物和状态接得上；换空间、闪回、跳时按已经写明的场景转场。"
        "pure环境或手部镜不强行把主角的脸放进in_frame；人物身份与入镜是两回事。"
        "actions的actor/target可以是人、extras中的动物、物件、空目标或合法自我动作，不能换成演员名单里另一个人。"
        "action只写谓语，event是完整自然语言事件，不机械重复主体宾语。\n"
        "camera明确拍摄位置、高度、取景范围和必要运动，不能要求同一时刻特写又看清完整盆地。"
        "sfx把原场声音意图落实到这个镜头的起止和具体声源，不用不存在的'烟雾声/泪水声'。"
        "duration_seconds给出本镜动作、台词、停顿和反应需要的时长；不是所有静默镜都4秒。"
        "单镜可短至1秒、不超single_shot_max_seconds，不能把素材片长下限当成剪辑镜长。"
        "dialogue_budget已由程序按验收规则计算：一镜对白最低秒数为shot_overhead_seconds加所选台词turn_seconds之和，向上取整。"
        "suggested_groups给出同一正文单元内可容纳的分组；可以拆细、穿插反应镜，不能为了保持一镜合并到超限。"
        "先分配这些台词组，再设计每镜动作与反应，duration_seconds不得低于所选台词的最低秒数。"
        "长动作按可见状态闭合处拆开；全集按已有目标与上限预算安排，先把各镜时长加总再交稿。"
        "不得为了缩短而漏掉后半场、高潮或结尾。对照所有scenes完整输出，不能提前结束。"
        "只输出Schema JSON。"
    )


def direction_schema(script, payload, method, *, scene_ids=None):
    directions = {}
    cap = payload['planning_budget']['clip_seconds'][1]
    for scene in script['scenes']:
        if scene_ids is not None and scene['scene_id'] not in scene_ids:
            continue
        units = [u['unit_id'] for u in scene['units']]
        turns = [t['turn_id'] for u in scene['units'] for t in u['turns']]
        shot = obj({
            'unit_ids': array({'type': 'string', 'enum': units}, 1, len(units)),
            'turn_ids': {**array({'type': 'string', **({'enum': turns} if turns else {})}, maximum=len(turns)),
                         **({'maxItems': 0} if not turns else {})},
            'purpose': text('本镜让观众新知道什么或接收哪个结果'),
            'start_state': text(), 'event': text(), 'end_state': text(),
            'camera': text(), 'light': text(), 'sfx': text(),
            'cut': text('本镜结束时在哪个动作或声音处切，下一镜怎样接'),
            'duration_seconds': {'type': 'number', 'minimum': 1, 'maximum': cap},
            'shot_scale': {'type': 'string', 'enum': SHOT_SCALES},
            'in_frame': cast_field(payload['available_characters']),
            'extras': extras_field(), 'actions': actions_field(),
        })
        directions[scene['scene_id']] = obj({
            'method_details': obj({k: text(v) for k, v in method.beat_fields}),
            'shots': array(shot, 1, 24),
        })
    return obj({'method_plan': obj({k: text(v) for k, v in method.episode_fields}),
                'directions': obj(directions)})


def project_direction(script, direction):
    """Bind source and dialogue in code; the director cannot rename/rewrite them."""
    clips, errors = [], []
    def reject(scene_id, issue):
        errors.append({'scene_id': scene_id, 'issue': issue})
    wanted = {s['scene_id'] for s in script['scenes']}
    if set(direction.get('directions', {})) != wanted:
        raise DirectionIssues([{'scene_id': sid, 'issue': f'{sid}: 导演稿必须完整覆盖全部场景'}
                               for sid in sorted(wanted ^ set(direction.get('directions', {})))])
    for scene in script['scenes']:
        sid = scene['scene_id']
        units = {u['unit_id']: u for u in scene['units']}
        turns = {t['turn_id']: t for u in units.values() for t in u['turns']}
        used_units, used_turns = set(), []
        stages = []
        for n, shot in enumerate(direction['directions'][sid]['shots'], 1):
            ids, tids = shot['unit_ids'], shot['turn_ids']
            if not ids or any(u not in units for u in ids) or any(t not in turns for t in tids):
                reject(sid, f'{sid}镜{n}: 引用不属于本场正文')
                continue
            selected = [units[u] for u in ids]
            used_units.update(ids); used_turns.extend(tids)
            refs = list({(r['segment_id'], r['source_quote']): r for u in selected for r in u['source_refs']}.values())
            if not refs:
                reject(sid, f'{sid}镜{n}: 没有原文依据'); continue
            dialogue = [{k: v for k, v in turns[t].items() if k != 'turn_id'} for t in tids]
            floor = dialogue_seconds(dialogue)
            allocated = max(shot['duration_seconds'], math.ceil(floor))
            if shot['duration_seconds'] <= 0 or allocated > script['clip_seconds_max']:
                reject(sid, f'{sid}镜{n}: 台词至少估算{floor:g}秒，超出单段上限；按dialogue_budget分配到相邻镜头，不删改台词')
            visible = {t['speaker_name'] for t in dialogue if t['delivery_mode'] == 'visible_dialogue'}
            if len(visible) > 1 or not visible <= set(shot['in_frame']):
                reject(sid, f'{sid}镜{n}: 可见说话人与入镜列表不一致')
            stages.append({**copy.deepcopy(shot), 'segment_id': selected[0]['source_segment_id'],
                           'source_quote': selected[0]['source_quote'], 'source_refs': refs,
                           'duration_seconds': allocated,
                           **({'timing_adjustment': {'director_seconds': shot['duration_seconds'],
                               'speech_estimate_seconds': floor, 'allocated_seconds': allocated}}
                              if allocated != shot['duration_seconds'] else {}),
                           'scene_id': sid, 'scene_time': scene['time'], 'scene_transition': scene['transition'],
                           'shot_id': f'{sid}-SH{n:02d}',
                           'turns': dialogue or [{'speaker_name': '', 'delivery_mode': 'silent_action',
                                                 'text': shot['event'], 'emotion': '', 'chat_target': ''}]})
        if used_units != set(units):
            reject(sid, f'{sid}: 漏拍正文 ' + ', '.join(sorted(set(units)-used_units)))
        if used_turns != list(turns):
            reject(sid, f'{sid}: 台词漏说、重复或顺序改变')
        clips.append({'clip_id': sid, 'location': scene['location'], 'avoid': '',
                      'characters': list(dict.fromkeys(c for s in stages for c in s['in_frame'])), 'stages': stages})
    if errors:
        raise DirectionIssues(errors)
    cited = {r['segment_id'] for c in clips for s in c['stages'] for r in s['source_refs']}
    omitted = {r['segment_id']: o['reason'] for o in script.get('omissions', [])
               for r in o['source_refs'] if r['segment_id'] not in cited}
    return {'video_title': script['video_title'], 'hook': script['hook'], 'summary': script['summary'],
            'clips': clips, 'skipped_segments': [{'segment_id': s, 'reason': reason} for s, reason in omitted.items()],
            'scene_pipeline': VERSION}


def validate_handoff(raw, blueprint):
    expected = project_direction(blueprint, blueprint['direction'])
    actual = [(c.get('location'), s) for c in raw.get('clips', []) for s in c.get('stages', [])]
    target = [(c['location'], s) for c in expected['clips'] for s in c['stages']]
    if len(actual) != len(target):
        return ['导演拆镜的镜数在交接后改变']
    errors = []
    if not blueprint.get('review', {}).get('completed'):
        errors.append('场景和导演稿尚未完成内容复核')
    if not blueprint.get('script_review', {}).get('completed') or blueprint['script_review'].get('issues'):
        errors.append('分场稿尚未通过前置原文核对')
    if raw.get('skipped_segments') != expected['skipped_segments']:
        errors.append('原文省略范围在交接后改变')
    # These are authored decisions, not fields for a downstream repair to guess again.
    fields = ('scene_id', 'shot_id', 'unit_ids', 'turn_ids', 'source_quote', 'segment_id',
              'source_refs', 'scene_time', 'duration_seconds', 'start_state', 'event', 'end_state',
              'camera', 'light', 'sfx', 'in_frame', 'turns')
    for (location, got), (where, want) in zip(actual, target):
        if location != where or any(got.get(f) != want.get(f) for f in fields):
            errors.append(f"{want['shot_id']}: 场景或导演稿在交接后被改写")
    errors.extend(i['issue'] for i in blueprint.get('review', {}).get('issues', []))
    return errors


def review_schema(script, part, direction=None):
    item_ids = ([s['scene_id'] for s in script['scenes']] if part == 'script' else
                [s['shot_id'] for c in project_direction(script, direction)['clips'] for s in c['stages']])
    upstream = ([s['segment_id'] for s in script['source_segments']] if part == 'script' else
                [u['unit_id'] for s in script['scenes'] for u in s['units']])
    return obj({'issues': array(obj({
        'item_id': {'type': 'string', 'enum': ['episode', *item_ids],
                    'description': '有问题的场景或镜头；缺整段用episode，具体文字由程序提取'},
        'upstream_id': {'type': 'string', 'enum': ['', *upstream],
                        'description': '原文区段或分场正文单元；仅内部矛盾可留空，漏整段时必须选择'},
        'issue': text('具体事实或可拍性问题及观众影响，不评分'),
        'repair': text('保持原意的明确修订要求'),
    }), maximum=12)})


REVIEW_PROMPT = (
    "你核对已定稿的分场剧本与实际绑定后的镜头。剧本是剧情依据，只报镜头没有落实或改写剧本的明确问题，"
    "不重新改编原著、不评分、不为风格不同挑错。item_id选有问题的shot_id；"
    "upstream_id选择对应分场稿unit_id，内部矛盾可留空。原句由程序取，不需要重打引文。"
    "检查每场实际动作、台词、声音和结尾是否由镜头承担。"
    "逐镜看单一连续时空能否完成动作，是否跨夜/跨日/跨地点却装成一镜，"
    "关键动作或道具是否提前、重复、消失，表演是否只有'意识到/接受现实'而没有可见或可听载体。"
    "摄影范围能否看到它声称展示的内容；入镜人物与对白是否正确；短发等已有造型不能被动作改成长发。"
    "对明确跳时允许省略过程，对无对白不要求补台词，对照原文不要要求复述所有议论和食谱。"
    "跨镜声音桥是设计意图，不能因它需要后期就判剧情错误；不要新增家人声音或强迫冷开场。"
    "必须选择当前存在的编号定位问题；未发现明确问题则issues=[]。"
    "不要否定上游已明确的正常事实，也不要用臆测的物理限制制造问题。"
    "不要靠source_id覆盖率给整集放行。只输出JSON。"
)

SCRIPT_REVIEW_PROMPT = (
    "只核对完整原文与分场剧本，不设计摄影、不要求照抄原文句子。"
    "检查四类实质问题：主要因果或结尾缺失/改变；回忆、夜晚、次晨等不同时空被塞在同一连续场；"
    "人物猜测被当成已确认事实；装备取得、行动先后和已给人物造型发生矛盾。"
    "原文片段编号只是位置，不是完整性证明；omissions有理由也不能掩盖漏掉主要事件。"
    "书名、人物资料的后续经历不能当成本章人物已经知道的信息。"
    "可以删作者议论、压缩食谱来历、改写口语对白、用不同动作表达相同含义；"
    "不要为原文没有逐字写的正常表情、机位或微动作制造问题，不以细节数量评分。"
    "若一场里由晚上吃饭跳到次日醒来，应在剧本阶段拆场并明确转场，不能留给视频模型猜。"
    "只报会影响理解的错误。item_id选择当前scene_id，upstream_id选择对应的seg编号；"
    "场景内部矛盾可不选upstream_id；若整段原文被漏掉，item_id=episode并选择该原文区段。"
    "程序会取出对应原文和草稿作为证据，不要重打引文。"
    "不要报告草稿已经明确满足的事项。没有这类问题则issues=[]。只输出JSON。"
)


def grounded_review(review, script, direction, payload, part):
    import json
    from novel_manga.planning.preparation import source_segment_text
    written = {s['scene_id']: {k: v for k, v in s.items() if k not in {'units'}} |
               {'units': [{k: u[k] for k in ('action', 'turns', 'sfx')} for u in s['units']]}
               for s in script['scenes']}
    projected = project_direction(script, direction)['clips'] if direction is not None else []
    directed = {s['shot_id']: {k: s[k] for k in ('scene_id', 'start_state', 'event', 'end_state', 'camera',
                                                'light', 'sfx', 'in_frame', 'turns', 'extras', 'duration_seconds')}
                for c in projected for s in c['stages']}
    units = {u['unit_id']: {k: u[k] for k in ('action', 'turns', 'sfx')} for s in script['scenes'] for u in s['units']}
    owners = {u['unit_id']: s['scene_id'] for s in script['scenes'] for u in s['units']}
    written_episode = {k: script[k] for k in ('video_title', 'hook', 'summary', 'episode_contract')}
    written_episode['scenes'] = written
    bound = []
    items = written if part == 'script' else directed
    for issue in review['issues']:
        item, upstream = issue.get('item_id'), issue.get('upstream_id')
        if item not in items and item != 'episode':
            raise ValueError('审查引用了不存在的场景或镜头')
        if item == 'episode' and not upstream:
            raise ValueError('缺失内容必须指出上游原文或正文单元')
        sid = item if part == 'script' or item == 'episode' else directed[item]['scene_id']
        evidence = source_segment_text(upstream, payload['segments']) if part == 'script' else units.get(upstream)
        if upstream and not evidence:
            raise ValueError('审查引用了不存在的上游内容')
        if part == 'shots' and upstream and sid != 'episode' and owners[upstream] != sid:
            raise ValueError('镜头审查跨场引用了其他正文')
        scope = (written_episode if part == 'script' else directed) if item == 'episode' else items[item]
        bound.append({**issue, 'scene_id': sid, 'part': part,
                      'source_kind': 'novel' if part == 'script' else 'screenplay',
                      'source_quote': evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False) if evidence else '',
                      'draft_quote': json.dumps(scope, ensure_ascii=False)})
    return {'issues': bound}
