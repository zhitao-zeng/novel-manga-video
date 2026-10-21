"""Review adaptation meaning once; literal differences are not content failures."""
from __future__ import annotations

from copy import deepcopy
import json

from novel_manga.llm.client import ask_json, obj
from novel_manga.review.endpoints import judge_settings
from novel_manga.planning.issues import PlanningCode, PlanningIssue
from novel_manga.planning.text import quote_key
from novel_manga.story.dialogue import merged_turns

POLICY = 'adaptation-meaning-v2'
FIELDS = ('speech', 'picture', 'characters', 'location', 'omissions')
SCHEMA = obj({'verdict': {'type':'string','enum':['pass','fail','incomplete']},
    'errors': {'type':'array','items':obj({
        'shot': {'type':'integer','minimum':0},
        'field': {'type':'string','enum':list(FIELDS)},
        'source_quote': {'type':'string'}, 'draft_quote': {'type':'string'},
        'viewer_error': {'type':'string'}})},
    'note': {'type':'string'}})


def section_of(outline):
    sections = outline.get('sections') or {}
    return str(sections.get('retained_dialogue') or sections.get('causal_chain') or
               sections.get('expression_plan') or '')


def speech_view(shot):
    rows=[]
    for t in merged_turns(deepcopy({**shot,'turns':shot.get('turns') or []})):
        mode=t.get('delivery_mode')
        voice=('角色心声，场内其他人听不见' if t.get('inner_monologue') and mode=='offscreen_dialogue'
               else '画外人物发言' if mode=='offscreen_dialogue' else '画内人物发言' if mode=='visible_dialogue'
               else {'silent_action':'无声动作','chat_message':'屏幕消息','singing':'哼唱','title_card':'字幕卡'}.get(mode,mode))
        rows.append({'speaker_name':t.get('speaker_name'),'text':t.get('text'),'delivery':voice})
    return rows


def material(section, shots, source, *, skipped=()):
    return {'source':source,'adaptation_intent':section,'omissions':list(skipped),
            'shots':[{'shot':i,'label':s.get('label'),'origin_index':s.get('origin_index',i),
                      'segment_id':s.get('segment_id'),'scene':s.get('scene_id'),
                      'location':s.get('location'),'characters':s.get('characters'),
                      'picture':{k:s.get(k) for k in ('visual_prompt','motion_prompt','end_state','camera','in_frame')},
                      'speech':speech_view(s)} for i,s in enumerate(shots,1)]}


def interpret(verdict, section):
    if verdict.get('section') != section:
        raise ValueError('剧情判断不属于当前提纲')
    if verdict.get('policy') != POLICY:
        # Historical reports remain readable; they cannot be reused by live review.
        rows=verdict.get('verdicts')
        if not isinstance(rows,list):raise ValueError('旧剧情复核缺少结论')
        issues=[]
        for row in rows:
            if row.get('status') not in {'delivered','missing','changed','unclear'}:
                raise ValueError('旧剧情复核返回未知状态')
            if row['status']!='delivered':
                issues.append(PlanningIssue(PlanningCode.RETAINED_LINE_LOST,
                    f"历史改编差异（未证明影响理解）：{row.get('reason','')}",field='turns'))
        return issues
    outcome,rows=verdict.get('verdict'),verdict.get('errors')
    if outcome not in {'pass','fail','incomplete'} or not isinstance(rows,list):
        raise ValueError('剧情复核没有有效结论')
    if (outcome=='pass' and rows) or (outcome=='fail' and not rows):
        raise ValueError('剧情复核结论与错误证据冲突')
    inputs=verdict.get('inputs') or {};source=quote_key(inputs.get('source',''))
    shots={s['shot']:s for s in inputs.get('shots',[])};issues=[];invalid=[]
    for row in rows:
        index,field=row.get('shot'),row.get('field')
        original,actual=row.get('source_quote'),row.get('draft_quote')
        target=inputs.get('omissions') if index==0 and field=='omissions' else shots.get(index,{}).get(field)
        if (type(index)is not int or field not in FIELDS or target is None
                or not isinstance(original,str) or not quote_key(original) or quote_key(original)not in source
                or not isinstance(actual,str) or not quote_key(actual)
                or quote_key(actual)not in quote_key(json.dumps(target,ensure_ascii=False))
                or not isinstance(row.get('viewer_error'),str) or not row['viewer_error'].strip()):
            invalid.append(f'镜{index}/{field}的证据未对应实际材料')
            continue
        issues.append(PlanningIssue(PlanningCode.SOURCE_CONTRADICTION,
            f"{row['viewer_error']}；原文「{original}」，稿件「{actual}」",
            stage=shots.get(index,{}).get('label'),field='turns' if field=='speech' else field))
    if invalid:
        if not issues:raise ValueError('剧情复核没有对应原文与实际镜头的错误证据')
        issues.append(PlanningIssue(PlanningCode.REVIEW_INCOMPLETE,'；'.join(invalid)))
    if outcome=='incomplete':
        issues.append(PlanningIssue(PlanningCode.REVIEW_INCOMPLETE,verdict.get('note') or '没有形成有效判断'))
    return issues


def review(section, shots, source, *, previous=None, skipped=()):
    inputs=material(section,shots,source,skipped=skipped)
    if previous and previous.get('policy')==POLICY and previous.get('inputs')==inputs:
        issues=interpret(previous,section)
        if previous.get('verdict') in {'pass','fail'} or any(i.code==PlanningCode.SOURCE_CONTRADICTION for i in issues):
            return previous,issues
    prompt=(
        '核对小说改编稿是否让观众误解重要事实。原文是事实依据，提纲只是可修改的取舍意图，不是逐句交付清单。'
        '允许删作者说明、重复解释和非必要细节，允许换词、合句、改用动作或有依据的角色心声。'
        '省略与原文不同不等于错误；只有省略使当前行动无法理解或造成错误理解才需要返修。'
        '按观众看到、听到的完整场面判断：人物身份/行为归属是否错换，拒绝与同意等重要意思是否反转，'
        '关键因果是否断裂或秘密错误地变成其他人物已知。人物的谎言、猜测、回忆不是作者宣布的事实。'
        'speech.delivery已经按实际执行字段解释，心声只有观众听见；画外发言不要求入镜。'
        '对白归属以speech.speaker_name为准，不能用原文或picture把实际标错的说话人脑补纠正。'
        '读完相邻镜头的完整对白和反应，再判断意义是否缺失；同一动作的拆镜不等于重复发生。'
        '错误须是实际发生的含义或归属变化，指出原文谁做什么、稿件变成谁做什么。'
        '不能仅因省略称呼、使用代词或代表人物表达而推测观众可能误会，就认定实际错人或因果断裂。'
        '只报确有依据、影响理解的错误，不报文采、细腻程度、普通镜头风格、可接受的删减。'
        '错误必须给镜号shot、实际字段field、原文连续引文source_quote、该镜该字段中的连续引文draft_quote，'
        '以及一两句话viewer_error，明确观众会误解什么。引用只作定位，不能因为引用字面不同就判错。'
        '对错误省略可引用相邻实际镜头定位，或shot=0、field=omissions引用省略记录。'
        '没有实质错误就verdict=pass、errors=[]；有证据的实质错误才fail；无法形成判断则incomplete。'
        'note可简短解释接受的改编选择，不列逐句差异。不声称稿中不存在的台词已经表达，不输出推理过程。\n'
        +json.dumps(inputs,ensure_ascii=False))
    answer=ask_json([{'type':'text','text':prompt}],SCHEMA,name='retained_content_review',
                    max_tokens=3000,timeout=180,retry_truncated=False,settings=judge_settings())
    verdict={**answer,'policy':POLICY,'section':section,'inputs':inputs}
    return verdict,interpret(verdict,section)
