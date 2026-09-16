"""Single repair model operations; no publication, history or task dispatch."""
from __future__ import annotations

import json
from novel_manga.model_client import ask_json
from novel_manga.review.endpoints import judge_settings
from novel_manga.repair.contracts import schema_for
from novel_manga.repair.execution import repair_prompt

def source_appearance_check(passage: str, shots: list[dict], context: dict) -> dict:
    """Check rewritten pictures only when the reading found local body evidence."""
    from novel_manga.model_client import obj
    from novel_manga.runtime_backends import normalize_text
    active = {n for s in shots for n in [*s.get('characters', []), *s.get('in_frame', [])]}
    relevant = [a for a in context.get('appearances', [])
                if context.get('entities', {}).get(a['entity_id']) in active
                and a.get('source_quote') and normalize_text(a['source_quote']) in normalize_text(passage)]
    if not relevant:
        return {'issues': [], 'checked': False}
    # The extraction only selects the check; its paraphrase is never evidence.
    # Guessed Bible gender and character-card designs do not enter this call.
    fields = ['visual_prompt', 'motion_prompt', 'start_state', 'end_state', 'extras', 'actions']
    candidates = {s['index']: {k: s[k] for k in fields if k in s} for s in shots}
    schema = obj({'issues': {'type': 'array', 'maxItems': 6, 'items': obj({
        'stage': {'type': 'integer', 'enum': list(candidates)},
        'source_quote': {'type': 'string'}, 'candidate_quote': {'type': 'string'},
        'reason': {'type': 'string', 'maxLength': 200}})}})
    answer = ask_json([{'type': 'text', 'text':
        '核对修后画面是否违背原文明示的性别、物种或当时身体形态，只报确定矛盾。'
        '只能依据下列原文，禁止按名字、称谓、常识、人物卡或默认设计猜测。原文没交代就不报。'
        '按事件先后和各阶段source_quote确认当前形态；变化前后的形态可以不同，不能用后来的变身否定较早镜头。'
        '附身者身份与外在身体分开；比喻、辱骂和对白不能当身体事实。服装、配色与画风不在本检查范围。'
        '每项必须逐字摘取原文source_quote和画面字段candidate_quote，并说明二者明确冲突，无法举证就留空。\n'
        + json.dumps({'source': passage, 'name_bindings': [m for m in context.get('mentions', [])
                     if m.get('kind') == 'proper' and context.get('entities', {}).get(m['entity_id']) in active],
                     'entities': {eid: name for eid, name in context.get('entities', {}).items() if name in active},
                     'stages': [{'stage': s['index'], 'source_quote': s.get('source_quote', ''),
                                 'picture': candidates[s['index']]} for s in shots]}, ensure_ascii=False)}],
        schema, name='repair_source_appearance', max_tokens=1400, timeout=120, settings=judge_settings())
    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in strings(v)]
        return []
    for issue in answer['issues']:
        source = normalize_text(issue['source_quote'])
        candidate = normalize_text(issue['candidate_quote'])
        if (not source or source not in normalize_text(passage) or not candidate
                or not any(candidate in normalize_text(s) for s in strings(candidates.get(issue['stage'], {})))):
            raise ValueError('source appearance check returned unsupported evidence')
    return {**answer, 'checked': True}


def speaker_contract(passage: str, shots: list[dict], names: list[str], identities: list[dict], fixed: list[dict] = (), evidence_out: list | None = None, *, identity_context=None) -> dict[tuple[int, int], str]:
    """Resolve disputed attribution from the source before writing the picture."""
    turns = [{'stage': s['origin_index'], 'turn': i, 'text': t['text'], 'current_speaker': t.get('speaker_name')}
             for s in shots for i,t in enumerate(s.get('turns', []), 1)
             if t.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue'} and t.get('text')]
    if not turns:
        return {}
    grounded = [row['name'] for row in identities if row.get('source_names')]
    if not identities:
        grounded = [name for name in names if name in passage]
    if not grounded:
        return {}
    from novel_manga.runtime_backends import normalize_text
    def adapted_text(text):
        # These two homophones occur in the source files (754 / 1722), while
        # the adapted dialogue uses normal spelling. Quotes remain unmodified.
        return normalize_text(text).translate(str.maketrans({'莪':'我','伱':'你'}))
    def phrase_matches_speaker(row):
        phrase = str(row.get('source_speaker_phrase') or '').strip()
        if not phrase:
            return identity_context is None  # historical direct-call records
        matches = [(len(normalize_text(form)), candidate['name']) for candidate in identities
                   for form in [candidate['name'], *candidate.get('source_names', [])]
                   if normalize_text(form) and normalize_text(form) in normalize_text(phrase)]
        if not matches:
            return False
        longest = max(length for length, _ in matches)
        owners = {name for length, name in matches if length == longest}
        return owners == {row.get('speaker')}
    def supported_adaptation(row, turn):
        relation = row.get('relation')
        if relation not in {'narrated','shared_dialogue'} or row.get('speaker') == turn.get('current_speaker'):
            return True
        phrase = str(row.get('source_speaker_phrase') or '')
        forms = next((r.get('source_names',[]) for r in identities if r['name']==row.get('speaker')),[])
        return (relation=='narrated' and bool(phrase) and phrase in row.get('source_quote','')
                and any(form and form in phrase for form in forms))
    fixed_result = {}
    by_key = {(t['stage'],t['turn']):t for t in turns}
    for row in fixed:
        key=(row.get('stage'),row.get('turn'));turn=by_key.get(key);quote=str(row.get('source_quote') or '')
        if (turn and row.get('speaker') in grounded and normalize_text(quote)
                and (identity_context is None or row.get('identity_policy') == identity_context.get('policy'))
                and phrase_matches_speaker(row)
                and normalize_text(quote) in normalize_text(passage)
                and supported_adaptation(row,turn)
                and (adapted_text(turn['text']) in adapted_text(quote)
                     or normalize_text(row.get('adapted_text','')) == normalize_text(turn['text']))):
            fixed_result[key]=row['speaker']
            if evidence_out is not None:
                evidence_out.append(row)
    turns = [t for t in turns if (t['stage'],t['turn']) not in fixed_result]
    if not turns:
        return fixed_result
    paragraphs = [p for p in passage.splitlines() if p.strip()]
    schema = {'type':'object','additionalProperties':False,'required':['speakers'],'properties':{'speakers':{
        'type':'array','items':{'type':'object','additionalProperties':False,
        'required':['stage','turn','source_quote','source_speaker_phrase','relation','speaker'], 'properties':{
            'stage':{'type':'integer'},'turn':{'type':'integer'},
            'source_quote':{'type':'string','maxLength':700},
            'source_paragraphs':{'type':'array','items':{'type':'integer','minimum':1,'maximum':max(1,len(paragraphs))}},
            'source_speaker_phrase':{'type':'string','maxLength':80},
            'relation':{'type':'string','enum':['verbatim','condensed','paraphrased','narrated','shared_dialogue','uncertain']},
            'speaker':{'type':'string','enum':grounded}}}}}}
    prompt = ('只根据原文确定下列台词分别是谁说的。当前说话者可能是错的，不能沿用作证据。'
              '候选资料列出了姓名与原文称谓；先对照称谓，再确定说话者。'
              '剧本台词可以删减、合并原句或换一种说法，不要求它逐字出现在原文中。'
              '先找到与改编台词语义对应的原始发言，再根据原文前后叙述确定说话人。'
              '先根据上下文解析指代，不能选引用里最先出现的名字。'
              '按顺序填写：先摘录 source_quote，再从引用中填写实际说话人的 source_speaker_phrase（原文称谓或指代），'
              '最后才把这个说话人对应到候选资料的 speaker。'
              'source_quote 必须逐字摘录支撑该发言及归属的原文，包含必要的前后叙述；不能把改编台词伪装成原文引用。'
              'relation 标明 verbatim原句、condensed压缩合并、paraphrased改写、uncertain不能对应；不能对应的不要强行指定人物。'
              'narrated 仅用于原文明写的在场行动、想法或事实被改编成简短对白，不能增加原文没有的承诺、身份、事实。'
              'shared_dialogue 用于原文明确多人共同询问/回答，改编由其中一人代表发言。'
              'shared_dialogue 只能保留下方 adaptation_speaker；narrated 若需修正人物，source_speaker_phrase 必须摘录原文明写的事件主体，不能选旁观者。'
              '引用必须支持此人在场并参与该事；这两类不代表原文逐字归属。'
              '不要用 paraphrased 给没有原始发言的旁白强行指定说话人。'
              '引用必须是连续原文，不能自己插入省略号；原文中的省略号原样保留。'
              '优先填写 source_paragraphs：支撑台词及归属的原文段落编号，例如 [2,3]；此时 source_quote 留空，由程序直接摘录原文，避免抄错字或省略。'
              '只能选具有本段原文称谓的候选；无法确定时不输出，不借用旁观者的名字。'
              '不要写分镜或描述画面，只输出JSON。\n候选资料：' + json.dumps(identities,ensure_ascii=False)
              + '\n原文（方括号是段落编号，不是原文内容）：' + '\n'.join(f'[{i}] {p}' for i,p in enumerate(paragraphs,1)) + '\n待核台词：' + json.dumps([
                  {**{k:v for k,v in t.items() if k != 'current_speaker'},
                   'adaptation_speaker':t.get('current_speaker')} for t in turns],ensure_ascii=False))
    answer = ask_json([{'type':'text','text':prompt}], schema, name='speaker_binding', max_tokens=min(4000,600+400*len(turns)), settings=judge_settings())
    by_key = {(t['stage'],t['turn']):t for t in turns}
    result = dict(fixed_result)
    def quoted(row):
        indexes = row.get('source_paragraphs')
        if indexes and all(type(i) is int and 1 <= i <= len(paragraphs) for i in indexes):
            return {**row,'source_quote':'\n'.join(paragraphs[min(indexes)-1:max(indexes)])}
        return row
    def valid(row):
        key=(row.get('stage'),row.get('turn'));turn=by_key.get(key);quote=str(row.get('source_quote') or '')
        return (turn and normalize_text(quote) and normalize_text(quote) in normalize_text(passage)
                and phrase_matches_speaker(row)
                and row.get('relation') != 'uncertain' and row.get('speaker') in grounded
                and supported_adaptation(row,turn)
                and (adapted_text(turn['text']) in adapted_text(quote)
                     or row.get('relation') in {'condensed','paraphrased','narrated','shared_dialogue'}))
    rows = [quoted(r) for r in answer.get('speakers', [])]
    missing = set(by_key) - {(r.get('stage'),r.get('turn')) for r in rows if valid(r)}
    if missing:
        # Split dialogue can end in the next source paragraph. Locate unique
        # verbatim fragments directly instead of repeatedly asking the model to
        # copy a quote that omits the trailing "看来" / "另外" (星海 11/268/278).
        paragraph_ends, source_flat = [], ''
        for paragraph in paragraphs:
            source_flat += adapted_text(paragraph)
            paragraph_ends.append(len(source_flat))
        literal = {}
        for key in missing:
            target = adapted_text(by_key[key]['text'])
            if not target or source_flat.count(target) != 1:
                continue
            start = source_flat.index(target)
            first = next(i for i, end in enumerate(paragraph_ends) if end > start)
            last = next(i for i, end in enumerate(paragraph_ends) if end >= start + len(target))
            literal[key] = '\n'.join(paragraphs[max(0, first-4):min(len(paragraphs), last+2)])
        # Correct the specific rejected evidence once, rather than marking the
        # whole clip permanently blocked because a quote contained an ellipsis.
        retry = ask_json([{'type':'text','text':prompt+'\n只补正以下未通过核验的阶段/轮次：'+json.dumps(sorted(missing))
                          +'。上次回答：'+json.dumps([r for r in rows if (r.get('stage'),r.get('turn')) in missing],ensure_ascii=False)
                          +'。source_speaker_phrase中的具名人物必须与speaker是同一个人；候选里没有原文说话者时保留uncertain，不能换成另一名在场人物。'
                          +'。重新逐字复制连续原文，不添加省略号，不拼接远处的句子。若发言跨多个段落，就引用整段。'
                          '若原文确实无法支持改编台词，保留 uncertain，不用猜测。'
                          +'\n以下台词片段已在原文唯一定位，source_quote必须留空，程序使用给定连续原文。'
                          '仍需依据叙述独立确定speaker和source_speaker_phrase，不能沿用旧归属：'
                          +json.dumps([{'stage':k[0],'turn':k[1],'source_quote':v} for k,v in literal.items()],ensure_ascii=False)}],
                         schema,name='speaker_binding_evidence_correction',max_tokens=min(5000,900+650*len(missing)), settings=judge_settings())
        corrected = [{**r, 'source_quote':literal[(r.get('stage'),r.get('turn'))]}
                     if (r.get('stage'),r.get('turn')) in literal and not str(r.get('source_quote') or '').strip()
                     else quoted(r) for r in retry.get('speakers', [])]
        rows = [r for r in rows if (r.get('stage'),r.get('turn')) not in missing] + corrected
    for row in rows:
        key=(row.get('stage'),row.get('turn'));turn=by_key.get(key);quote=str(row.get('source_quote') or '')
        if valid(row):
            result[key]=row['speaker']
            if evidence_out is not None:
                evidence_out.append({**row,'adapted_text':turn['text'], **({'identity_policy':identity_context['policy'],
                    'entity_id':next((r.get('entity_id') for r in identities if r['name']==row['speaker']),None)} if identity_context else {})})
    return result


def rewrite_clip(passage, shots, snapshot, issue, names, history, reframe, request_context,
                 identity_prompt, indexes, bible, *, require_structure=False):
    schema = schema_for(names, indexes, reframe=reframe, bible=bible)
    return ask_json([{"type": "text", "text": repair_prompt(passage, shots, snapshot, issue, names, history,
                    reframe, request_context, require_structure=require_structure) + identity_prompt}],
                    schema, name="clip_repair", max_tokens=max(2400, min(4500, 800 * len(indexes))) if reframe else 1500,
                    settings=judge_settings())
