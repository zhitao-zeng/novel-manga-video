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
os.environ.setdefault("SECOND_REVIEW_JUDGE", "local")
import second_review  # noqa: E402,F401
from novel_manga.util import atomic_write_json  # noqa: E402
from plan_chapter_thin import ledger_cast, ledger_snapshot_for  # noqa: E402
from thin_review import ask_json  # noqa: E402

LANE_FIELDS = ("prompt_h3", "prompt_h3_of")
RULES = (
    "你在修一段动画短剧的分镜。判官对照原文发现这段画面把动作或台词安错了人，或漏了人。下面给你：这段原文、现有分镜的各阶段、"
    "原著账本记的这段谁在场、判官的意见。只输出 JSON。对每个阶段（按 origin_index）重写：\n"
    "in_frame：这一阶段画面里真正出现的具名人物（只能从候选名单选；原文里只被提起、在别处、或只有声音的人不进）。\n"
    "actions：这一阶段谁对谁做了什么，actor/target 是 in_frame 里的名字，action 是谓语短语（如“环住脖子吻住”“递过信封”），没有动作就空数组。\n"
    "extras：原文里在场、有动作或台词、但不在候选名单里的无名人物，用不超过 12 字的外貌描述（如“戴眼镜的灰发老妇人”），没有就空数组。\n"
    "event：改写后的事件句，一句话写清谁做什么，先写动作再写其余。\n"
    "有可见说话者的阶段，in_frame 只放说话的人和这一阶段与他有动作往来的人（听的人不进）。判官说缺席的人若原文这段确实在场，必须进 in_frame。"
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
                                                                      "in_frame": {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": names}},
                                                                      "actions": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
                                                                                                                            "required": ["actor", "action", "target"],
                                                                                                                            "properties": {"actor": {"type": "string", "enum": [*names, ""]},
                                                                                                                                           "action": {"type": "string"},
                                                                                                                                           "target": {"type": "string", "enum": [*names, ""]}}}},
                                                                      "extras": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                                                                      "event": {"type": "string"}}}}}}


def stage_view(shot: dict) -> str:
    spoken = [f"{t.get('speaker_name') or '无名'}{'（画外）' if t.get('delivery_mode') == 'offscreen_dialogue' else ''}：{str(t.get('text') or '')[:40]}"
              for t in shot.get("turns") or [] if t.get("delivery_mode") in ("visible_dialogue", "offscreen_dialogue") and t.get("text")]
    return (f"阶段 {shot['origin_index']}：画面「{str(shot.get('visual_prompt') or '')[:120]}」 事件「{str(shot.get('motion_prompt') or '')[:160]}」 "
            f"现有人物名单 {shot.get('characters')}" + (f" 台词：{'；'.join(spoken)[:200]}" if spoken else ""))


def source_passage(segments: dict, segment_ids: list) -> str:
    """Keep adjacent prose so a quote's speaker is not lost at segment borders."""
    ids = list(segments)
    selected = {i for i,key in enumerate(ids) if key in {str(s) for s in segment_ids}}
    window = {j for i in selected for j in range(max(0,i-1),min(len(ids),i+2))}
    return '\n'.join(segments[ids[i]] for i in sorted(window))


def repair_prompt(passage: str, shots: list[dict], snapshot: dict, issue: str, names: list[str], history: str = "", reframe: bool = False, request_context: str = "") -> str:
    cast = "、".join(f"{c['name']}（{c['presence']}）" for c in snapshot.get("chapter_cast", [])) or "（账本没读这一章）"
    named = {seg: v.get("named_here", []) for seg, v in (snapshot.get("segments") or {}).items()}
    strategy = ("\n本次处理多轮后残留，必须重新组织镜头并填写 visual_prompt、camera、shot_scale。"
                "检查现有画面、动作句和英文请求是否互相矛盾；把错误动作主体和错误性别描述从新画面句里移除。"
                "用明确的单主体动作或前后景位置减少多人混淆；需要在场的人仍须有正确人物绑定。"
                "保持原有阶段索引、全部对白和剧情，不删必要角色或事件，不重复先前已失败的改法。"
                "end_state 也必须与新画面、动作一致。speakers 按本阶段 turn_index（从1开始）修正说话者；"
                "台词文字、先后顺序及画内/画外方式保持不变，只改确实安错的说话者。"
                "画面和事件字段只用中文角色名，不写@图片编号或Subject编号，这些由打包器重新分配。"
                "修复建议是线索，以原文和账本为准；不能根据判官猜测改人物身份。" if reframe else "")
    return (RULES + strategy + f"\n\n候选名单：{'、'.join(names)}\n账本记的本章在场情况：{cast}\n这段原文里点到名的人：{named}\n"
            f"判官意见：{issue}\n\n原文（含相邻段落供身份指代核对；只改现有阶段的事件）：\n{passage}\n\n现有分镜：\n" + "\n".join(stage_view(s) for s in shots)
            + ("\n\n" + history + "\n据已发生的结果拟定具体修改；不要把未验证的猜测当事实。" if history else "")
            + ("\n\n当前请求与绑定（核对冲突，不照抄错误）：\n" + request_context if request_context else "")
            + ("\n\n原始轮次（speakers 使用这里的阶段号和 turn_index；不改台词原句）：\n" + json.dumps([
                {'origin_index': s['origin_index'], 'turns': [{'turn_index': i, **t} for i,t in enumerate(s.get('turns', []), 1)]}
                for s in shots], ensure_ascii=False) if reframe else ""))


def apply_stage(shot: dict, fix: dict, names: list[str], *, reframe: bool = False) -> None:
    fix = dict(fix)
    for key in ('event', 'visual_prompt', 'end_state'):
        if fix.get(key):
            fix[key] = re.sub(r'@图片\d+|<(?:Subject|Picture)\s*\d+>', '', str(fix[key])).replace('（）','').replace('()','')
    if reframe:
        for speaker in fix.get('speakers') or []:
            i = int(speaker.get('turn_index', 0)) - 1
            if (0 <= i < len(shot.get('turns', [])) and speaker.get('speaker_name') in names
                    and shot['turns'][i].get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue', 'singing'}):
                shot['turns'][i]['speaker_name'] = speaker['speaker_name']
    in_frame = [n for n in fix.get("in_frame") or [] if n in names]
    actions = [{"actor": a["actor"], "action": str(a.get("action") or "").strip()[:40], "target": a.get("target") or ""}
               for a in fix.get("actions") or [] if a.get("actor") in names and str(a.get("action") or "").strip()]
    for a in actions:
        for who in (a["actor"], a["target"]):
            if who and who in names and who not in in_frame:
                in_frame.append(who)
    # Preserve the repair model's explicit participants. Listener framing below
    # may hide their faces, but must not erase their identity/reference binding.
    shot["in_frame"] = list(in_frame)
    speakers = [t.get("speaker_name") for t in shot.get("turns") or [] if t.get("delivery_mode") == "visible_dialogue" and t.get("speaker_name") in names]
    listeners: list[str] = []
    if len(set(speakers)) == 1 and in_frame:
        speaker = speakers[0]
        acting = {a["actor"] for a in actions} | {a["target"] for a in actions if a["target"]}
        keep = [c for c in in_frame if c == speaker or c in acting]
        if keep:
            listeners = [c for c in in_frame if c not in keep]
            in_frame = keep
    line = "；".join(f"{a['actor']}{a['action']}{a['target']}" for a in actions)
    event = str(fix.get("event") or shot.get("motion_prompt") or "").strip()
    shot["characters"] = in_frame or shot.get("characters") or []
    shot["motion_prompt"] = (f"{line}。{event}" if line and line not in event else event) or shot.get("motion_prompt", "")
    shot["actions"] = actions
    shot["extras"] = [str(e).strip()[:24] for e in fix.get("extras") or [] if str(e).strip()][:3]
    shot["listeners"] = listeners
    if reframe:
        for key in ("visual_prompt", "camera", "shot_scale", "end_state"):
            if fix.get(key):
                shot[key] = str(fix[key]).strip()


REBUILD_LOCK = threading.Lock()  # the packer's per-plan limits and name tables are module state


def source_identities(names: list[str], bible: dict, passage: str) -> list[dict]:
    """Ground scene candidates, including descriptive names shortened in prose.

    These forms are local to the candidate set, never global character aliases.
    A shared role such as 男孩 is not enough to choose between two candidates.
    """
    from plan_chapter_thin import _usable_forms
    forms = {name:set(values) for name,values in _usable_forms(tuple(names)).items()}
    for name in names:
        if '的' in name and name.endswith(('男孩','女孩','老人','男人','女人')):
            forms[name].add(name.rsplit('的',1)[1])
        if name.endswith('身影'):
            forms[name].add(name[:-2]+'人')
        if name.endswith('公爵夫人'):
            forms[name].update({name.replace('公爵夫人','公爵的夫人'),'公爵夫人'})
        if name == '无名群声':
            forms[name].update({'众人','人群','姑娘们','众位女士'})
    owners = {}
    for name, values in forms.items():
        for value in values:
            owners.setdefault(value,set()).add(name)
    characters = [*bible.get('characters',[])]
    if '无名群声' in names:
        characters.append({'name':'无名群声','role':'原文中的群体画外音，不绑定某一人的角色卡'})
    return [{'name':c['name'],'gender':c.get('gender'),'role':c.get('role'),
             'source_names':[v for v in forms[c['name']] if v in passage and owners[v]=={c['name']}]}
            for c in characters if c['name'] in names]


def speaker_contract(passage: str, shots: list[dict], names: list[str], identities: list[dict], fixed: list[dict] = (), evidence_out: list | None = None) -> dict[tuple[int, int], str]:
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
    def supported_adaptation(row, turn):
        relation = row.get('relation')
        if relation not in {'narrated','shared_dialogue'} or row.get('speaker') == turn.get('current_speaker'):
            return True
        phrase = str(row.get('source_speaker_phrase') or '')
        forms = next((r.get('source_names',[]) for r in identities if r['name']==row.get('speaker')),[])
        return (relation=='narrated' and bool(phrase) and phrase in row.get('source_quote','')
                and any(form and form in phrase for form in forms))
    def contradicts_latter(row):
        # Observed in chapter 113: “莱恩将视线投向旧日的神明，后者拍拍手：”.
        # A literal quote alone did not stop the model from assigning it to 莱恩.
        forms=next((r.get('source_names',[]) for r in identities if r['name']==row.get('speaker')),[])
        quote=str(row.get('source_quote') or '')
        for match in re.finditer(r'([^，,。！？；\n：“”]+?)(?:将视线投向|看向|望向|转向)([^，。；：]+)[，,]\s*后者[^。！？：]*[:：]',quote):
            if any(form and form in match[1] and form not in match[2] for form in forms):
                return True
        return False
    fixed_result = {}
    by_key = {(t['stage'],t['turn']):t for t in turns}
    for row in fixed:
        key=(row.get('stage'),row.get('turn'));turn=by_key.get(key);quote=str(row.get('source_quote') or '')
        if (turn and row.get('speaker') in grounded and normalize_text(quote) and not contradicts_latter(row)
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
              '先解析叙述中的指代：“甲看向乙，后者说”是乙说话，不是甲；不能选引用里最先出现的名字。'
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
    answer = ask_json([{'type':'text','text':prompt}], schema, name='speaker_binding', max_tokens=min(4000,600+400*len(turns)))
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
                and not contradicts_latter(row) and row.get('relation') != 'uncertain' and row.get('speaker') in grounded
                and supported_adaptation(row,turn)
                and (adapted_text(turn['text']) in adapted_text(quote)
                     or row.get('relation') in {'condensed','paraphrased','narrated','shared_dialogue'}))
    rows = [quoted(r) for r in answer.get('speakers', [])]
    missing = set(by_key) - {(r.get('stage'),r.get('turn')) for r in rows if valid(r)}
    if missing:
        # Correct the specific rejected evidence once, rather than marking the
        # whole clip permanently blocked because a quote contained an ellipsis.
        retry = ask_json([{'type':'text','text':prompt+'\n只补正以下未通过核验的阶段/轮次：'+json.dumps(sorted(missing))
                          +'。上次回答：'+json.dumps([r for r in rows if (r.get('stage'),r.get('turn')) in missing],ensure_ascii=False)
                          +'。重新逐字复制连续原文，不添加省略号，不拼接远处的句子。若发言跨多个段落，就引用整段。'
                          '若原文确实无法支持改编台词，保留 uncertain，不用猜测。'}],
                         schema,name='speaker_binding_evidence_correction',max_tokens=min(5000,900+650*len(missing)))
        rows = [r for r in rows if (r.get('stage'),r.get('turn')) not in missing] + [quoted(r) for r in retry.get('speakers', [])]
    for row in rows:
        key=(row.get('stage'),row.get('turn'));turn=by_key.get(key);quote=str(row.get('source_quote') or '')
        if turn and normalize_text(quote) in normalize_text(passage) and contradicts_latter(row):
            # One bounded correction of a demonstrated attribution error. Do
            # not keep sampling all accepted turns or let an impossible former
            # speaker through merely because its name appears in the quote.
            retry_schema=copy.deepcopy(schema)
            retry_schema['properties']['speakers']['items']['properties']['speaker']['enum']=[n for n in grounded if n!=row['speaker']]
            if not retry_schema['properties']['speakers']['items']['properties']['speaker']['enum']:
                continue
            retry_schema['properties']['speakers']['minItems']=1
            retry_schema['properties']['speakers']['maxItems']=1
            retry=ask_json([{'type':'text','text':prompt+'\n只纠正这一条：'+json.dumps(row,ensure_ascii=False)
                            +'。这段引用里，“后者”是被看向的人，前面执行“看向”动作的人不可能同时是“后者”。'
                            '从剩余候选中确定被看向的是谁；可扩大引用到前文称谓对应，不能再次选刚才被排除的人。'}],
                           retry_schema,name='speaker_binding_correction',max_tokens=1200)
            fixed_rows=[r for r in retry.get('speakers',[]) if (r.get('stage'),r.get('turn'))==key]
            if fixed_rows:
                row=quoted(fixed_rows[0]);quote=str(row.get('source_quote') or '')
        if valid(row):
            result[key]=row['speaker']
            if evidence_out is not None:
                evidence_out.append({**row,'adapted_text':turn['text']})
    return result


def wrong_gender_description(text: str, name: str, gender: str) -> bool:
    wrong = r'(?:男性|男人|男子|男士)' if gender == '女' else r'(?:女性|女人|女子|女士)' if gender == '男' else None
    if not wrong:
        return False
    return bool(re.search(re.escape(name) + r'(?:是|为|呈现为|被画成|的外形是|的形象是)[^。；\n，]{0,20}' + wrong, text))


def rebuild_clips(episode_dir: Path, bible_path: Path, script: dict, plan: dict, clip_ids: set[str]) -> tuple[dict, list[str]]:
    """Rebuild only the named clips from their recorded shot indexes; every other clip keeps its entry (and request)."""
    import build_clip_plan_thin as bcp
    with REBUILD_LOCK:
        ctx = bcp.context_for_plan(episode_dir, bible_path, plan)
        from h3_request_checks import source_crowds
        bible_data=read(bible_path,{})
        source_segments={str(s.get('segment_id')):s.get('text','') for s in read(episode_dir/'segments.json',[])}
        shots = bcp.prepared_shots(copy.deepcopy(script), episode_dir)
        # Each clip recovers its own stage parts: a rewritten stage that no longer splits the way the plan
        # recorded (雾月 batch 2: "stage 13 has 1 parts, plan requires part 1/2") leaves that clip as it was
        # instead of failing the episode - and, before this, the whole batch.
        merged, changed, skipped = [], [], []
        for before in plan.get("clips") or []:
            if before["clip_id"] not in clip_ids or before.get("kind") != "video":
                merged.append(before)
                continue
            try:
                pieces = bcp.shots_for_plan(plan, shots, {before["clip_id"]}).get(before["clip_id"], [])
            except ValueError as error:
                skipped.append(f"{before['clip_id']}: {str(error)[:80]}")
                pieces = []
            if not pieces:
                merged.append(before)
                continue
            clip = {"kind": "video", "location": before.get("location") or pieces[0]["location"], "shots": pieces,
                    "seconds": round(sum(bcp.shot_seconds(p) for p in pieces), 2)}
            after = bcp.clip_entry(clip, before["clip_id"], ctx)
            crowds=source_crowds(after,bible_data,'\n'.join(source_segments.get(str(s),'') for s in after.get('segment_ids',[])))
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


def repair_episode(novel_dir: Path, index: int, apply: bool, *, use_history: bool = True, reframe: bool = False, identity: bool = False, source_issues: dict | None = None, return_proposal: bool = False) -> dict:
    episode_dir = novel_dir / f"{novel_dir.name}_{index}"
    review = read(episode_dir / "episode_review.json", {})
    failing = source_issues if source_issues is not None else failing_clips(review)
    plan = read(episode_dir / "clip_plan.json", None)
    script = read(episode_dir / "chapter_script.json", None)
    segments = {str(s.get("segment_id")): str(s.get("text") or "") for s in read(episode_dir / "segments.json", [])}
    if identity and plan and script:
        from source_identity_thin import resolve_script
        identities = resolve_script(script, novel_dir, [{'segment_id': k, 'text': v} for k, v in segments.items()])
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
    from plan_chapter_thin import load_entity_index, mentioned_characters
    load_entity_index(novel_dir)
    source_names = mentioned_characters('\n'.join(segments.values()), bible_names)
    names = list(dict.fromkeys([*leads, *source_names, *present, *in_script]))[:40] or bible_names[:12]
    if any(t.get('speaker_name')=='无名群声' and t.get('delivery_mode')=='offscreen_dialogue'
           for shot in script.get('shots',[]) for t in shot.get('turns',[])):
        names = list(dict.fromkeys([*names,'无名群声']))
    # clip_plan addresses the prepared shot's index. origin_index can survive an
    # older split/merge and need not equal that address; retain it in the script.
    by_index = {int(s.get("index", i)): s for i, s in enumerate(script.get("shots", []), 1)}
    seg_rows = [{"segment_id": k, "text": v} for k, v in segments.items()]
    snapshot = ledger_snapshot_for(novel_dir, index, seg_rows, cast_here, names) if cast_here else {}
    repaired, notes, changes = [], [], {}
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
        identity_legend = [{**row,'presence':cast_here.get(row['name'],'not_established')}
                           for row in source_identities(names,bible,passage)]
        by_name = {c['name']: c for c in bible.get('characters', [])}
        for shot in shots:
            corrected_names = set(identities.get(shot['origin_index'], {}).values()) if identity else set()
            corrected_names.update(name for name in shot.get('characters', []) if name in by_name
                                   and wrong_gender_description(str(shot.get('visual_prompt') or ''), name, by_name[name].get('gender','')))
            if corrected_names:
                looks = '；'.join(f"{name}：{by_name[name].get('gender','')}，{by_name[name].get('age','')}，{by_name[name].get('appearance','')}，{by_name[name].get('wardrobe','')}"
                                 for name in corrected_names if name in by_name)
                # Do not keep feeding the old male-doctor picture back to the
                # model after resolving it to the female diviner.
                shot['visual_prompt'] = f"{'、'.join(shot.get('characters',[]))}在{shot.get('location','')}。{shot.get('motion_prompt','')}。已核实外形：{looks}"
        excluded_speakers = set()
        if reframe and not identity and re.search('台词|说话|说出|发言|对白', failing[cid]):
            try:
                contract_path = episode_dir / 'source_speaker_contract.json'
                existing_contracts = read(contract_path, [])
                evidence = []
                binding_passage = '\n'.join(segments.values())
                attributed = speaker_contract(binding_passage, shots, names, source_identities(names,bible,binding_passage), existing_contracts, evidence)
                if apply and evidence:
                    merged = {(r['stage'],r['turn']):r for r in existing_contracts}
                    merged.update({(r['stage'],r['turn']):r for r in evidence})
                    atomic_write_json(contract_path,list(merged.values()))
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
                "speakers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "required": ['turn_index', 'speaker_name'], 'properties': {'turn_index': {'type': 'integer', 'minimum': 1},
                    'speaker_name': {'type': 'string', 'enum': clip_names}}}}})
            schema["properties"]["stages"]["items"]["required"].extend(["visual_prompt", "camera", "shot_scale", "end_state", "speakers"])
        request_context = json.dumps({'candidate_identities': identity_legend, "cast": clip.get("cast"), "references": clip.get("references"),
                                      "prompt": clip.get("prompt", ""), "prompt_h3": clip.get("prompt_h3", ""),
                                      "advice": ((review.get("clips", {}).get(cid) or {}).get("verify") or {}).get("repair_advice")},
                                     ensure_ascii=False)[:7500] if reframe else ""
        try:
            answer = ask_json([{"type": "text", "text": repair_prompt(passage, shots, snapshot, failing[cid], clip_names, history, reframe, request_context)}],
                              schema, name="clip_repair", max_tokens=max(2400, min(4500, 800 * len(indexes))) if reframe else 1500)
        except Exception as error:  # noqa: BLE001
            notes.append(f"{cid}: model {type(error).__name__}: {str(error)[:80]}")
            continue
        fixes = {int(f["origin_index"]): f for f in answer.get("stages") or [] if int(f.get("origin_index", -1)) in by_index}
        if not fixes:
            notes.append(f"{cid}: empty answer")
            continue
        if identity and any(wrong_gender_description(str(f.get('visual_prompt') or ''), name, by_name[name].get('gender',''))
                            for i,f in fixes.items() for name in identities.get(i, {}).values() if name in by_name):
            notes.append(f'{cid}: rewritten picture contradicts resolved character gender')
            continue
        for i, fix in fixes.items():
            if reframe:
                # Picture-writing cannot override the source attribution or an
                # identity correction decided before this call.
                fix['speakers'] = [{'turn_index': j, 'speaker_name': t.get('speaker_name','')}
                                   for j,t in enumerate(by_index[i].get('turns', []),1)]
            apply_stage(by_index[i], fix, names, reframe=reframe)
        changes[cid] = list(fixes.values())
        repaired.append(cid)
    if not repaired:
        return {"episode": index, "clips": 0, "why": "; ".join(notes)[:160]}
    if identity and set(failing) - set(repaired):
        return {'episode': index, 'clips': 0, 'why': 'identity preparation incomplete: ' + '; '.join(notes)[:160]}
    try:
        new_plan, changed = rebuild_clips(episode_dir, novel_dir / "story_bible.json", script, plan, set(repaired))
    except Exception as error:  # noqa: BLE001 - one episode's rebuild must not take the batch down
        return {"episode": index, "clips": 0, "failing": len(failing), "why": f"rebuild failed: {type(error).__name__}: {str(error)[:100]}"}
    if identity and set(failing) - set(changed):
        return {'episode': index, 'clips': 0, 'why': 'identity clips could not all be rebuilt; source files left unchanged'}
    old_notes = read(episode_dir / 'review_feedback.json', {})
    new_notes = {k: v for k, v in old_notes.items() if k not in changed} if reframe else old_notes
    if apply and changed:
        if use_history:
            from repair_history import begin_trial
            begin_trial(episode_dir, set(changed), "identity" if identity else "reframe" if reframe else "rewrite", after_plan=new_plan, after_notes=new_notes, changes=changes)
        for name in ("chapter_script.json", "clip_plan.json"):
            if identity:
                before_identity = episode_dir / f'{name}.bak-source-identities'
                if not before_identity.exists():
                    before_identity.write_bytes((episode_dir / name).read_bytes())
            backup = episode_dir / f"{name}.bak-repair-0914"
            if not backup.is_file():
                backup.write_text((episode_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
        (episode_dir / "chapter_script.json").write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        atomic_write_json(episode_dir / "clip_plan.json", new_plan)
        if new_notes != old_notes:
            atomic_write_json(episode_dir / 'review_feedback.json', new_notes)
    result = {"episode": index, "clips": len(changed), "failing": len(failing), "why": "; ".join(notes)[:120], "changed": changed}
    if return_proposal:
        result['proposal'] = {'script':script,'plan':new_plan,'notes':new_notes,'changes':changes}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", default="", help="e.g. 12,48-60; default: every episode whose latest review has story-class must_fix clips")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    if args.episodes:
        wanted: list[int] = []
        for part in args.episodes.split(","):
            a, _, b = part.strip().partition("-")
            if a:
                wanted.extend(range(int(a), int(b or a) + 1))
    else:
        wanted = [int(p.parent.name.rsplit("_", 1)[-1]) for p in novel_dir.glob(f"{novel_dir.name}_*/episode_review.json")
                  if failing_clips(read(p, {}))]
        wanted.sort()
    started = time.time()
    print(f"{novel_dir.name}: {len(wanted)} 集有剧情类必修段，{args.workers} 路修段{'' if args.apply else '（不写入）'}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        def one(n: int) -> dict:
            try:
                if args.apply:
                    from managed_repair_thin import prepare
                    result=prepare(novel_dir/f'{novel_dir.name}_{n}')
                    return {'episode':n,'clips':len(result['changed']),'changed':result['changed'],
                            'failing':len(set(result['changed'])|set(result['blocked'])),
                            'why':'; '.join(f'{cid}: {why}' for cid,why in result['blocked'].items()),**result}
                return repair_episode(novel_dir, n, args.apply)
            except Exception as error:  # noqa: BLE001 - reported per episode, never the whole batch
                return {"episode": n, "clips": 0, "why": f"{type(error).__name__}: {str(error)[:120]}"}
        results = list(pool.map(one, wanted))
    clips = sum(r.get("clips", 0) for r in results)
    failing = sum(r.get("failing", 0) for r in results)
    for r in results:
        if r.get("clips") or r.get("why"):
            print(f"  {novel_dir.name}_{r['episode']}: 修 {r.get('clips', 0)}/{r.get('failing', '?')} 段 {r.get('why', '')}", flush=True)
    print(f"REPAIR RESULT: {len(wanted)} episodes, {clips} clips repaired of {failing} failing, {(time.time() - started) / 60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
