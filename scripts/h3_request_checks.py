"""Check identity contradictions in the actual English request before a new take."""
from __future__ import annotations

import re


def request_issues(clip: dict) -> list[str]:
    text = str(clip.get('prompt_h3') or '')
    body = text.split('detailed_description:',1)[-1]
    plural = re.compile(r'\b(?:two|three|four|five|six|several|multiple|[2-9])\s+'
                        r'(?:(?:identical|different|distinct)\s+)?(?:(?:copies|instances)\s+of\s+)?'
                        r'<Subject\s+(\d+)>',re.I)
    issues = [f'subject {n} is one identity but is requested as multiple people'
              for n in sorted(set(plural.findall(body)))]
    definitions = text.split('summary:', 1)[0]
    declared=set(re.findall(r'<Subject\s+(\d+)> is the (?:character\b|person shown in <Picture\s+\d+>)',definitions))
    if declared or 'subject_definitions:' in text:
        issues.extend(f'subject {n} has no identity definition' for n in sorted(set(re.findall(r'<Subject\s+(\d+)>',body))-declared))
    from dialogue_binding import final_dialogue_issues
    return issues + final_dialogue_issues(clip)


def source_crowds(clip: dict, bible: dict, passage: str, *, context=None) -> dict:
    """A literal plural occupational role denotes people, not repeated identity.

    Limited to silent unnamed roles whose role description repeats that label.
    Named characters and ambiguous speaking groups keep individual attribution.
    """
    by_name={c['name']:c for c in bible.get('characters',[])}
    speaking={t.get('speaker_name') for t in clip.get('lines',[]) if t.get('text')}
    result={}
    if context:
        from novel_manga.story.identity import canonical_entity
        for m in context.get('mentions', []):
            if m.get('entity_kind') != 'group' or m['entity_id'] == 'UNKNOWN':
                continue
            name = context['entities'].get(canonical_entity(context, m['entity_id']))
            if name in clip.get('cast', []) and m['form'] in passage:
                result[name] = {'count': m.get('count', 0), 'source_quote': m.get('source_quote', '')}
        return result
    numbers={'两':2,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    for ref in clip.get('references',[]):
        name=ref.get('name','')
        role = str(by_name.get(name,{}).get('role',''))
        if (ref.get('role')!='character' or not 2<=len(name)<=4 or '·' in name or name in speaking
                or (name not in role and not any(label in role for label in ['群演','背景角色']))):
            continue
        found=re.search(r'([两二三四五六七八九2-9])[名位个](?:[^，。！？；：\n]{0,18}的)?(?:男|女)?'+re.escape(name),passage)
        if found:
            result[name]={'count':numbers.get(found[1],int(found[1]) if found[1].isdigit() else 0),'source_quote':found[0]}
    return result


def correction(clip: dict) -> str:
    """Describe the concrete conflict without prescribing invented identities."""
    issues = request_issues(clip)
    if not issues:
        return ''
    return ('实际英文请求把同一个人物编号写成了多个人：'+'；'.join(issues)+
            '。先核对原文人数。如果原文确为两名不同的无名配角，把他们作为外貌可区分的匿名配角写入 extras；'
            '不要把集体称呼当一个具名人物放进 in_frame、characters 或 actions 的 actor/target，'
            '否则同一张脸会被强制复制。用事件句描述配角的共同动作，保留原文人数和行为。'
            '如果原文只有一人，改回一个人物，不增加人。')
