"""Model review of source identity candidates; bounded correction, no queue or media calls."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import novel_manga.episodes as episodes
from novel_manga.util import atomic_write_json
from novel_manga.llm import client
from novel_manga.llm.transport import IncompleteResponse
from novel_manga.entities import contracts, evidence
from novel_manga.application.identity import ledger_store


def candidate_view(candidate, *, diagnostics=False):
    """Keep corrected citations; old wrong addresses belong to audit history, not the judge."""
    candidate = deepcopy(candidate)
    if isinstance(candidate, dict):
        for actor in candidate.get('actors', []):
            if actor.get('kind') == 'individual' and type(actor.get('count')) is int and actor['count'] == 0:
                actor['count'] = 1
            if not diagnostics:
                for form in actor.get('forms') or []:
                    form.pop('cited', None)
    return candidate


def same_candidate(left, right):
    """Explanations and audit-only citation history do not change the current actors."""
    def value(candidate):
        candidate = candidate_view(candidate)
        if isinstance(candidate, dict) and 'bindings' in candidate:
            candidate.pop('removed_source_ids', None)
            for row in candidate['bindings']:
                row.pop('reason', None)
        return candidate
    return value(left) == value(right)


def review_context(context):
    """Unrelated name-index rows have no forms or evidence for this chapter."""
    if 'catalogue' not in context:
        return context
    return {**context, 'catalogue': [row for row in context['catalogue'] if row.get('forms')]}


def close(candidate, context, schema, *, name, cached=None, settings=None, validate=lambda value: value, record=None,
          merge_correction=lambda current,proposal:proposal, pending=None):
    """Review once; an optional host correction must pass review before adoption.

    An Agent already owns its revision: merge_correction=None returns a rejection
    to that Agent rather than interleaving a second writer with its candidate.
    """
    context = review_context(context)
    inputs = {'context': context, 'candidate': candidate}
    for saved in (cached, pending):
        if (saved and saved.get('approved') is True
                and review_context(saved.get('inputs', {}).get('context', {})) == context
                and any(same_candidate(candidate, previous) for previous in
                        (saved['inputs'].get('candidate'), saved.get('result')))):
            result = validate(deepcopy(saved.get('result', candidate)))
            return result, {'approved': True, **({'reason': saved['reason']} if 'reason' in saved else {}),
                            'inputs': inputs, 'result': result}
    trace={'inputs':inputs,'approved':False,'steps':[]}
    archived = False
    def note(kind, **details):
        nonlocal archived
        if record is not None:
            if not archived and Path(record).is_file():
                with Path(record).with_suffix('.history.jsonl').open('a') as history:
                    history.write(json.dumps(json.loads(Path(record).read_text()), ensure_ascii=False) + '\n')
            archived = True
            trace['steps'].append({'kind':kind,**deepcopy(details)})
            atomic_write_json(record,trace)
    reply_schema = client.obj({'reason': {'type': 'string'}, 'approved': {'type': 'boolean'},
                              'correction': {'anyOf': [schema, {'type': 'null'}]}})
    current = deepcopy(candidate)
    if (pending and pending.get('approved') is False and merge_correction is not None
            and review_context(pending.get('inputs', {}).get('context', {})) == context):
        last = next((s for s in reversed(pending.get('steps') or []) if s.get('kind') == 'review'), None)
        latest = validate(deepcopy(last['candidate'])) if last else None
        if (last and last.get('reviewed_view', candidate_view(last['candidate'], diagnostics=True))
                != candidate_view(latest)):
            # The earlier judge saw an obsolete diagnostic as if it were a fact.
            # Retain the checked candidate, not that judgment of different inputs.
            current = latest
            note('resume_candidate', candidate=current)
            pending = None
    if (pending and not pending.get('approved')
            and review_context(pending.get('inputs', {}).get('context', {})) == context
            and same_candidate(pending.get('inputs', {}).get('candidate'), candidate)):
        reviews = [step for step in pending.get('steps', []) if step.get('kind') == 'review']
        if not reviews:
            resumed = [step['candidate'] for step in pending.get('steps', [])
                       if step.get('kind') == 'resume_candidate']
            if resumed:
                current = validate(deepcopy(resumed[-1]))
                note('resume_candidate', candidate=current)
        if reviews:
            latest = reviews[-1]
            last_answer = latest.get('answer')
            last_answer = last_answer if isinstance(last_answer, dict) else {}
            proposal = last_answer.get('correction')
            if (merge_correction is not None and isinstance(proposal, dict)
                    and last_answer.get('approved') is False):
                try:
                    previous = latest['candidate']
                    resumed = validate(merge_correction(deepcopy(previous), proposal))
                except (KeyError, TypeError, ValueError):
                    pass  # A rejected or stale proposal cannot supply a new candidate.
                else:
                    if not same_candidate(resumed, previous):
                        current = resumed
                        note('resume_candidate', candidate=current)
            if (last_answer.get('approved') is False
                    and isinstance(last_answer.get('reason'), str)
                    and 'correction' in last_answer
                    and same_candidate(current, candidate)):
                # No changed, valid repair: a second batch pass must not buy
                # a different judgment of the same evidence and candidate.
                raise ValueError('identity semantic review unresolved: '
                                 + str(last_answer.get('reason', 'no source-backed correction')))
    demand = ''
    demanded = False
    attempt = 0
    while attempt < 2:
        review_candidate = candidate_view(current)
        view={
            'visible_or_voice_subjects':[{k:a.get(k)for k in ('source_id','name','kind','presence')}
                for a in current.get('actors',[])if a.get('presence')in {'on_stage','voice'}],
            'mentioned_only_subjects':[{k:a.get(k)for k in ('source_id','name','kind')}
                for a in current.get('actors',[])if a.get('presence')=='mentioned']}
        prompt = ('对照current_segments核对主体身份绑定。检查人物或说话人错绑、不同独立身体被误合并、物种错误及当前事件物件错认，不把资料完整性当作剧本准入。'
            '先写简短reason形成判断，再填写approved与correction，三者必须一致。没有上述实质错误时approved=true、correction=null；字段说明reason可为空，原文称呼与档案正名不同也不是错误。不要为了填写correction原样重抄候选。'
            'planning_view区分当前故事时空中的主体与仅提及。catalogue和record_origins供查已指称主体的身份，不是本章出演名单；不要求补齐全书背景关系，不因仅提及主体的unknown/extra差别阻塞。已有明确身份保留，临时人物允许无名。'
            'on_stage表示确实在当前故事场景，含旁观者或休息者，不要求主动参与；不等于每镜入画。paragraphs包含指称与参与范围，同一人先被提及、后来出场仍保留一个actor并标on_stage，不因覆盖提及段落而拆人。'
            'source_id是本章主体整数编号，entity_id是全书档案编号，两者由bindings连接，不要求编号相同。actors.name保留本章原文主要叫法；个体按individual理解，count=0不表示没有这个演员，群体/物件的0也表示数量未写明。'
            '群体指称允许包含已单列成员，不要求拆出所有匿名成员或去重成不相交的演员表；不能按重叠条目相加推定多拍或少拍了人。实际镜头人数由后续分镜的具名人物与extras核对，此处没有镜头，不能据此预判拍错。画外可归属的呻吟等声音允许voice，不要求把它编成台词。'
            '依据连续事件、对话和代词自然指向判断同一人；父子等关系是不同人，分身等独立行动的身体也须分开。确实同一人两个叫法应合并forms、保留一个source_id并用removed_source_ids删除重复条目。'
            '核对物件各次指称的事件和时间：不能仅因同名同类就把回忆、转述中的物件归入当前物件。同一性有明确原文依据才合并，否则保留独立主体及各自的指称、段落和presence；泛指类名无需新增主体。'
            '确有上述实质错误才approved=false，只返回须修改的actors/bindings及removed_source_ids，未改条目由宿主保留；修订引用本章原文，不制造新称呼或无据主体。出演、台词和事件以current_segments为据，不把档案中的事件或人物转成本章要求；不能把章节号或自然行号当成本章paragraphs，只引用current_segments的编号。'
            'actors和bindings的paragraphs是主体指称与参与范围，forms.paragraphs是称呼逐字出现的位置，三者不必逐项相同。不要求为群体补出匿名成员，或把连续同行者逐段登记；只有原文支持的身份或发声归属错误才修这些范围。'
            + demand + '\n'
            + json.dumps({'evidence': context, 'candidate': review_candidate,'planning_view':view}, ensure_ascii=False))
        options = {'settings': settings} if settings is not None else {}
        answer = client.ask_json([{'type': 'text', 'text': prompt}], reply_schema,
                                 name=name, max_tokens=16000, timeout=240, json_object=True, **options)
        note('review',candidate=current,reviewed_view=review_candidate,answer=answer)
        if (not isinstance(answer, dict) or type(answer.get('approved')) is not bool or 'correction' not in answer
                or not isinstance(answer.get('reason'), str)):
            raise IncompleteResponse('identity review has no complete typed judgment')
        if answer.get('approved'):
            if answer.get('correction') is not None:
                raise IncompleteResponse('identity reviewer approved a different, unreviewed correction')
            current = validate(current)
            trace.update(approved=True,result=current)
            if record is not None:atomic_write_json(record,trace)
            return current, {'approved': True, 'reason': answer.get('reason', ''), 'inputs': inputs, 'result': current}
        if merge_correction is None:
            raise ValueError('identity semantic review unresolved: ' + answer['reason'])
        proposal = answer.get('correction')
        if proposal is None and not demanded:
            # A complaint without a correction object cannot be applied; demand it once before failing.
            # The demand consumes no review attempt: the bounded merge/final-review budget is unchanged.
            demand = ('你刚才指出了问题但 correction 为空。这次必须给出 correction:'
                      '只含需要改动的 actors/bindings 条目和 removed_source_ids;'
                      '若问题实际无法落笔修订,说明哪条证据不足,不要把同样的理由再写一遍。')
            demanded = True
            note('demand_correction', reason=answer.get('reason'))
            continue
        if attempt or proposal is None:
            raise ValueError('identity semantic review unresolved: ' + str(answer.get('reason', 'no source-backed correction')))
        rejected_candidate=deepcopy(current)
        proposed=current
        try:
            proposed=merge_correction(current,proposal)
            proposed = validate(proposed)
            current = proposed
        except ValueError as error:
            note('validation_error',candidate=proposal,error=str(error))
            # Correct the concrete rejected proposal once, before final review.
            # A failed protocol correction never renews the semantic review loop.
            repair_prompt=('修正下面身份修订的具体校验错误，只输出改动的actors/bindings及removed_source_ids。引用仅能使用原文逐字存在的'
                '名字或称谓；不得制造新主体、改原文或扩大修订范围。保留其他正确字段。'
                'previous_candidate尚未采用被拒提议；本次输出完整修订提议来替换rejected_proposal，保留其中有依据的改动。'
                '新增主体若没有本章原文指称，应从提议中省去；不得删除或修改无关原主体。\n'
                + json.dumps({'evidence':context,'previous_candidate':candidate_view(current),'rejected_proposal':candidate_view(proposal),
                              'validation_error':str(error)},ensure_ascii=False))
            affected={row.get('source_id') for field in ('actors','bindings')
                      for row in proposal.get(field) or []} | set(proposal.get('removed_source_ids') or [])
            allowed=sorted(sid for sid in affected if type(sid) is int)
            repair_schema=deepcopy(schema)
            for field in ('actors','bindings'):
                rows=repair_schema.get('properties',{}).get(field,{})
                if allowed and 'source_id' in rows.get('items',{}).get('properties',{}):
                    rows['maxItems']=len(allowed)
                    rows['items']['properties']['source_id']={'type':'integer','enum':allowed}
            removed=repair_schema.get('properties',{}).get('removed_source_ids')
            if allowed and removed is not None:
                removed['items']={'type':'integer','enum':allowed}
            repaired=client.ask_json([{'type':'text','text':repair_prompt}],repair_schema,
                name=name+'_correction',max_tokens=16000,timeout=240,**options)
            note('protocol_correction',candidate=repaired)
            outside=set(repaired.get('removed_source_ids') or []) - affected
            for field in ('actors','bindings'):
                original={row.get('source_id'):row for row in current.get(field) or []}
                for row in repaired.get(field) or []:
                    previous=original.get(row.get('source_id'), {})
                    if (row.get('source_id') not in affected
                            and {k:v for k,v in row.items() if k!='reason'}
                            != {k:v for k,v in previous.items() if k!='reason'}):
                        outside.add(row.get('source_id'))
            if outside:
                raise ValueError('protocol correction changes unrelated source actors: '+str(sorted(outside)))
            current=validate(merge_correction(current,repaired))
        if same_candidate(current, rejected_candidate):
            raise ValueError('identity semantic review unresolved: '
                             + str(answer.get('reason', '')) + '; correction has no changed grounded candidate')
        demand = ''
        attempt += 1
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
    context = {'chapter': chapter, 'source': {str(chapter): text},
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
    # This gate validates the current fragment. Identity background remains in
    # the catalogue/confirmed decisions; adjacent plot is not a coverage target.
    review_context = {k: v for k, v in context.items() if k != 'source'}
    review_context['current_source'] = text
    prompt = ('核对current_source当前制作分段的最终解析结果，目标是演员与身份归属正确。原始档案是待核资料，resolved是解析后的候选。'
              '以resolved实际采用的编号、归属与关系为审查对象，candidate是供修订的原始输入；未采用的提议不等于最终结果，不能把不同编号的主体误报为已合并。'
              'kind=person表示人类主体，可能是个体或群体，不表示人数等于1；按原文和description区分群体及其中成员。'
              'current_source是本次唯一需要覆盖的制作分段；records和confirmed_decisions只供识别称呼与身份，不是本段出演名单。pending关系尚未被确证，不能当作已采纳事实来否决正确的演员归属。'
              '同人归并含义：' + contracts.CLAIM_MEANINGS['same_as'] + '\n'
              '依据连续事件、对话和自然代词指向判断同一人；例如“她是甲的女儿，父亲说……”已能确定父亲是甲。'
              '原文的自然同指是有效证据，只有确有多种解释时才保留未知。正确结果填approved=true、correction=null。'
              '实际出现错人、误并、物件当人或错误关系时填false，用原结构修订这几个问题并保留其他正确字段。'
              '这是修订已有候选：原来为空的relations/claims保持为空，原来没有的人物不为补全原文而新增；'
              '仅在漏掉实际参与事件的主体就是本次问题时才补该主体。没有漏记关系不算错误。'
              '临时人物可以不具名；称呼、自报名字写mentions，身份关系有具体两端。修订中的evidence必须是本章逐字连续原句，不能用省略号或分号拼接，原人工作证保持。\n'
              + json.dumps({'context':review_context,'candidate':candidate}, ensure_ascii=False))
    answer = client.ask_json([{'type':'text','text':prompt}], schema, name='ledger_identity_review',
                             max_tokens=16000, timeout=240, settings=judge_settings())
    if (not isinstance(answer, dict) or type(answer.get('approved')) is not bool
            or 'correction' not in answer or not isinstance(answer.get('reason'), str)):
        raise IncompleteResponse('identity review has no complete typed judgment')
    if answer.get('approved'):
        if answer.get('correction') is not None:
            raise IncompleteResponse('approved unreviewed identity correction')
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


def binding_context(directory, answer, catalog, segments, mapped):
    """The same source-backed catalogue slice for review and planning resumption."""
    old = {m['source_actor']: m['entity_id'] for m in mapped['mentions']}
    refs = {eid for eid in old.values() if eid != 'UNKNOWN'}
    chapter = episodes.chapter_of(Path(directory).name)
    text = '\n'.join(s['text'] for s in segments)
    forms={a['name']for a in answer['actors']}|{f['form']for a in answer['actors']for f in a['forms']}
    # Current source supplies the cast. Registered origins still explain known
    # identities; whole neighboring scenes cannot create current performers.
    lookup_ids=refs|{eid for eid,r in catalog.entities.items()if r['name']in text or r['forms']&forms}
    related=[r for r in catalog.records if r['id']in lookup_ids or r.get('merged_into')in lookup_ids]
    context = {'chapter':chapter,
               'current_segments':[{'paragraph':i, **row} for i,row in enumerate(segments,1)],
               'catalogue': [{'id':r['id'],'name':r['name'],
                              'forms':sorted(r['forms']) if r['id'] in lookup_ids else []}
                             for r in catalog.entities.values() if r['id'] in lookup_ids],
               'record_origins':catalogue_rows(related),
               'binding_rules': '核对当前原文的主体身份绑定，不在无分镜时预判拍摄人数。on_stage含当前场景的旁观者或休息者；群体允许与单列成员重叠，不要求穷举匿名成员或逐段登记同行者。catalogue=已有具体主体，entity_id填已有编号；extra=本章临时个体/群体/物件，不需真名或永久卡；named_missing=本章原文明示的新人物名但库里尚无；unknown=归属仍不能确定。extra、named_missing、unknown的entity_id均填UNKNOWN。仅被提及而不出场的主体允许unknown，不因此阻塞拍摄；在场或发声的无名个体应为extra，不能因为无真名写unknown。同一具体人物的叫法放在一个actor的forms；独立身体各自绑定。forms必须逐字来自本章，不用人物库规范名替换原文称谓，不能因相邻章出现姓名就给本章无名群体编造姓名。record_origins是历史资料，不是本章结果；下游以actors.kind/count理解本章指称与物件表现，群体总数可包含已单列成员，不按重叠条目相加推定镜头人数；可归属的画外发声允许voice，不强制补台词。不能因旧库kind=person断言本章会把树或器物画成人。bindings.entity_kind仅用于named_missing的新建档分类，其他决定填null；本章群体/物件类型已由actors.kind确定，不改旧库；person/animal/spirit是个体类别，与actors.kind=individual是两层分类。群体、物件保持原文kind/count，数量可超过十万；不为提及的国家/军队建立演员卡。'}
    return context


def bindings(directory, answer, catalog, segments, mapped, *, cached=None, aliases=None, pending=None, proposal=None):
    from novel_manga.story.source_identity import source_schema, clean_reading, map_source_reading
    ids = {a['source_id'] for a in answer['actors']}
    old = {m['source_actor']: m['entity_id'] for m in mapped['mentions']}
    temporary = {a['source_id'] for a in answer['actors']
                 if a['presence'] in {'on_stage', 'voice'} and
                 (a['kind'] in {'group', 'object'} or
                  (a['kind'] == 'individual' and not any(f['kind'] == 'proper' for f in a['forms'])))}
    candidate = {'actors': answer['actors'], 'bindings': [
        {'source_id': sid, 'entity_id': old.get(sid, 'UNKNOWN'),
         'entity_kind': None,
         'decision': 'catalogue' if old.get(sid, 'UNKNOWN') != 'UNKNOWN' else 'extra' if sid in temporary else 'unknown',
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
    context = binding_context(directory, answer, catalog, segments, mapped)
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
        by_id = {a['source_id']: a for a in actors}
        checked = {**value, 'actors': actors, 'bindings': [
            {**row, 'paragraphs': sorted(set(row['paragraphs']) | {
                p for form in by_id[row['source_id']]['forms'] for p in form['paragraphs']})}
            for row in value['bindings']]}
        targets = {r['source_id']: r['entity_id'] for r in value['bindings']}
        resolved = map_source_reading(cleaned, catalog, segments, aliases, approved_bindings=targets)
        changed={m['source_actor']:m['entity_id']for m in resolved['mentions']
                 if m['entity_id']!=targets[m['source_actor']]}
        if changed:
            conflicts=[{'source_id':sid,'name':next(a['name']for a in actors if a['source_id']==sid),
                        'requested_entity':targets[sid],'resolved_entity':eid}for sid,eid in changed.items()]
            raise ValueError('semantic binding collapses distinct source actors; correction must preserve their bodies: '
                +json.dumps(conflicts,ensure_ascii=False)+'. Keep distinct bodies separate. If source evidence shows two actor entries are one person, merge their forms/paragraphs into one actor and delete the duplicate with removed_source_ids. An anonymous group can use decision=extra rather than sharing a person id.')
        return checked
    if proposal is not None:
        original = proposal.get('inputs') or {}
        if review_context(original.get('context') or {}) != review_context(context):
            raise ValueError('planning identity proposal is stale for the current source or catalogue')
        if not same_candidate(original.get('candidate'), candidate):
            # Feedback continues the persisted rejected revision, rather than
            # forcing every correction back onto the initial source candidate.
            latest = next((s for s in reversed((pending or {}).get('steps') or [])
                           if s.get('kind') == 'review'), {})
            if (not pending or pending.get('approved') is not False
                    or review_context(pending.get('inputs', {}).get('context') or {}) != review_context(context)
                    or not (same_candidate(original.get('candidate'), pending.get('inputs', {}).get('candidate'))
                            or same_candidate(original.get('candidate'), latest.get('candidate')))):
                raise ValueError('planning identity proposal is stale for the current source or catalogue')
            candidate = validate(deepcopy(original['candidate']))
        proposed = validate(merge_binding_changes(candidate, proposal['correction']))
        if same_candidate(proposed, candidate):
            raise ValueError('planning identity proposal has no changed grounded candidate')
        candidate = proposed
    value, review = close(candidate,context,schema,name='chapter_binding_review',cached=cached,validate=validate,
                          merge_correction=None if proposal is not None else merge_binding_changes,
                          pending=pending,
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
