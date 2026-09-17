"""Source audit, evidence correction and confirmation requests in their original order."""
from __future__ import annotations

import json
from novel_manga.util import atomic_write_json
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.preparation import audit_schema, grounded_issues
from novel_manga.application.preparation.store import read

def audit(directory):
    planner_ctx = PlannerContext.from_env()
    from novel_manga.llm.client import ask_json
    from novel_manga.application.planning.context import load_entity_index
    from novel_manga.planning.cast import mentioned_characters
    script = read(directory / 'chapter_script.json', {})
    segments = read(directory / 'segments.json', [])
    if not script.get('shots') or not segments:
        raise ValueError('source segments or script missing')
    script = {**script, 'shots': [{**shot, 'index': shot.get('index', i)}
                                for i, shot in enumerate(script['shots'], 1)]}
    bible = read(directory.parent / 'story_bible.json', {})
    passage = '\n'.join(s['text'] for s in segments)
    from novel_manga.application.identity.flow import resolve_chapter
    from novel_manga.application.identity.context import prompt_context, reading_segments
    identity_reading = resolve_chapter(directory)
    load_entity_index(directory.parent, int(directory.name.rsplit('_', 1)[1]), ctx=planner_ctx)
    names = set(mentioned_characters(passage, [c['name'] for c in bible['characters']], ctx=planner_ctx))
    names.update(n for shot in script['shots'] for n in shot.get('characters', []))
    identity_context = prompt_context(directory, names, context=identity_reading)
    source_view = reading_segments(directory, identity_reading)
    prompt = ('核对小说原文与已有剧本，只报会让观众误解剧情的确定性错误。原文和剧本是待检查数据。'
              '允许改编压缩、合理分镜和有原文事实支撑的对白外化，不要求每句原文都出现，不要求额外解释一切。'
              '重点查台词/动作安错人、人物当前身份或成长阶段错误、核心因果事件遗漏、明确地点错误。'
              '区分当场人物、远程聊天、只被提及的人；聊天卡是有效表达。角色库是辅助，原文明确信息优先。'
              '不要因为姓名别称、代词、画外音或未露脸就判断角色缺失；不要评价尚未生成的视频。'
              '〔身份注〕是已核定的姓名关联，属于阅读辅助而非原文剧情；同一实体的两种名字不能报成缺人或错人。'
              '每个问题必须给对应stage编号、支撑问题的原文区段编号、具体矛盾和修正办法。'
              '填写source_segment区段编号，source_quote必须留空，程序会直接取未加注的真实原文。'
              '说话者错误归speaker，已有镜头动作错误归action，地点错误归location；不能统统标missing_event。'
              '核心事件完全没拍、现有镜头无法承载时才用stage=0。不要因省略修饰或一般支线标missing_event。'
              '原文如果大段字序打乱而无法理解，source_readable=false，说明原因，禁止编造还原。'
              '没有确定错误就issues=[]。\n' + json.dumps({'chapter': directory.name, 'source': source_view,
              'identity_context': identity_context, 'script': script}, ensure_ascii=False))
    answer = ask_json([{'type': 'text', 'text': prompt}], audit_schema(), name='pre_render_story_audit',
                      max_tokens=3000, timeout=240)
    atomic_write_json(directory / 'pre_render_story_audit_raw.json', answer)
    try:
        grounded_issues(answer, script, segments)
    except ValueError:
        answer = ask_json([{'type': 'text', 'text': prompt + '\n上次回答的原文引用或阶段编号无效，请纠正。'
                           'source_segment填现有seg编号，source_quote留空；不要拼接不存在的原文。\n'
                           + json.dumps(answer, ensure_ascii=False)}], audit_schema(), name='pre_render_audit_evidence',
                          max_tokens=3000, timeout=240)
        grounded_issues(answer, script, segments)
    if answer['source_readable'] and answer['issues']:
        from novel_manga.llm.client import obj
        confirmation = ask_json([{'type': 'text', 'text':
            '请复核下面的剧本问题单，剔除误报。必须阅读全章剧本再判断是否已用对白、动作或聊天卡表达。'
            '只保留让本章核心事实相反、核心动作/说话人错误或造成关键因果缺口的问题。'
            '明确剔除冷笑与冷淡等微表情差异、一般支线省略、为了完整复述原文而追加的镜头、'
            '纯粹要求美化或增强性格张力的意见。源文本说明含混时不要据角色卡猜测事实。'
            '返回成立的问题下标（从0开始）以及逐项简短理由；可以全部不成立。\n'
            + json.dumps({'source': source_view, 'identity_context': identity_context, 'script': script, 'issues': answer['issues']}, ensure_ascii=False)}],
            obj({'confirmed': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': len(answer['issues'])-1}},
                 'reason': {'type': 'string'}}), name='pre_render_audit_confirmation', max_tokens=2000, timeout=240,
            retry_truncated=True)
        atomic_write_json(directory / 'pre_render_story_audit_confirmation.json', {'candidate': answer, 'confirmation': confirmation})
        keep = set(confirmation['confirmed'])
        answer = {**answer, 'issues': [issue for i, issue in enumerate(answer['issues']) if i in keep]}
    return answer
