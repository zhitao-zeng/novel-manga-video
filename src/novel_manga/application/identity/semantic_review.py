"""Model review of source identity candidates; bounded correction, no queue or media calls."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import novel_manga.episodes as episodes
from novel_manga.util import atomic_write_json
from novel_manga.llm import client
from novel_manga.entities import contracts, evidence
from novel_manga.application.identity import ledger_store


def close(candidate, context, schema, *, name, cached=None, settings=None, validate=lambda value: value, record=None,
          merge_correction=lambda current,proposal:proposal):
    """Review once; a proposed correction must pass the same review before adoption."""
    inputs = {'context': context, 'candidate': candidate}
    if cached and cached.get('approved') and cached.get('inputs') == inputs:
        return deepcopy(cached.get('result', candidate)), cached
    trace={'inputs':inputs,'approved':False,'steps':[]}
    def note(kind, **details):
        if record is not None:
            trace['steps'].append({'kind':kind,**deepcopy(details)})
            atomic_write_json(record,trace)
    reply_schema = client.obj({'approved': {'type': 'boolean'}, 'reason': {'type': 'string'},
                              'correction': {'anyOf': [schema, {'type': 'null'}]}})
    current = deepcopy(candidate)
    demand = ''
    for attempt in range(2):
        view={
            'visible_or_voice_subjects':[{k:a.get(k)for k in ('source_id','name','kind','presence')}
                for a in current.get('actors',[])if a.get('presence')in {'on_stage','voice'}],
            'mentioned_only_subjects':[{k:a.get(k)for k in ('source_id','name','kind')}
                for a in current.get('actors',[])if a.get('presence')=='mentioned']}
        prompt = ('按完整小说原文核对本章规划使用的actors和bindings，输出JSON。判断资料是否会让实际演员、数量、种类、名字或在场状态出错。'
            '依据连续事件、对话问答和代词的自然指向识别个体；连续两句“她是甲的女儿，父亲说……”中的父亲可以确定为甲。'
            '“甲的姐夫乙”中甲与乙是两个人，乙与“甲的姐夫”同指；不能把甲本人的名字误当作乙的别名。'
            '当前actors是本章主体；catalogue/record_origins是查找背景资料，不能把历史档案的重复条目误当作当前actors重复。'
            'planning_view明确实际出演和仅提及主体，不能把mentioned_only_subjects误当作入镜演员；物件按其kind呈现，不受旧库person标签影响。'
            '原文已经清楚的同指正常采用，只有确有多种合理解释时才留未知；临时个体可以无名。'
            '原文已支持且不会误导演员的候选填approved=true、correction=null。实际有错才填false，只返回需要改动的actors/bindings条目，source_id保持；未改条目由宿主保留。'
            '删除确实重复或虚构的主体用removed_source_ids；同一人两个叫法应合并forms、保留一个source_id并删除另一个，不强求共享人编号的两个主体。'
            '人物、群体、物件和声源分别登记。此候选没有relations字段，不负责补齐全书亲属关系或归并历史档案；未登记背景关系不构成本章绑定错误。'
            '只被提及且不出场的未知身份允许保留，不能以此否决规划。个体按individual理解，count=0不表示没有这个演员。'
            '引用对应章节/段落原句，已有确证身份保持。'
            + demand + '\n'
            + json.dumps({'evidence': context, 'candidate': current,'planning_view':view}, ensure_ascii=False))
        options = {'settings': settings} if settings is not None else {}
        answer = client.ask_json([{'type': 'text', 'text': prompt}], reply_schema,
                                 name=name, max_tokens=6500, timeout=240, **options)
        note('review',candidate=current,answer=answer)
        if answer.get('approved'):
            if answer.get('correction') is not None:
                raise ValueError('identity reviewer approved a different, unreviewed correction')
            current = validate(current)
            trace.update(approved=True,result=current)
            if record is not None:atomic_write_json(record,trace)
            return current, {'approved': True, 'reason': answer.get('reason', ''), 'inputs': inputs, 'result': current}
        proposal = answer.get('correction')
        if proposal is None and not attempt:
            # A complaint without a correction object cannot be applied; demand it once before failing.
            demand = ('你刚才指出了问题但 correction 为空。这次必须给出 correction:'
                      '只含需要改动的 actors/bindings 条目和 removed_source_ids;'
                      '若问题实际无法落笔修订,说明哪条证据不足,不要把同样的理由再写一遍。')
            note('demand_correction', reason=answer.get('reason'))
            continue
        if attempt or proposal is None:
            raise ValueError('identity semantic review unresolved: ' + str(answer.get('reason', 'no source-backed correction')))
        proposed=current
        try:
            proposed=merge_correction(current,proposal)
            current = validate(proposed)
        except ValueError as error:
            note('validation_error',candidate=proposal,error=str(error))
            # Correct the concrete rejected proposal once, before final review.
            # A failed protocol correction never renews the semantic review loop.
            repair_prompt=('修正下面身份修订的具体校验错误，只输出改动的actors/bindings及removed_source_ids。引用仅能使用原文逐字存在的'
                '名字或称谓；不得制造新主体、改原文或扩大修订范围。保留其他正确字段。\n'
                + json.dumps({'evidence':context,'previous_candidate':proposed,'rejected_proposal':proposal,
                              'validation_error':str(error)},ensure_ascii=False))
            repaired=client.ask_json([{'type':'text','text':repair_prompt}],schema,
                name=name+'_correction',max_tokens=6500,timeout=240,**options)
            note('protocol_correction',candidate=repaired)
            current=validate(merge_correction(proposed,repaired))
    raise AssertionError('bounded review did not return')


def catalogue_rows(records):
    return [{'id': e['id'], 'name': e['canonical'], 'kind': e.get('kind'), 'named': e.get('named'),
             'merged_into': e.get('merged_into'), 'origin': e.get('source'), 'quote': e.get('evidence', '')} for e in records]


def merge_binding_changes(current,proposal):
    """Apply a bounded model delta; unchanged evidence is never rewritten."""
    result=deepcopy(current)
    removed=set(proposal.get('removed_source_ids')or[])
    known={a['source_id']for a in result['actors']}
    if removed-known:raise ValueError('identity correction removes an unknown source actor')
    for field in ('actors','bindings'):
        updates=proposal.get(field)or[]
        ids=[row['source_id']for row in updates]
        if len(set(ids))!=len(ids):raise ValueError('identity correction duplicated a source actor id')
        if set(ids)&removed:raise ValueError('identity correction both updates and removes a source actor')
        by_id={row['source_id']:row for row in result[field]if row['source_id']not in removed}
        by_id.update({row['source_id']:deepcopy(row)for row in updates})
        result[field]=list(by_id.values())
    return result


def context_sources(novel, chapter, text, records, refs, texts=None):
    if texts is None:
        meta = ledger_store.read_json(Path(novel) / 'novel.json', {})
        texts = ledger_store.novel_texts(novel) if meta.get('source') else {}
    selected = {chapter: text}
    for ch in (() if chapter is None else (chapter - 1, chapter + 1)):
        if ch in texts: selected[ch] = texts[ch]
    for record in records:
        if record['id'] not in refs: continue
        source = str(record.get('source') or '')
        if source.startswith('ch') and source[2:].isdigit():
            ch = int(source[2:])
            if ch in texts: selected[ch] = texts[ch]
    return {str(ch): content for ch, content in sorted(selected.items(), key=lambda item: str(item[0]))}


def book(ledger, chapter, text, raw, *, texts=None, original_records=None):
    from novel_manga.review.endpoints import judge_settings
    candidate = {field: deepcopy(raw.get(field) or []) for field in ('mentions', 'new_entities', 'claims', 'relations')}
    refs = {str(row.get(field) or '') for key, fields in [('mentions', ['entity']), ('claims', ['subject','object'])]
            for row in candidate[key] for field in fields}
    records = original_records if original_records is not None else ledger.entities
    context = {'chapter': chapter, 'source': context_sources(ledger.novel_dir, chapter, text, records, refs, texts),
               'records': catalogue_rows(records), 'confirmed_decisions': ledger.corrections,
               'resolved': {'mentions': ledger.mentions_by_chapter.get(chapter, []),
                            'relations': ledger.relations_by_chapter.get(chapter, []),
                            'claims': [c for c in ledger.claims if c['chapter'] == chapter],
                            'merges': ledger.merges, 'records': catalogue_rows(ledger.entities)}}
    schema = client.obj({'approved': {'type': 'boolean'}, 'reason': {'type': 'string'},
                         'correction': {'anyOf': [contracts.EXTRACT_SCHEMA, {'type': 'null'}]}})
    refs.update(m.get('entity') for m in ledger.mentions_by_chapter.get(chapter, []))
    cache_context = {**context,
        'records': [{k:v for k,v in row.items() if k!='merged_into'} for row in context['records'] if row['id'] in refs],
        'confirmed_decisions': [row for row in ledger.corrections if {row.get('subject'), row.get('object')} & refs],
        'resolved': {**context['resolved'], 'records': [row for row in context['resolved']['records'] if row['id'] in refs],
                     'merges': [row for row in ledger.merges if {row['from'],row['into']} & refs]}}
    inputs = {'context': cache_context, 'candidate': candidate}
    cached = raw.get('semantic_review')
    if cached and cached.get('approved') and cached.get('inputs') == inputs:
        return raw, cached
    prompt = ('按完整原文核对本章最终解析结果，目标是演员与身份归属正确。原始档案是待核资料，resolved是解析后的候选。'
              '依据连续事件、对话和自然代词指向判断同一人；例如“她是甲的女儿，父亲说……”已能确定父亲是甲。'
              '原文的自然同指是有效证据，只有确有多种解释时才保留未知。正确结果填approved=true、correction=null。'
              '实际出现错人、误并、物件当人或错误关系时填false，用原结构修订这几个问题并保留其他正确字段。'
              '这是修订已有候选：原来为空的relations/claims保持为空，原来没有的人物不为补全原文而新增；'
              '仅在漏掉实际参与事件的主体就是本次问题时才补该主体。没有漏记关系不算错误。'
              '临时人物可以不具名；称呼、自报名字写mentions，身份关系有具体两端。引用对应章节原句，原人工作证保持。\n'
              + json.dumps({'context':context,'candidate':candidate}, ensure_ascii=False))
    answer = client.ask_json([{'type':'text','text':prompt}], schema, name='ledger_identity_review',
                             max_tokens=6500, timeout=240, settings=judge_settings())
    if answer.get('approved'):
        if answer.get('correction') is not None: raise ValueError('approved unreviewed identity correction')
        return raw, {'approved': True, 'reason': answer.get('reason',''), 'inputs': inputs}
    correction = answer.get('correction')
    if correction is None: raise ValueError('identity semantic review unresolved: '+str(answer.get('reason','')))
    for field in candidate:
        if not isinstance(correction.get(field), list): raise ValueError('identity correction lacks '+field)
    _, _, _, dropped = evidence.ground(chapter, text, correction)
    if any(row.get('why') == '证据不在本章' for row in dropped): raise ValueError('identity correction fabricated a chapter quotation')
    for row in correction['mentions']:
        if str(row.get('form') or '') not in text or not evidence.locate(row.get('evidence',''), text):
            raise ValueError('identity correction has an ungrounded mention')
    return {**raw, **correction}, {'approved': False, 'reason': answer.get('reason','')}


def bindings(directory, answer, catalog, segments, mapped, *, cached=None, aliases=None):
    from novel_manga.story.source_identity import source_schema, clean_reading, map_source_reading
    ids = {a['source_id'] for a in answer['actors']}
    old = {m['source_actor']: m['entity_id'] for m in mapped['mentions']}
    candidate = {'actors': answer['actors'], 'bindings': [
        {'source_id': sid, 'entity_id': old.get(sid, 'UNKNOWN'),
         'entity_kind': None,
         'decision': 'catalogue' if old.get(sid, 'UNKNOWN') != 'UNKNOWN' else 'unknown',
         'paragraphs': next(a['paragraphs'] for a in answer['actors'] if a['source_id']==sid), 'reason': ''}
        for sid in sorted(ids)]}
    target_schema = client.obj({'source_id': {'type':'integer'},
        'entity_id': {'type':'string','enum': [*catalog.entities, 'UNKNOWN']},
        'entity_kind': {'anyOf':[{'type':'string','enum':['person','animal','spirit','other']},{'type':'null'}]},
        'decision': {'type':'string','enum':['catalogue','extra','named_missing','unknown']},
        'paragraphs': {'type':'array','minItems':1,'items':{'type':'integer','minimum':1,'maximum':len(segments)}},
        'reason': {'type':'string'}})
    schema = client.obj({'actors': source_schema(len(segments))['properties']['actors'],
                         'bindings': {'type':'array','items':target_schema},
                         'removed_source_ids':{'type':'array','items':{'type':'integer','enum':sorted(ids)}}})
    refs = {eid for eid in old.values() if eid != 'UNKNOWN'}
    chapter = episodes.chapter_of(Path(directory).name)
    text = '\n'.join(s['text'] for s in segments)
    sources=context_sources(Path(directory).parent,chapter,text,catalog.records,refs)
    corpus='\n'.join(sources.values())
    forms={a['name']for a in answer['actors']}|{f['form']for a in answer['actors']for f in a['forms']}
    lookup_ids=refs|{eid for eid,r in catalog.entities.items()if r['name']in corpus or r['forms']&forms}
    related=[r for r in catalog.records if r['id']in lookup_ids or r.get('merged_into')in lookup_ids]
    context = {'chapter':chapter, 'source':sources,
               'catalogue': [{'id':r['id'],'name':r['name'],'forms':sorted(r['forms'])} for r in catalog.entities.values()],
               'record_origins':catalogue_rows(related),
               'binding_rules': '按实际下游用途核对bindings。catalogue=已有具体主体，entity_id填已有编号；extra=本章临时个体/群体/物件，不需真名或永久卡；named_missing=本章原文明示的新人物名但库里尚无；unknown=归属仍不能确定。extra、named_missing、unknown的entity_id均填UNKNOWN。仅被提及而不出场的主体允许unknown，不因此阻塞拍摄；在场或发声的无名个体应为extra，不能因为无真名写unknown。同一具体人物的叫法放在一个actor的forms；独立身体各自绑定。forms必须逐字来自本章，不用人物库规范名替换原文称谓，不能因相邻章出现姓名就给本章无名群体编造姓名。record_origins是历史资料，不是本章结果；下游以actors.kind/count决定当前群体/物件的表现，不能因旧库kind=person断言本章会把树或器物画成人。bindings.entity_kind仅用于named_missing的新建档分类，其他决定填null；本章群体/物件类型已由actors.kind确定，不改旧库；person/animal/spirit是个体类别，与actors.kind=individual是两层分类。群体、物件保持原文kind/count，数量可超过十万；不为提及的国家/军队建立演员卡。'}
    def validate(value):
        cleaned, rejected, unresolved = clean_reading({'actors':value['actors']},segments)
        actors = cleaned['actors']
        # clean_reading already drops invented alternate names and repairs citations.
        # Review the resulting grounded actor, rather than rejecting its valid identity
        # because an extra form was unsupported. Actors with no evidence still block.
        if unresolved:
            raise ValueError('semantic binding correction has ungrounded source forms: '
                             + ', '.join(str(a['name']) for a in unresolved))
        if not actors and answer['actors']: raise ValueError('semantic correction removed all source actors')
        actor_ids = {a['source_id'] for a in actors}
        if len(actor_ids)!=len(actors): raise ValueError('semantic correction duplicated a source actor id')
        if {r['source_id'] for r in value['bindings']}!=actor_ids or len(value['bindings'])!=len(actors):
            raise ValueError('semantic correction lacks one binding per source actor')
        for row in value['bindings']:
            if row['entity_id'] not in set(catalog.entities)|{'UNKNOWN'}:raise ValueError('semantic binding invents a catalogue id')
            if (row['decision']=='catalogue') != (row['entity_id']!='UNKNOWN'):raise ValueError('semantic binding decision disagrees with target')
            if not row['paragraphs'] or any(p<1 or p>len(segments) for p in row['paragraphs']):raise ValueError('semantic binding cites missing paragraphs')
            actor = next(a for a in actors if a['source_id']==row['source_id'])
            if row['decision']=='named_missing' and not any(f['kind']=='proper' and f['form']==actor['name'] for f in actor['forms']):
                raise ValueError('new named actor lacks a proper source name')
            if row['decision']=='named_missing' and row.get('entity_kind') not in {'person','animal','spirit','other'}:
                raise ValueError('new named actor lacks a source entity kind')
        checked = {**value, 'actors': actors}
        targets = {r['source_id']: r['entity_id'] for r in value['bindings']}
        resolved = map_source_reading(cleaned, catalog, segments, aliases, approved_bindings=targets)
        changed={m['source_actor']:m['entity_id']for m in resolved['mentions']
                 if m['entity_id']!=targets[m['source_actor']]}
        if changed:
            conflicts=[{'source_id':sid,'name':next(a['name']for a in actors if a['source_id']==sid),
                        'requested_entity':targets[sid],'resolved_entity':eid}for sid,eid in changed.items()]
            raise ValueError('semantic binding collapses distinct source actors; correction must preserve their bodies: '
                +json.dumps(conflicts,ensure_ascii=False)+'. Keep individual bodies distinct; an anonymous group can use decision=extra rather than sharing a person id.')
        return checked
    value, review = close(candidate,context,schema,name='chapter_binding_review',cached=cached,validate=validate,
                          merge_correction=merge_binding_changes,
                          record=Path(directory)/'identity_binding_review.json')
    return value, review


def register_names(novel, context, decisions):
    """Only reviewed, explicitly named source actors reach existing bible growth."""
    names = context.get('semantic_new_names') or []
    if not context.get('semantic_review',{}).get('approved') or not names: return
    import fcntl
    path = Path(novel) / 'reading_cast.json'
    with ledger_store.ledger_lock(novel):
        with (Path(novel)/'reading_cast.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            ledger = ledger_store.Ledger(novel)
            for actor in context.get('source_actors', []):
                if actor['name'] not in names or actor['name'] in ledger.by_name: continue
                row = next(r for r in decisions if r['source_id']==actor['source_id'])
                quote = next(m['source_quote'] for m in context['mentions'] if m['source_actor']==actor['source_id'])
                ledger._add_entity(actor['name'],row['entity_kind'],True,actor.get('appearance',''),context['chapter'],quote)
            reading = ledger_store.read_json(path,{})
            reading['characters'] = sorted(set(reading.get('characters') or [])|set(names))
            ledger_store.write_json(path,reading)
            ledger.save()


def committed_current(ledger, chapter, text):
    if not ledger.has_chapter(chapter): return False
    review = ledger_store.read_json(ledger.raw_path(chapter), {}).get('semantic_review') or {}
    context = review.get('inputs', {}).get('context', {})
    if not review.get('approved') or context.get('source', {}).get(str(chapter)) != text:
        return False
    resolved = context.get('resolved', {})
    ids = {row['id'] for row in resolved.get('records', [])}
    if catalogue_rows([row for row in ledger.entities if row['id'] in ids]) != resolved.get('records', []):
        return False
    if [row for row in ledger.corrections if {row.get('subject'), row.get('object')} & ids] != context.get('confirmed_decisions', []):
        return False
    return ([row for row in ledger.merges if {row['from'], row['into']} & ids] == resolved.get('merges', [])
            and ledger.mentions_by_chapter.get(chapter, []) == resolved.get('mentions', [])
            and ledger.relations_by_chapter.get(chapter, []) == resolved.get('relations', [])
            and [row for row in ledger.claims if row['chapter'] == chapter] == resolved.get('claims', []))
