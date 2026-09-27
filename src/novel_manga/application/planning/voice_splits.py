"""Ask the director for explicit cuts when an authored shot mixes thought and native speech.

Dialogue stays authored. Only the affected shot's pictures/timing are directed again, before binding.
"""
from __future__ import annotations
import copy
import json

from novel_manga.llm.client import ask_json, obj
from novel_manga.llm.config import endpoint_settings, planner_endpoint_name
from novel_manga.planning.storyboard import authored_sound, SOUND_PREFIX
from novel_manga.story.compilation import spoken_chars, text_chunks

PICTURE_FIELDS = ('景别', '摄影角度', '画面内容 / 动作', '机位 / 运镜 / 连续性', '叙事目的')


def groups(row, max_seconds=15):
    lines = [s.strip() for s in str(row.get('台词 / 声音') or '').splitlines()
             if s.strip() and not s.strip().startswith(SOUND_PREFIX)]
    sound = authored_sound(row.get('台词 / 声音', ''))
    if sound.problems or len(lines) != len(sound.turns):
        raise ValueError('mixed-voice shot has an unparseable dialogue cell')
    result = []
    for line, original in zip(lines, sound.turns):
        key = (bool(original.get('inner_monologue')), original['written_speaker'])
        for text in text_chunks(original['text'], max(1, int((max_seconds - 2) * 4))):
            turn = {**original, 'text': text}
            written = line[:line.index('：“') + 2] + text + '”'
            seconds = spoken_chars(text) / 4 + 1
            if result and result[-1]['key'] == key and result[-1]['seconds'] + seconds <= max_seconds:
                result[-1]['lines'].append(written); result[-1]['turns'].append(turn); result[-1]['seconds'] += seconds
            else:
                result.append({'key': key, 'lines': [written], 'turns': [turn], 'seconds': seconds})
    return result


def needs_split(row):
    turns = [t for t in authored_sound(row.get('台词 / 声音', '')).turns
             if t['delivery_mode'] in {'visible_dialogue', 'offscreen_dialogue'}]
    return any(t.get('inner_monologue') for t in turns) and (
        not all(t.get('inner_monologue') for t in turns) or len({t['written_speaker'] for t in turns}) > 1)


def split_authored(authored, source, *, max_seconds=15, ask=None):
    """Return a new sheet and per-shot model evidence; untouched rows and all spoken words stay identical."""
    result = copy.deepcopy(authored); result['shots'] = []; records = []
    used_ids = {str(s['镜号']) for s in authored['shots']}
    ask = ask or (lambda prompt, schema: ask_json([{'type': 'text', 'text': prompt}], schema,
                       name='postmix_director_split', max_tokens=4000,
                       settings=endpoint_settings(planner_endpoint_name())))
    for original in authored['shots']:
        if not needs_split(original):
            result['shots'].append(copy.deepcopy(original)); continue
        partitions = groups(original, max_seconds)
        schema = obj({'shots': {'type': 'array', 'minItems': len(partitions), 'maxItems': len(partitions),
                    'items': obj({**{k: {'type': 'string'} for k in PICTURE_FIELDS},
                                  '预算秒': {'type': 'number', 'minimum': 4, 'maximum': max_seconds},
                                  '音效': {'type': 'string'}})}})
        prompt = ('此镜混有心声与现场发言，后期心声必须与其他发声分开。请作为导演按下面的连续对白组明确拆镜。'
                  '每组对应一镜，不能重复动作、改变事件先后或增加剧情。相邻镜头有起点、动作和末态交接；'
                  'thought=true的组只有后期心声，画面人物不能按该组台词开口，也不能让别人替心声说话人表演说话；'
                  '不要全部停在开场瞬间。保留原地点、人物与叙事意图；对白由程序逐字放回，不需重写。'
                  '保留必要的原有音效，并把音效分配到实际发生动作的镜头。每镜预算含完整发言及动作，'
                  f'至少4秒，最多{max_seconds:g}秒，按每秒4个汉字给台词留足时间。只输出JSON。\n'
                  '原镜：' + json.dumps(original, ensure_ascii=False) + '\n顺序固定的对白组：'
                  + json.dumps([{'group': i, 'lines': p['lines'], 'thought': p['key'][0],
                                 'minimum_seconds': max(4, p['seconds']), 'maximum_seconds': max_seconds}
                                for i, p in enumerate(partitions, 1)], ensure_ascii=False)
                  + '\n当前章节原文（只作事实依据）：' + source)
        error = ''; answer = None
        for _ in range(2):
            answer = ask(prompt + ('\n上一回复：' + json.dumps(answer, ensure_ascii=False)
                                   + '\n只修正以下错误：' + error if error else ''), schema)
            try:
                shots = answer.get('shots') or []
                if len(shots) != len(partitions):
                    raise ValueError('director must return one shot for every dialogue group')
                for index, (part, shot) in enumerate(zip(partitions, shots), 1):
                    minimum = max(4, sum(spoken_chars(t['text']) / 4 + 1 for t in part['turns']))
                    missing = [k for k in PICTURE_FIELDS if not shot.get(k)]
                    if missing:
                        raise ValueError(f'第{index}镜缺少完整画面字段：{missing}')
                    if not minimum <= float(shot['预算秒']) <= max_seconds:
                        raise ValueError(f"第{index}镜预算秒填了{shot['预算秒']}，必须在{minimum:g}–{max_seconds:g}秒内以容纳全部台词")
                checked = ask('只核对拆镜是否遵守以下原镜与固定对白组。心声组不得包含现场说话、口型或他人替心声说话；'
                              '动作只发生一次，顺序和地点保持。只列明确矛盾，不提美术建议；无问题返回空problems数组。\n'
                              + prompt + '\n候选拆镜：' + json.dumps(answer, ensure_ascii=False),
                              obj({'problems': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6}}))
                if 'problems' not in checked or checked['problems']:
                    raise ValueError('拆镜语义检查：' + str(checked.get('problems', '未得到检查结果')))
                break
            except (ValueError, KeyError, TypeError) as exc:
                error = str(exc)
        else:
            raise ValueError(f"镜号{original['镜号']}心声拆镜未通过：{error}")
        for i, (part, shot) in enumerate(zip(partitions, shots), 1):
            sid = str(original['镜号']) if i == 1 else str(original['镜号']) + f'.v{i}'
            if i > 1 and (sid in used_ids or len(sid) > 8):
                raise ValueError('voice split would collide with an authored shot identifier')
            used_ids.add(sid)
            sound = '\n'.join(part['lines'])
            if shot.get('音效'):
                sound += '\n声音：' + shot['音效']
            result['shots'].append({**original, **{k: shot[k] for k in PICTURE_FIELDS},
                                    '镜号': sid, '预算秒': shot['预算秒'], '台词 / 声音': sound})
        records.append({'shot_id': original['镜号'], 'original': original, 'answer': answer, 'semantic_check': checked,
                        'result': copy.deepcopy(result['shots'][-len(partitions):])})
    return result, records
