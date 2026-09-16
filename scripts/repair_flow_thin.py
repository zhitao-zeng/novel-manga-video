#!/usr/bin/env python
"""Repair only the clips the story judge failed, keeping every other clip's request (and cached render) intact.

    repair_clips_thin.py --novel-dir outputs/X [--episodes 12,48-60 | --targets] [--workers 4] [--apply]

Re-planning a chapter under the storyboard contract rewrites every stage, so every clip re-renders; the
pilot showed that (1,041 clips, 768 "request changed", the rest new cuts).  A failed clip does not need the
chapter re-planned: its own stages need the contract - who is in frame, who does what to whom, which unnamed
extra is there - written with the judge's complaint, the passage and the ledger's casting snapshot in view.
One small model call per failed clip does that; the storyboard shots of that clip are updated in place
(cuts unchanged), the clip is rebuilt from its recorded shot indexes, and only its request changes.
"""
from __future__ import annotations
from novel_manga.review.endpoints import judge_settings

import argparse
import copy
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
from novel_manga.story.fields import cast_field, actions_field, extras_field, field_instructions
from novel_manga.repair.proposal import RepairProposal
from novel_manga.repair.execution import action_owners, apply_stage, framing_signature, repair_prompt, stage_view
from novel_manga.story.actions import normalize_actions, normalize_extras, action_text, action_participants
from novel_manga.util import atomic_write_json  # noqa: E402
from novel_manga.planning.context import PlannerContext
from planner_context_thin import ledger_cast, ledger_snapshot_for  # noqa: E402
from novel_manga.model_client import ask_json  # noqa: E402

LANE_FIELDS = ("prompt_h3", "prompt_h3_of")
RULES = (
    field_instructions("repair")
)


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def failing_clips(review: dict) -> dict[str, str]:
    return {k: str(v.get("story_issue") or v.get("feedback") or "") for k, v in (review.get("clips") or {}).items()
            if (v.get("tier") or v.get("fix_tier")) == "must_fix" and v.get("story_ok") is False}


def schema_for(names: list[str], indexes: list[int]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["stages"], "properties": {"stages": {
        "type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                                                                  "required": ["origin_index", "in_frame", "actions", "extras", "event"],
                                                                  "properties": {
                                                                      "origin_index": {"type": "integer", "enum": indexes},
                                                                      "in_frame": cast_field(names),
                                                                      "actions": actions_field(),
                                                                      "extras": extras_field(),
                                                                      "event": {"type": "string"}}}}}}




def source_passage(segments: dict, segment_ids: list) -> str:
    """Keep adjacent prose so a quote's speaker is not lost at segment borders."""
    ids = list(segments)
    selected = {i for i,key in enumerate(ids) if key in {str(s) for s in segment_ids}}
    window = {j for i in selected for j in range(max(0,i-1),min(len(ids),i+2))}
    return '\n'.join(segments[ids[i]] for i in sorted(window))






REBUILD_LOCK = threading.Lock()  # the packer's per-plan limits and name tables are module state






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


def source_identities(names: list[str], bible: dict, passage: str, *, context=None, catalog=None) -> list[dict]:
    from story_identity import identity_rows
    return identity_rows(names, bible, passage, context=context, catalog=catalog)


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


def rebuild_clips(episode_dir: Path, bible_path: Path, script: dict, plan: dict, clip_ids: set[str], *, repack_report: dict | None = None) -> tuple[dict, list[str]]:
    """Rebuild only the named clips from their recorded shot indexes; every other clip keeps its entry (and request)."""
    import build_clip_plan_thin as bcp
    with REBUILD_LOCK:
        ctx = bcp.context_for_plan(episode_dir, bible_path, plan)
        from h3_request_checks import source_crowds
        bible_data=read(bible_path,{})
        source_segments={str(s.get('segment_id')):s.get('text','') for s in read(episode_dir/'segments.json',[])}
        shots = bcp.prepared_shots(copy.deepcopy(script), episode_dir)
        from clip_readiness import location_issues
        by_index = {s['index']: s for s in shots}
        recut = {c['clip_id'] for c in plan.get('clips', []) if c['clip_id'] in clip_ids
                 and c.get('kind') == 'video' and location_issues(c, by_index)}
        recut_ids = set()
        if recut:
            from repair_blocked_plan import repack
            plan, report = repack(episode_dir, plan, script, targets=recut)
            recut_ids = {cid for group in report['groups'] for cid in [*group['old'], *group['new']]}
            if repack_report is not None:
                repack_report.update(report)
        # Each clip recovers its own stage parts: a rewritten stage that no longer splits the way the plan
        # recorded (雾月 batch 2: "stage 13 has 1 parts, plan requires part 1/2") leaves that clip as it was
        # instead of failing the episode - and, before this, the whole batch.
        merged, changed, skipped = [], sorted(recut_ids), []
        for before in plan.get("clips") or []:
            if before["clip_id"] in recut_ids or before["clip_id"] not in clip_ids or before.get("kind") != "video":
                merged.append(before)
                continue
            try:
                pieces = bcp.shots_for_plan(plan, shots, {before["clip_id"]}, settings=ctx.get("compiler_options")).get(before["clip_id"], [])
            except ValueError as error:
                skipped.append(f"{before['clip_id']}: {str(error)[:80]}")
                pieces = []
            if not pieces:
                merged.append(before)
                continue
            clip = {"kind": "video", "location": pieces[0]["location"], "shots": pieces,
                    "seconds": round(sum(bcp.shot_seconds(p, settings=ctx.get("compiler_options")) for p in pieces), 2)}
            after = bcp.clip_entry(clip, before["clip_id"], ctx)
            from story_identity import current_context
            crowds=source_crowds(after,bible_data,'\n'.join(source_segments.get(str(s),'') for s in after.get('segment_ids',[])), context=current_context(episode_dir))
            if crowds:
                after['crowd_roles']=crowds
            # An unchanged request keeps its existing English rendering. A no-op
            # repair must not create a fresh take just by translating it again.
            if all(after.get(k) == before.get(k) for k in ("prompt", "references", "request_seconds", "lines", "chat_lines",'crowd_roles')):
                # Update recovered provenance without losing any runtime/cache
                # mode (notably prompt_h3_skip) from the existing request.
                kept = {**before, **{key: after[key] for key in ("shot_indexes", "shot_parts") if key in after}}
                merged.append(kept)
                if kept != before:
                    changed.append(before["clip_id"])
                continue
            merged.append({k: v for k, v in after.items() if k not in LANE_FIELDS})
            changed.append(before["clip_id"])
        if skipped:
            print("  left as is (stage parts no longer match the plan): " + "; ".join(skipped), flush=True)
        return {**plan, "clips": merged}, changed


def _proposal_data(novel_dir: Path, index: int, *, record_evidence=None, use_history: bool = True, reframe: bool = False, identity: bool = False, source_issues: dict | None = None, require_structure: bool = False) -> dict:
    planner_ctx = PlannerContext.from_env()
    episode_dir = novel_dir / f"{novel_dir.name}_{index}"
    review = read(episode_dir / "episode_review.json", {})
    failing = source_issues if source_issues is not None else failing_clips(review)
    plan = read(episode_dir / "clip_plan.json", None)
    script = read(episode_dir / "chapter_script.json", None)
    segments = {str(s.get("segment_id")): str(s.get("text") or "") for s in read(episode_dir / "segments.json", [])}
    if identity and plan and script:
        from source_identity_thin import resolve_script
        identities = resolve_script(script, novel_dir, [{'segment_id': k, 'text': v} for k, v in segments.items()], chapter=index)
        failing = {c['clip_id']: '原文身份消歧：' + json.dumps({i: identities[i] for i in set(c.get('shot_indexes', [])) if i in identities}, ensure_ascii=False)
                   + '。这是同一个人，统一使用正确姓名、角色卡和说话者；删掉旧错误身份的外貌、职业与服装描述，保留原文动作和全部对白。'
                   for c in plan.get('clips', []) if any(
                       set(mapping) & (set(c.get('cast', [])) | {t.get('speaker_name') for t in c.get('lines', [])})
                       for i,mapping in identities.items() if i in c.get('shot_indexes', []))}
        reframe = True
    if not failing or not plan or not script:
        return {"episode": index, "clips": 0, "why": "nothing to repair" if not failing else "no plan/script"}
    bible = read(novel_dir / "story_bible.json", {})
    bible_names = [c["name"] for c in bible.get("characters", []) if c.get("name")]
    cast_here = ledger_cast(novel_dir, index)
    present = [n for n in bible_names if cast_here.get(n) in ("on_stage", "voice")]
    in_script = [n for s in script.get("shots", []) for n in s.get("characters", [])]
    if identity:
        resolved_old = {old for mapping in identities.values() for old in mapping} - set(in_script)
        present = [name for name in present if name not in resolved_old]
    leads = [c["name"] for c in bible.get("characters", []) if "主角" in str(c.get("role", ""))]
    from planner_context_thin import load_entity_index
    from novel_manga.planning.cast import mentioned_characters
    from story_identity import IdentityCatalog, resolve_chapter, prompt_block
    identity_reading = resolve_chapter(episode_dir)
    from dialogue_binding import apply_confirmed_speakers
    protected_bindings = apply_confirmed_speakers(episode_dir, script['shots'])
    catalog = IdentityCatalog(novel_dir)
    load_entity_index(novel_dir, index, ctx=planner_ctx)
    source_names = mentioned_characters('\n'.join(segments.values()), bible_names, ctx=planner_ctx)
    resolved_names = [identity_reading['entities'].get(m['entity_id']) for m in identity_reading['mentions']
                      if m.get('presence') in {'on_stage','voice'} and m['entity_id'] != 'UNKNOWN']
    names = list(dict.fromkeys([*(n for n in resolved_names if n in bible_names), *leads, *source_names, *present, *in_script]))[:40]
    if identity_reading.get('actorless_confirmed'):
        names = []
    from story_identity import typed_entities
    entity_types = typed_entities(novel_dir, identity_reading)
    names = [n for n in names if entity_types.get(n, {}).get('kind') != 'object']
    if any(t.get('speaker_name')=='无名群声' and t.get('delivery_mode')=='offscreen_dialogue'
           for shot in script.get('shots',[]) for t in shot.get('turns',[])):
        names = list(dict.fromkeys([*names,'无名群声']))
    # clip_plan addresses the prepared shot's index. origin_index can survive an
    # older split/merge and need not equal that address; retain it in the script.
    by_index = {int(s.get("index", i)): s for i, s in enumerate(script.get("shots", []), 1)}
    seg_rows = [{"segment_id": k, "text": v} for k, v in segments.items()]
    snapshot = ledger_snapshot_for(novel_dir, index, seg_rows, cast_here, names) if cast_here else {}
    repaired, notes, changes, appearance_checks = [], [], {}, {}
    for clip in plan.get("clips") or []:
        cid = clip.get("clip_id")
        if cid not in failing:
            continue
        indexes = [i for i in dict.fromkeys(clip.get("shot_indexes") or []) if i in by_index]
        if not indexes:
            notes.append(f"{cid}: no shot indexes")
            continue
        passage = source_passage(segments, clip.get("segment_ids") or [])
        shots = [{**by_index[i], "origin_index": i} for i in indexes]
        original_stages = copy.deepcopy([by_index[i] for i in indexes])
        identity_legend = [{**row,'presence':cast_here.get(row['name'],'not_established')}
                           for row in source_identities(names,bible,passage,context=identity_reading,catalog=catalog)]
        by_name = {c['name']: c for c in bible.get('characters', [])}
        for shot in shots:
            corrected_names = set(identities.get(shot['origin_index'], {}).values()) if identity else set()
            if corrected_names:
                looks = '；'.join(row['description'] for row in identity_reading.get('appearances', [])
                                 if identity_reading['entities'].get(row['entity_id']) in corrected_names)
                # An identity correction discards the previous identity's look.
                # Only source-backed body facts are called verified appearance.
                shot['visual_prompt'] = f"{'、'.join(shot.get('characters',[]))}在{shot.get('location','')}。{shot.get('motion_prompt','')}。原文形态：{looks or '按本章原文及当前资产确定，不继承旧错误身份的外观'}"
        excluded_speakers = set()
        if reframe and not identity and re.search('台词|说话|说出|发言|对白', failing[cid]):
            try:
                contract_path = episode_dir / 'source_speaker_contract.json'
                existing_contracts = read(contract_path, [])
                existing_contracts = list({**{(r['stage'],r['turn']):r for r in existing_contracts}, **protected_bindings}.values())
                evidence = []
                binding_passage = '\n'.join(segments.values())
                attributed = speaker_contract(binding_passage, shots, names,
                    source_identities(names,bible,binding_passage,context=identity_reading,catalog=catalog),
                    existing_contracts, evidence, identity_context=identity_reading)
                if record_evidence and evidence:
                    merged = {(r['stage'],r['turn']):r for r in existing_contracts}
                    merged.update({(r['stage'],r['turn']):r for r in evidence})
                    record_evidence(contract_path,list(merged.values()))
            except Exception as error:
                notes.append(f'{cid}: speaker attribution failed: {type(error).__name__}')
                continue
            if not attributed and any(t.get('delivery_mode') in {'visible_dialogue','offscreen_dialogue'} for s in shots for t in s.get('turns', [])):
                notes.append(f'{cid}: source speaker could not be established')
                continue
            for (stage,turn), name in attributed.items():
                old_name = by_index[stage]['turns'][turn-1].get('speaker_name')
                grounded = {r['name'] for r in identity_legend if r['source_names']}
                if old_name != name and old_name not in grounded and cast_here.get(old_name) != 'on_stage':
                    excluded_speakers.add(old_name)
                by_index[stage]['turns'][turn-1]['speaker_name']=name
        history = ""
        if use_history:
            from repair_history import history_context
            history = history_context(episode_dir, cid)
        clip_names = [n for n in names if n not in excluded_speakers]
        schema = schema_for(clip_names, indexes)
        if reframe:
            schema["properties"]["stages"]["items"]["properties"].update({
                "visual_prompt": {"type": "string", "maxLength": 200}, "camera": {"type": "string", "maxLength": 60},
                "shot_scale": {"type": "string", "enum": ["远景", "全景", "中景", "近景", "特写"]},
                "end_state": {"type": "string", "maxLength": 200},
                "location": {"type": "string", "enum": list(dict.fromkeys(
                    str(loc).split('：', 1)[0] for loc in bible.get('locations', [])))},
                "speakers": {"type": "array", **({"maxItems": 0} if not clip_names else {}),
                    "items": {"type": "object", "additionalProperties": False,
                    "required": ['turn_index', 'speaker_name'], 'properties': {'turn_index': {'type': 'integer', 'minimum': 1},
                    'speaker_name': {'type': 'string', **({'enum': clip_names} if clip_names else {})}}}}})
            if not bible.get('locations'):
                schema['properties']['stages']['items']['properties'].pop('location')
            schema["properties"]["stages"]["items"]["required"].extend(["visual_prompt", "camera", "shot_scale", "end_state", "speakers"])
        request_context = json.dumps({'candidate_identities': identity_legend, "cast": clip.get("cast"), "references": clip.get("references"),
                                      "prompt": clip.get("prompt", ""), "prompt_h3": clip.get("prompt_h3", ""),
                                      "advice": ((review.get("clips", {}).get(cid) or {}).get("verify") or {}).get("repair_advice")},
                                     ensure_ascii=False)[:7500] if reframe else ""
        try:
            answer = ask_json([{"type": "text", "text": repair_prompt(passage, shots, snapshot, failing[cid], clip_names, history, reframe, request_context, require_structure=require_structure)
                              + prompt_block(episode_dir, clip_names)}],
                              schema, name="clip_repair", max_tokens=max(2400, min(4500, 800 * len(indexes))) if reframe else 1500, settings=judge_settings())
        except Exception as error:  # noqa: BLE001
            notes.append(f"{cid}: model {type(error).__name__}: {str(error)[:80]}")
            continue
        fixes = {int(f["origin_index"]): f for f in answer.get("stages") or [] if int(f.get("origin_index", -1)) in indexes}
        if not fixes:
            notes.append(f"{cid}: empty answer")
            continue
        for i, fix in fixes.items():
            if reframe:
                # Picture-writing cannot override the source attribution or an
                # identity correction decided before this call.
                fix['speakers'] = [{'turn_index': j, 'speaker_name': t.get('speaker_name','')}
                                   for j,t in enumerate(by_index[i].get('turns', []),1)]
            apply_stage(by_index[i], fix, names, reframe=reframe)
        if require_structure:
            revised = [by_index[i] for i in indexes]
            problem = ('reframe changed action owners instead of camera structure' if any(
                           old and old != new for old,new in zip(action_owners(original_stages), action_owners(revised)))
                       else 'reframe only changed wording; cast and shot scales are unchanged'
                       if framing_signature(original_stages) == framing_signature(revised) else '')
            if problem:
                for i, original in zip(indexes, original_stages):
                    by_index[i].clear(); by_index[i].update(original)
                notes.append(f'{cid}: {problem}')
                continue
        revised = [by_index[i] for i in indexes]
        if revised != original_stages:
            try:
                # Legacy scripts use list position as their stage address;
                # origin_index can refer to a different, pre-split numbering.
                check = source_appearance_check(passage, [{**by_index[i], 'index': i} for i in indexes], identity_reading)
                appearance_checks[cid] = check
                if check['issues']:
                    raise ValueError('source appearance conflict: ' + '; '.join(i['reason'] for i in check['issues']))
            except Exception as error:
                for i, original in zip(indexes, original_stages):
                    by_index[i].clear(); by_index[i].update(original)
                notes.append(f'{cid}: {str(error)[:200]}')
                continue
        changes[cid] = list(fixes.values())
        repaired.append(cid)
    if not repaired:
        return {"episode": index, "clips": 0, "why": "; ".join(notes)[:250], 'appearance_checks': appearance_checks}
    if identity and set(failing) - set(repaired):
        return {'episode': index, 'clips': 0, 'why': 'identity preparation incomplete: ' + '; '.join(notes)[:160]}
    try:
        structural = {}
        new_plan, changed = rebuild_clips(episode_dir, novel_dir / "story_bible.json", script, plan, set(repaired), repack_report=structural)
    except Exception as error:  # noqa: BLE001 - one episode's rebuild must not take the batch down
        return {"episode": index, "clips": 0, "failing": len(failing), "why": f"rebuild failed: {type(error).__name__}: {str(error)[:100]}"}
    if identity and set(failing) - set(changed):
        return {'episode': index, 'clips': 0, 'why': 'identity clips could not all be rebuilt; source files left unchanged'}
    old_notes = read(episode_dir / 'review_feedback.json', {})
    new_notes = {k: v for k, v in old_notes.items() if k not in changed} if reframe or structural else old_notes
    result = {"episode": index, "clips": len(changed), "failing": len(failing), "why": "; ".join(notes)[:250], "changed": changed,
              'appearance_checks': appearance_checks}
    result['proposal'] = {'script':script,'plan':new_plan,'notes':new_notes,'changes':changes,
                              'structural_repair': structural}
    return result





def propose_episode(novel_dir: Path, index: int, **kwargs) -> RepairProposal:
    return RepairProposal.from_result(_proposal_data(novel_dir, index, **kwargs))


def repair_episode(novel_dir: Path, index: int, apply: bool, *, use_history=True, reframe=False,
                   identity=False, source_issues=None, return_proposal=False, require_structure=False) -> dict:
    from repair_publication_thin import record_speaker_evidence
    candidate = propose_episode(novel_dir, index, record_evidence=record_speaker_evidence if apply else None,
                                use_history=use_history, reframe=reframe,
                                identity=identity, source_issues=source_issues, require_structure=require_structure)
    if apply and candidate.changed:
        from repair_publication_thin import publish_rewrite
        publish_rewrite(novel_dir / f'{novel_dir.name}_{index}', candidate,
                        use_history=use_history, identity=identity, reframe=reframe)
    return candidate.as_result(return_proposal)
