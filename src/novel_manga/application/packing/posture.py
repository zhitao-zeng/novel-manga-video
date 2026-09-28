"""Read authored poses and time/entry boundaries into the existing posture ledger.

The model classifies continuity from the source and shot sequence. Pure rules execute that decision;
packing only consumes it. Nothing here generates video or rewrites an author's shot.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from novel_manga.llm.client import ask_json, obj
from novel_manga.llm.config import endpoint_settings
from novel_manga.story.posture import POLICY, POSTURES, people, resolve, phrase
from novel_manga.story.sources import segment_ids
from novel_manga.util import atomic_write_json, read_json

FILE = 'posture_states.json'
BATCH = 12
FIELDS = ('location', 'scene_id', 'scene_time', 'scene_transition', 'visual_prompt', 'motion_prompt',
          'end_state', 'camera', 'light', 'source_quote', 'actions')
_TEXT = {'type': 'string'}
_STATE = obj({'quote': _TEXT, 'posture': {'type': 'string', 'enum': list(POSTURES)}, 'where': _TEXT})


def material(script, segments=()):
    source = {s['segment_id']: s['text'] for s in segments}
    return [{**{k: copy.deepcopy(shot.get(k)) for k in FIELDS},
             'index': int(shot.get('index', i)), 'in_frame': people(shot),
             'source': [{'segment_id': sid, 'text': source.get(sid, '')} for sid in segment_ids([shot])]}
            for i, shot in enumerate(script.get('shots') or [], 1)]


def schema(rows):
    names = list(dict.fromkeys(n for s in rows for n in s['in_frame']))
    person = obj({'name': {'type': 'string', 'enum': names} if names else _TEXT,
                  'entry': {'type': 'string', 'enum': ['continue', 'reset', 'unknown']},
                  'start': _STATE, 'end': _STATE, 'transition': _TEXT})
    return obj({'stages': {'type': 'array', 'minItems': len(rows), 'maxItems': len(rows),
                          'items': obj({'index': {'type': 'integer', 'enum': [s['index'] for s in rows]},
                                        'boundary': {'type': 'string', 'enum': ['continuous', 'reset', 'unknown']},
                                        'reason': _TEXT, 'people': {'type': 'array', 'items': person}})}})


def question(inputs):
    return ('核对分镜的姿态台账，不重写剧本，不设计新姿势。逐镜按输入顺序输出。\n'
            'boundary：continuous=与前镜属于同一段连续时间；reset=开场、跳时、回忆切入/切出或新的场次；'
            'unknown=依据不足。相同地点或scene_id不能证明时间连续；不同机位、反打、空镜不等于跳时。'
            '必须结合source原文、scene_time、光线与起点/事件/末态判断。\n'
            'people只列本镜in_frame的具名角色，不能补入画外人物。entry按每个人分别判断：'
            'continue=本镜确实承接他先前的末态；reset=首次入场或重新入场，不能沿用离场前状态；'
            'unknown=无法确定。镜头暂时不拍这个人不等于离场，已经离开后再出现也不等于原地不动。\n'
            'start/end只摘取本镜文字明确写的姿势、位置；未写姿势填未写明，位置留空，程序再按已确认的连续关系继承。'
            'where仅记桌后椅子上、窗边等场景位置，不继承画面左侧、前景等随机位改变的构图坐标。'
            '先写quote，再按quote填写posture/where。quote必须逐字引用本镜visual_prompt/motion_prompt/end_state中的依据，'
            '不得引用前镜或原文来冒充本镜明写；没有坐站位置的本镜依据就quote留空、posture填未写明、where留空。'
            '例如前镜坐着，本镜只写“抬起一根手指”，本镜start.posture必须是未写明、quote为空，entry可填continue。'
            '例如本镜写“站在窗边看报告”，start应摘录站，quote摘录“站在窗边”；end未单独交代则填未写明。'
            '身体前倾、抬手或人物气质不代表坐或站，source只帮助理解连续性，不能替剧本新编动作。'
            'transition只逐字摘录本镜明确属于这个人的姿态改变动作，没写就空字符串；不能拿另一个人的起身为他作证。'
            '不要把说话或点头当成坐站改变。结束时没写姿势表示本镜姿态未明确改变。'
            'reason简短说明连续/重置/不确定的依据，不得默认同地点就连续。只输出JSON。\n'
            + json.dumps(inputs, ensure_ascii=False))


def checked_answers(rows, answer):
    result = answer.get('stages') or []
    if [s.get('index') for s in result] != [s['index'] for s in rows]:
        raise ValueError('姿态台账回复缺镜、重复或顺序不符')
    problems = []
    for shot, row in zip(rows, result):
        names = [p['name'] for p in row['people']]
        if len(names) != len(set(names)) or set(names) != set(shot['in_frame']):
            raise ValueError(f'姿态台账镜{shot["index"]}的入镜名单不符')
        if row.get('boundary') not in {'continuous', 'reset', 'unknown'}:
            raise ValueError('姿态台账缺少连续性判断')
        text = '；'.join(str(shot.get(k) or '') for k in ('visual_prompt', 'motion_prompt', 'end_state'))
        for person in row['people']:
            if person['entry'] not in {'continue', 'reset', 'unknown'} or any(
                    person[k]['posture'] not in POSTURES for k in ('start', 'end')):
                raise ValueError('姿态台账包含未知状态')
            for state in (person['start'], person['end']):
                quote = state.get('quote', '')
                if (state['posture'] != '未写明' or state['where']) and (not quote or quote not in text):
                    problems.append(f'镜{shot["index"]} {person["name"]}的quote「{quote}」不属于本镜：'
                                    '只提取本镜明确姿态/位置，不可把前镜状态冒充本镜明写；'
                                    '本镜未写时posture填未写明，where与quote留空')
    if problems:
        raise ValueError('姿态台账证据需修正：' + '；'.join(problems))
    return result


def fill(episode_dir: Path, *, script=None, segments=None, previous=None, ask=None, write=True):
    episode_dir = Path(episode_dir)
    script = script if script is not None else json.loads((episode_dir / 'chapter_script.json').read_text(encoding='utf-8'))
    segments = segments if segments is not None else read_json(episode_dir / 'segments.json', [])
    previous = previous if previous is not None else read_json(episode_dir / FILE, {})
    rows = material(script, segments)
    prior_batches = previous.get('batches', []) if previous.get('policy') == POLICY else []
    ask = ask or (lambda prompt, contract: ask_json([{'type': 'text', 'text': prompt}], contract,
                       name='posture_continuity', max_tokens=6000, settings=endpoint_settings('local')))
    result = {'policy': POLICY, 'inputs': rows, 'stages': {}, 'issues': [], 'batches': []}
    last = {}
    for offset in range(0, len(rows), BATCH):
        batch = rows[offset:offset + BATCH]
        inputs = {'previous_shot': rows[offset - 1] if offset else None,
                  'previous_states': copy.deepcopy(last), 'shots': batch}
        cached = next((b for b in prior_batches if b['inputs'] == inputs), None)
        feedback = ''
        for attempt in range(2):
            answer = (copy.deepcopy(cached['answer']) if cached and attempt == 0
                      else ask(question(inputs) + feedback, schema(batch)))
            try:
                readings = checked_answers(batch, answer)
                if offset == 0 and readings[0]['boundary'] != 'reset':
                    raise ValueError('姿态台账开场必须作为新场景，不能继承上一集')
                stages, issues, resolved_last = resolve(batch, readings, last)
                bad_evidence = [x['detail'] for x in issues if x.get('kind') == 'reading']
                if bad_evidence:
                    raise ValueError('；'.join(bad_evidence))
                break
            except ValueError as error:
                if attempt:
                    raise
                feedback = '\n上一回复：' + json.dumps(answer, ensure_ascii=False) + '\n只修正回复协议：' + str(error)
        last = resolved_last
        result['stages'].update(stages)
        result['issues'].extend(issues)
        result['batches'].append({'inputs': inputs, 'answer': answer})
    if write:
        atomic_write_json(episode_dir / FILE, result)
    return result


def attach(shots, script, states, segments=()):
    """Consume the entire current ledger; changing an upstream source invalidates its downstream carry."""
    result = copy.deepcopy(shots)
    if not states:
        return result
    if states.get('policy') != POLICY or states.get('inputs') != material(script, segments):
        raise ValueError('姿态台账已过期，请先重新解析本集连续性')
    if states.get('issues'):
        raise ValueError('姿态交接需修正：' + '；'.join(f'镜{x["index"]} {x["detail"]}' for x in states['issues']))
    for shot in result:
        shot['_posture'] = copy.deepcopy(states['stages'].get(str(shot['index']), {}))
    return result


def changed_stages(before, after):
    """Recompile dependent clips only if their inherited request text changes."""
    def notes(record):
        return {int(i): phrase({'in_frame': list(s['start']), '_posture': s})
                for i, s in record.get('stages', {}).items()}
    old, new = notes(before), notes(after)
    return {i for i in old.keys() | new.keys() if old.get(i, '') != new.get(i, '')}
