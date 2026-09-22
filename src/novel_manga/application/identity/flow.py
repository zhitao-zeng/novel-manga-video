"""Source reading requests, retries and evidence writes in their original order."""
from __future__ import annotations
import novel_manga.episodes as ep_names

from pathlib import Path
import json
import time
from novel_manga.util import atomic_write_json
from novel_manga.story.identity import canonical_entity
from novel_manga.story.source_identity import (POLICY, usable_reading, source_schema, UnreadableSource, clean_reading, map_source_reading)
from novel_manga.application.identity.store import ChapterIdentityData, load_chapter


def reading_aliases(novel_dir) -> dict:
    """Alias → canonical name, as the lean reading established them, with evidence behind each.

    Only these may collapse two source actors onto one catalogue row; an alias of unknown provenance
    still leaves the second actor unbound, which is what the 雾月 mis-merges taught.
    """
    from pathlib import Path
    path = Path(novel_dir) / "reading_cast.json"
    if not path.is_file():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")).get("aliases") or {})
    except (OSError, ValueError, TypeError):
        return {}


def resolve_chapter(directory: Path, *, force=False, data: ChapterIdentityData | None = None):
    # Read source first; legacy dictionaries never enter this semantic call.
    from novel_manga.llm.client import ask_json
    directory = Path(directory).resolve()
    data = data if data is not None else load_chapter(directory)
    expected, saved = data.expected, data.saved
    if not force and saved.get('policy') == POLICY and saved.get('inputs') == expected and usable_reading(saved):
        return saved
    segments = expected['segments']
    if not segments:
        raise ValueError('identity resolution needs source segments')
    chapter = ep_names.chapter_of(directory.name)
    catalog = data.catalog
    prompt = (
        '只读下面小说原文，独立列出本章实际指称的主体，不读取旧人物库、不改剧本、不评价画面。'
        '每个主体一个source_id。name选择原文主要叫法；同一人的旧名、新名和称谓记入同一actors条目的forms，不能因残留改名而拆人。'
        '根据连续动作、对话问答、受事对象和后续反馈判断是否同一人；不要求原文专门解释一次改名。'
        '分身和本体即使共享灵魂或肉身，只要各自行动、对话或同时出场，就列为两个主体，不能把双方名字放进同一个条目。'
        'forms只收原文实际使用的名字/称谓，不自己制造缩写；proper是专指此主体的叫法，contextual是局部代词或代称。'
        '先判断原文是否能连贯阅读；只有字序严重乱序、无法确定连续事件时source_readable=false，actors留空，不猜测还原。'
        '人名或数字中间换行、区段从半句话开始、错别字及旧名残留都是排版或身份核对问题；叙事仍连贯就必须填source_readable=true。'
        '代词不是独立人物。天气、境界、法术、抽象概念不列为人物；短字也可能是真正名字，依据语境判断。'
        'kind区分独立个体individual、群体group、具体道具object；对事件有作用的命名道具也列出，但绝不能当人物。'
        '动物、坐骑和被召唤的生物仍是individual；不能因为被召唤、被乘坐或沉默就当作object。只有原文明确为器物才填object，含混时保留individual。'
        '群体称号即使像姓名也不能拆成单一人物；count填原文明示的群体数量，不明填0，不根据旧人物库猜。'
        'presence区分在场、画外发言和仅被提及。'
        'appearance只写原文明示的当时身体/物种/外貌，没写就留空；不得自行补成人形或兽形。'
        '所有paragraphs均引用下列原文编号，由程序摘取证据；不要输出推理过程。\n'
        + json.dumps([{'paragraph': i, **r} for i, r in enumerate(segments, 1)], ensure_ascii=False))
    if (not force and saved.get('policy') == POLICY and saved.get('inputs', {}).get('segments') == segments
            and 'source_actors' in saved and usable_reading(saved)):
        answer = {'actors': saved['source_actors'], 'actorless_confirmed': saved.get('actorless_confirmed', False)}
    else:
        answer = ask_json([{'type': 'text', 'text': prompt}], source_schema(len(segments)),
                          name='chapter_identity_source', max_tokens=4096, timeout=240)
    raw_path = directory / 'identity_source_reading.json'
    atomic_write_json(raw_path, {'policy': POLICY, 'inputs': expected, 'answer': answer})
    if answer.get('source_readable') is False:
        from novel_manga.llm.client import obj
        confirmation = ask_json([{'type': 'text', 'text':
            '只判断这章小说的事件是否还能读懂，不做人物绑定，也不要求文本排版完美。'
            '人名、数字被换行切开，区段接续半句话，旧名和改名混用，均不构成原文不可读；'
            '只有字符顺序严重打乱、无法理解连续事件才填false。\n'
            + json.dumps(segments, ensure_ascii=False)}],
            obj({'source_readable': {'type':'boolean'}, 'source_problem': {'type':'string','maxLength':200}}),
            name='source_readability_confirmation', max_tokens=500, timeout=120)
        atomic_write_json(directory / 'source_readability_confirmation.json', confirmation)
        if not confirmation['source_readable']:
            raise UnreadableSource(confirmation.get('source_problem') or 'source text is unreadable')
        answer = ask_json([{'type':'text','text':prompt + '\n原文可读性已独立复核通过；按连续事件识别主体，不要把断行或旧名残留报成字序损坏。'}],
                          source_schema(len(segments)), name='chapter_identity_readable_source', max_tokens=4096, timeout=240)
        atomic_write_json(raw_path, {'policy': POLICY, 'inputs': expected, 'answer': answer})
        if answer.get('source_readable') is False:
            raise ValueError('identity extraction failed after source readability was confirmed')
    if not answer.get('actors') and not answer.get('actorless_confirmed'):
        schema = source_schema(len(segments))
        schema['properties']['actorless_confirmed'] = {'type': 'boolean'}
        schema['required'].append('actorless_confirmed')
        answer = ask_json([{'type': 'text', 'text': prompt + '\n上一次抽取返回空主体，需独立复核。'
            '逐段检查动作发起者、对话双方和提及对象；只要有人物或动物参与，就重新完整抽取actors。'
            '只有全文确实没有任何主体（例如纯景物描写），才允许actors为空并填actorless_confirmed=true。'
            '断行、人名残留或无法完成抽取不能作为无人章节；此时填false。'}], schema,
            name='chapter_identity_empty_confirmation', max_tokens=4096, timeout=240)
        atomic_write_json(directory / 'identity_empty_confirmation.json', {'policy': POLICY, 'inputs': expected, 'answer': answer})
        if answer.get('source_readable') is False or (not answer.get('actors') and not answer.get('actorless_confirmed')):
            raise ValueError('identity extraction returned no actors without confirming an actorless chapter')
    cleaned, rejected, unresolved = clean_reading(answer, segments)
    if unresolved:
        correction = ask_json([{'type': 'text', 'text': prompt + '\n只修正下列主体的forms与原文编号；保留source_id，不输出其他主体。'
            '每个主体必须至少有一个原文实际存在的指称，不能补出原文没有的代词。\n' + json.dumps(unresolved, ensure_ascii=False)}],
            source_schema(len(segments)), name='chapter_identity_evidence', max_tokens=2500, timeout=180)
        fixed, dropped, pending = clean_reading(correction, segments)
        wanted = {r['source_id'] for r in unresolved}
        restored = [r for r in fixed['actors'] if r['source_id'] in wanted]
        atomic_write_json(directory / 'identity_evidence_repair.json', {'answer': correction, 'rejected': dropped})
        still = [r for r in unresolved if r['source_id'] not in {x['source_id'] for x in restored}] + list(pending)
        # An actor with no form anywhere in the chapter is one the reading described rather than read:
        # chapter 26 asked for 红乌鸦帮的老大, a phrase the chapter never uses.  Losing a whole chapter
        # over someone the chapter only mentions is the wrong trade - nothing is filmed for them, and
        # the discard is on the record.  On stage is the opposite case: dropping someone the chapter
        # puts in the picture is exactly the defect the binding check exists to catch, so it still stops.
        onstage = [r for r in still if r.get('presence') in {'on_stage', 'voice'}]
        if onstage:
            raise ValueError('identity actors lack grounded names: ' + ','.join(r['name'] for r in onstage))
        cleaned['actors'].extend(restored)
        rejected.extend(dropped)
        rejected.extend({'source_id': r['source_id'], 'form': r['name'], 'kind': 'ungrounded',
                         'why': '全章没有这个指称，且只被提及'} for r in still)
    result = {'policy': POLICY, 'chapter': chapter, 'at': time.strftime('%F %T'), 'inputs': expected,
              'primary_entities': [r['id'] for r in catalog.entities.values() if r['design'].get('role') in {'主角','男主角','女主角'}],
              'entities': {r['id']: r['name'] for r in catalog.entities.values()},
              'actorless_confirmed': not cleaned['actors'] and bool(answer.get('actorless_confirmed')),
              'discarded_mentions': rejected, **map_source_reading(cleaned, catalog, segments, reading_aliases(directory.parent))}
    result['relations'] = [r for r in catalog.claims if r.get('status') == 'accepted' and r.get('chapter', 0) <= chapter]
    for key in ['mentions','appearances']:
        for row in result[key]:
            row['entity_id'] = canonical_entity(result, row['entity_id'])
    atomic_write_json(directory / 'identity_context.json', result)
    data.saved = result
    return result
