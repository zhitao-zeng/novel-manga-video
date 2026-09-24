"""Scene rewrite operations on in-memory candidates, with no publication or scheduling."""
from __future__ import annotations
import copy
import json
import re
from novel_manga.story.actions import normalize_actions, normalize_extras, action_text, action_participants, anchored_event
from novel_manga.story.fields import field_instructions
from .proposal import RepairProposal

RULES = field_instructions('repair')

def stage_view(shot: dict) -> str:
    spoken = [f"{t.get('speaker_name') or '无名'}{'（画外）' if t.get('delivery_mode') == 'offscreen_dialogue' else ''}：{str(t.get('text') or '')[:40]}"
              for t in shot.get("turns") or [] if t.get("delivery_mode") in ("visible_dialogue", "offscreen_dialogue") and t.get("text")]
    return (f"阶段 {shot['origin_index']}：画面「{str(shot.get('visual_prompt') or '')[:120]}」 事件「{str(shot.get('motion_prompt') or '')[:160]}」 "
            f"现有人物名单 {shot.get('characters')}" + (f" 台词：{'；'.join(spoken)[:200]}" if spoken else ""))

def repair_prompt(passage: str, shots: list[dict], snapshot: dict, issue: str, names: list[str], history: str = "", reframe: bool = False, request_context: str = "", *, require_structure: bool = False) -> str:
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
    if require_structure:
        strategy += ('\n这次必须改变可拍的镜头结构，不能只换形容词或重述纠正指令。'
                     '保留原有说话者、对白、动作主体和对象、阶段顺序与时长范围。'
                     '在以下方式中选择适合本段原文的一种：把易混动作改为动作主体的近景/特写；'
                     '把接触过程集中在明确的施事与受事上；把无动作的听者从当前正脸同框中移开；'
                     '给必须表现的关键人物明确的单人反应镜头。'
                     '至少改变一个阶段的出镜人物集合或景别，并用visual_prompt和camera写清新构图；'
                     '不能靠换动作主体或对象、删除必要事件来满足变化。')
    return (RULES + strategy + f"\n\n候选名单：{'、'.join(names)}\n账本记的本章在场情况：{cast}\n这段原文里点到名的人：{named}\n"
            f"判官意见：{issue}\n\n原文（含相邻段落供身份指代核对；只改现有阶段的事件）：\n{passage}\n\n现有分镜：\n" + "\n".join(stage_view(s) for s in shots)
            + ("\n\n" + history + "\n据已发生的结果拟定具体修改；不要把未验证的猜测当事实。" if history else "")
            + ("\n\n当前请求与绑定（核对冲突，不照抄错误）：\n" + request_context if request_context else "")
            + ("\n\n原始轮次（speakers 使用这里的阶段号和 turn_index；不改台词原句）：\n" + json.dumps([
                {'origin_index': s['origin_index'], 'turns': [{'turn_index': i, **t} for i,t in enumerate(s.get('turns', []), 1)]}
                for s in shots], ensure_ascii=False) if reframe else ""))

def apply_stage(shot: dict, fix: dict, names: list[str], *, reframe: bool = False) -> None:
    fix = dict(fix)
    old_action_line = action_text(shot.get('actions', []))
    for key in ('event', 'visual_prompt', 'end_state'):
        if fix.get(key):
            fix[key] = re.sub(r'@图片\d+|<(?:Subject|Picture)\s*\d+>', '', str(fix[key])).replace('（）','').replace('()','')
    if reframe:
        for speaker in fix.get('speakers') or []:
            i = int(speaker.get('turn_index', 0)) - 1
            if (0 <= i < len(shot.get('turns', [])) and speaker.get('speaker_name') in names
                    and shot['turns'][i].get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue', 'singing'}):
                shot['turns'][i]['speaker_name'] = speaker['speaker_name']
    in_frame = [n for n in fix.get('in_frame', shot.get('in_frame', shot.get('characters', []))) or [] if n in names]
    actions = normalize_actions(fix.get('actions'))
    speakers = [t.get('speaker_name') for t in shot.get('turns', [])
                if t.get('delivery_mode') in {'visible_dialogue', 'singing'} and t.get('speaker_name') in names]
    for speaker in speakers:
        if speaker not in in_frame:
            in_frame.append(speaker)
    for a in actions:
        for who in (a["actor"], a["target"]):
            if 'in_frame' not in fix and who and who in names and who not in in_frame:
                in_frame.append(who)
    # Preserve the repair model's explicit participants. Listener framing below
    # may hide their faces, but must not erase their identity/reference binding.
    shot["in_frame"] = list(in_frame)
    listeners: list[str] = []
    if len(set(speakers)) == 1 and in_frame:
        speaker = speakers[0]
        acting = action_participants(actions)
        keep = [c for c in in_frame if c == speaker or c in acting]
        if keep:
            listeners = [c for c in in_frame if c not in keep]
            in_frame = keep
    line = action_text(actions)
    event = str(fix.get("event") or shot.get("motion_prompt") or "").strip()
    if old_action_line and event.startswith(old_action_line + '。'):
        event = event[len(old_action_line) + 1:]
    shot["characters"] = in_frame
    shot["motion_prompt"] = anchored_event(actions, event) or shot.get("motion_prompt", "")
    shot["actions"] = actions
    shot["extras"] = normalize_extras(fix.get("extras"))
    shot["listeners"] = listeners
    if reframe:
        for key in ("visual_prompt", "camera", "shot_scale", "end_state", "location"):
            if fix.get(key):
                shot[key] = str(fix[key]).strip()

def framing_signature(shots: list[dict]) -> list:
    return [(tuple(sorted(set(s.get('in_frame', s.get('characters', []))))), s.get('shot_scale')) for s in shots]

def action_owners(shots: list[dict]) -> list:
    return [sorted((a.get('actor',''), a.get('target','')) for a in s.get('actions', [])) for s in shots]

def retake_proposal(plan, clip_id, diagnosis):
    updated = copy.deepcopy(plan)
    entry = next(c for c in updated['clips'] if c['clip_id'] == clip_id)
    entry['repair_take'] = int(entry.get('repair_take', 0)) + 1
    return RepairProposal({'changed': [clip_id]}, plan=updated, changes={clip_id: diagnosis})
