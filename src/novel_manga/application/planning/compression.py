"""Shorten an existing adaptation without generating another outline."""
from __future__ import annotations

import json
import time
from copy import deepcopy

import httpx

from novel_manga.llm.config import endpoint_key, endpoint_order
from novel_manga.llm.responses import extract_json
from novel_manga.llm.transport import post_any
from novel_manga.planning.text import stage_seconds, spoken_chars

ROUNDS = 2
TOTAL_SECONDS = 180.0


def compress_plan(raw, payload, schema, shots, errors, *, base_url, model, max_tokens,
                  timeout, ctx, seed=None, feedback=''):
    from novel_manga.application.planning.requests import qwen_default
    durations = [{'stage': shot.get('label'),
                  'seconds': float(shot.get('duration_seconds', stage_seconds(shot.get('turns', []), ctx=ctx)))}
                 for shot in shots]
    slots = [(ci, si, ti, turn) for ci, clip in enumerate(raw['clips'])
             for si, stage in enumerate(clip['stages']) for ti, turn in enumerate(stage['turns'])
             if turn.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue'}]
    spoken = sum(spoken_chars(turn.get('text', '')) for *_, turn in slots)
    total = sum(d['seconds'] for d in durations)
    # Leave room for the packer's extra boundaries. Only the actual spoken
    # words change; frame descriptions, source quotes and durations stay intact.
    target = min(ctx.episode_seconds_max * 0.9, ctx.episode_seconds_target + 15)
    overhead = max(0, total - spoken / 4)
    budget = max(0, int((target - overhead) * 4))
    entries, rows = [], []
    for index, (ci, si, ti, turn) in enumerate(slots, 1):
        entries.append({'type': 'object', 'additionalProperties': False,
            'required': ['index', 'text'], 'properties': {
                'index': {'type': 'integer', 'enum': [index]},
                'text': {'type': 'string'}}})
        rows.append({'index': index, 'clip': ci + 1, 'stage': si + 1,
            'speaker_name': turn['speaker_name'], 'text': turn['text']})
    stage_labels = [f'{ci + 1}:{si + 1}' for ci, clip in enumerate(raw['clips'])
                    for si, stage in enumerate(clip['stages'])]
    covered_segments = {stage['segment_id'] for clip in raw['clips'] for stage in clip['stages']}
    edit_schema = {'type': 'object', 'additionalProperties': False, 'required': ['edits', 'dropped_stages'],
        'properties': {'edits': {'type': 'array', 'minItems': 0, 'maxItems': len(entries),
            'items': {'type':'object','additionalProperties':False,'required':['index','text'],
                'properties':{'index':{'type':'integer','enum':list(range(1,len(entries)+1))},'text':{'type':'string'}}}},
            'dropped_stages': {'type': 'array', 'maxItems': max(0, len(stage_labels) - len(covered_segments)),
                'items': {'type': 'string', 'enum': stage_labels}}}}
    instructions = (
        f'本轮整章所有保留台词合计约{budget}个汉字，严格以这项总预算和语义完整为编辑目标。'
        '不要逐句保留：可以删掉非关键发言、合并同一阶段的信息，再给关键句重新分配字数。'
        '先决定核心信息，再写出完整短句。绝不能用残句通过字数检查。'
        '你是短剧剪辑编剧。压缩已有分镜中的可听对白，不重新改编本章、不重做提纲。'
        '目标是实际可播放时长不超过明确上限，并尽量靠近目标秒数。'
        '保留既定主线、核心事件、因果、问答、人物归属和结尾；所有原文区段仍须有镜头引用。'
        '先删不带新信息的感叹、复述和重复反应，再把同一人的连续对白改成简短的等义表达。'
        '保留镜头的画面、动作与顺序原样保留，不得改变谁做什么、删掉核心转折或新增剧情。'
        '逐个对照已有提纲的retained_dialogue保留其实际信息，可以缩短措辞，不能只在画面说明里写“解释了”。'
        '不要通过降低duration_seconds、加速语音、删除全部对白或伪造估时来过关；'
        '时长会按实际台词和镜头重新计算。source_quote仍逐字复制对应原文。'
        '只返回edits中的index与改短后的text，以及dropped_stages，不输出完整分镜、提纲或压缩建议。'
        '每条保留阶段的spoken_lines都须编辑一次；删除阶段的台词不用重复填写空字符串，直接省略该index。'
        'spoken_character_budget是整章共享预算，不是逐句按比例截断。保留的台词必须是语义完整、能自然听懂的表达，'
        '不得切掉词尾或保留半句话来凑字数；重要句可以分配更多字数。可把同一阶段、同一说话人的信息合到其一条对白，'
        '被合并的重复句或没有新信息的感叹用空字符串删除；重要问答仍要分别保留。'
        '如果空镜和重复反应本身占时过多，用dropped_stages列出冗余阶段的“片段序号:阶段序号”。'
        '只能删重复表情、重复环境展示、重复解释同一事实的阶段；不得删唯一关键动作、场景交接、'
        '独有问答或一个原文区段的全部引用。保留阶段必须仍能衔接，不能将删掉阶段的台词或动作暗示为已发生。'
        'dropped_stages 中的阶段不能同时保留任何非空对白编辑；决定删阶段时对应台词都须明确留空。'
        '每个原文区段至少保留一个引用阶段，程序会检查这些删除约束。'
        '无需删镜时dropped_stages为空。不要为满足固定发声字数保留多余空镜。'
        '不得删掉关键决定、否定词、条件、因果、数字含义或把其他人的话搬到本人的嘴里。'
        '只有source_quote须逐字引用原文；turns.text是改编对白，可以大幅缩短而保留意思。'
        '原始长对白引句只作事实参考，不是要求逐字读完的清单。'
        '减少长背景介绍的可听字数，用已有画面承担非关键背景，重要决定与问答仍须真正听到。'
    )
    # The editor needs this chapter's events and lines, not the book-wide casting
    # catalogue or the original planning instructions demanding long dialogue.
    context = {'chapter_index': payload.get('chapter_index'), 'chapter_title': payload.get('chapter_title'),
               'source_segments': payload.get('segments', []),
               'existing_outline': ctx.outline,
               'stages': [{'address': f'{ci+1}:{si+1}',
                           **{key: stage.get(key) for key in ('segment_id', 'event', 'start_state', 'end_state')}}
                          for ci, clip in enumerate(raw['clips']) for si, stage in enumerate(clip['stages'])],
               'spoken_lines': rows, 'spoken_character_budget': budget,
               'current_non_speech_seconds': round(overhead, 2),
               'stage_addresses': stage_labels,
               'current_stage_seconds': durations, 'current_total_seconds': sum(d['seconds'] for d in durations),
               'compression_target_seconds': ctx.episode_seconds_target,
               'compression_max_seconds': ctx.episode_seconds_max, 'issues_to_fix': errors}
    key = endpoint_key()
    endpoints = (endpoint_order(str(payload.get('chapter_index', '')) + '-compression')
                 if base_url == qwen_default() else [base_url])
    started = time.monotonic()
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        body = post_any(client, endpoints, {'Authorization': 'Bearer ' + key} if key else {}, {
            'model': model, 'temperature': 0.2, 'max_tokens': max_tokens,
            **({'seed': seed} if seed is not None else {}),
            'chat_template_kwargs': {'enable_thinking': False},
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'system', 'content': instructions +
                         ('\n上次编辑被拒绝，本次必须修正：' + feedback if feedback else '')},
                         {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)
                          + f'\n最后核对：全部text合计目标{budget}个汉字，现有{spoken}个汉字。'
                            '必须整体删并改写，不能每句话只是略微缩短。可删句留空，保留句必须完整。'
                          + '\n返回JSON对象，格式：' + json.dumps(edit_schema, ensure_ascii=False)}],
        })
    choice = body['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Compression response incomplete: ' + str(choice.get('finish_reason')))
    content = choice['message'].get('content') or ''
    edits = extract_json(content).get('edits', [])
    dropped = extract_json(content).get('dropped_stages', [])
    if set(dropped) - set(stage_labels):
        raise ValueError('Compression response tried to drop an absent stage')
    indices=[edit.get('index')for edit in edits]
    required={i for i,(ci,si,ti,turn)in enumerate(slots,1)if f'{ci+1}:{si+1}'not in dropped}
    if (any(type(i)is not int or not 1<=i<=len(slots)for i in indices)
            or len(set(indices))!=len(indices)or not required<=set(indices)):
        raise ValueError('Compression response did not address each spoken line exactly once')
    if any(not isinstance(edit.get('text'), str) for edit in edits):
        raise ValueError('Compression response requires a string for each spoken line')
    candidate = deepcopy(raw)
    for edit in edits:
        ci,si,ti,_=slots[edit['index']-1]
        candidate['clips'][ci]['stages'][si]['turns'][ti]['text'] = edit['text']
    conflicts=[f'{slots[e["index"]-1][0]+1}:{slots[e["index"]-1][1]+1}'for e in edits
               if str(e['text']).strip()and
               f'{slots[e["index"]-1][0]+1}:{slots[e["index"]-1][1]+1}'in dropped]
    if conflicts:
        raise ValueError('Deletion conflicts with retained dialogue edits at stages: ' + ', '.join(conflicts))
    remaining_segments = {stage['segment_id'] for ci, clip in enumerate(candidate['clips'], 1)
                          for si, stage in enumerate(clip['stages'], 1) if f'{ci}:{si}' not in dropped}
    if lost := covered_segments-remaining_segments:
        raise ValueError('Deletion removes all source coverage for segments: ' + ', '.join(sorted(lost)))
    for ci, clip in enumerate(candidate['clips'], 1):
        clip['stages'] = [stage for si, stage in enumerate(clip['stages'], 1) if f'{ci}:{si}' not in dropped]
        for stage in clip['stages']:
            stage['turns'] = [turn for turn in stage['turns'] if turn.get('text')
                or turn.get('delivery_mode') not in {'visible_dialogue', 'offscreen_dialogue'}]
            if not stage['turns']:
                stage['turns'] = [{'speaker_name': '', 'delivery_mode': 'silent_action',
                    'text': '', 'emotion': '', 'chat_target': ''}]
    candidate['clips'] = [clip for clip in candidate['clips'] if clip['stages']]
    return candidate, {
        'model': model, 'seconds': round(time.monotonic() - started, 1),
        'finish_reason': choice.get('finish_reason'), 'usage': body.get('usage', {}),
        'before_seconds': round(context['current_total_seconds'], 2),
        'target_seconds': ctx.episode_seconds_target, 'max_seconds': ctx.episode_seconds_max,
        'edit_target_seconds': target, 'spoken_character_budget': budget, 'edits': edits,
        'dropped_stages': dropped,
    }
