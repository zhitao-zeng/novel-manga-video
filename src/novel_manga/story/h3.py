"""Pure H3 request expression and checks, sharing picture and dialogue bindings."""
from __future__ import annotations
import re
from novel_manga.runtime_backends import normalize_text
from .dialogue import indexed_pictures, subject_map

SUBJECT_DECLARATION = '<Subject {subject}> is the person shown in <Picture {picture}>.'
DECLARED_SUBJECT = re.compile(re.escape(SUBJECT_DECLARATION)
                             .replace(re.escape('{subject}'), r'(\d+)')
                             .replace(re.escape('{picture}'), r'\d+'))

CHARACTER_TRAIT = re.compile(r"([^\s，。；<>]{1,12})的辨识特征[:：]\s*([^。\n]{1,80})")


STAGE = re.compile(r"【阶段[^】]*】(.*?)(?=【阶段|画面呈现|$)", re.S)


SOUND = re.compile(r"声音：.*?(?=结束时：|$)", re.S)


TURN = re.compile(r"中文普通话，([^，]*)，(.*?)(?:开口说|说)：\{([^}]*)\}(，画面中无人开口)?")


CJK = re.compile(r"[\u4e00-\u9fff]")


def stages_of(prompt: str) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """Each stage as (visual Chinese text, [(speaker, line, offscreen)])."""
    out = []
    for block in STAGE.findall(prompt):
        turns = [(who.strip(), text.strip(), bool(off)) for _, who, text, off in TURN.findall(block)]
        visual = re.sub(r"\s+", " ", SOUND.sub("", block)).strip(" 。")
        out.append((visual, turns))
    return out


def subject_lines(clip: dict) -> tuple[list[str], dict]:
    """The subject_definitions block, and the name -> <Subject N> map the shots will use."""
    defs, subject_of = [], subject_map(clip)
    for picture, ref in indexed_pictures(clip):
        if ref.get("role") == "character":
            crowd=clip.get('crowd_roles',{}).get(ref['name'])
            if crowd:
                defs.append(f"<Picture {picture}> provides shared clothing only for {crowd['count'] or 'several'} distinct unnamed supporting people. "
                            'Their faces and hairstyles must be different from each other and must not copy the face in this picture. '
                            'For a pair, one has a narrow face and the other a broad face. This is a clothing reference, not one repeated identity.')
                continue
            # One instance, and nobody else wears this face: 雾月's most common defect (321 clips on 2026-09-14) was
            # the lead's face or coat on a second person, and the Chinese binding's "只出现一次" never reached H3.
            # The name is decorative here (shots address <Subject N>), and H3 reads it out: the
            # speech invented in wordless shots was largely these names.  The guide also asks for
            # English everywhere outside <d>.
            defs.append(SUBJECT_DECLARATION.format(subject=picture, picture=picture) + ' ' +
                        f"Take only the face, hair, build and clothing from <Picture {picture}>. Exactly one "
                        f"<Subject {picture}> appears in the video; no other person has <Subject {picture}>'s face, hair or clothes.")
        elif ref.get("role") == "location":
            defs.append(f"<Picture {picture}> is the setting shown in it: take its architecture, ground, "
                        f"fixed props and light from it, and none of the people in it.")
    voice = 0
    for ref in (clip.get("references") or []):
        if ref.get("role") == "voice":
            voice += 1
            if ref["name"] in subject_of:
                defs.append(f"<Audio {voice}> is the voice-timbre reference for <Subject {subject_of[ref['name']]}>.")
    return defs, subject_of


def compose(clip: dict, english: list[str], stages: list, note: str = "") -> str:
    """The six sections MiniMax's own guide prescribes (skills/h3-prompt-writing/references/ref-en.txt), and only
    those: a `director_note` section is not in the format, so a correction goes into the summary.  Speakers carry
    stable (Sx) ids next to their subject tag; an off-screen line uses the guide's exact phrase and is followed by
    the statement that the on-screen characters' lips stay closed (the "wrong mouth moves" defect)."""
    defs, subject_of = subject_lines(clip)
    speaker_ids: dict = {}

    def sid(key: str) -> str:
        if key not in speaker_ids:
            speaker_ids[key] = f"(S{len(speaker_ids) + 1})"
        return speaker_ids[key]

    body = []
    for index, ((_, turns), text) in enumerate(zip(stages, english), 1):
        if 'dialogue_bindings' in clip:
            turns = [(r['speaker_name'],r['text'],r['delivery_mode']=='offscreen_dialogue')
                     for r in clip['dialogue_bindings'] if r['stage']==index]
        body.append(f"[Shot {index}] {text}")
        for who, line, offscreen in turns:
            if who in subject_of and not offscreen:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says <d>[Chinese] {line}</d>")
            elif who in subject_of:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says in an off-screen voiceover <d>[Chinese] {line}</d> "
                            "The on-screen characters' lips remain closed.")
            else:
                body.append(f"An off-screen voice {sid(who or 'off-screen')} says in an off-screen voiceover <d>[Chinese] {line}</d> "
                            "The on-screen characters' lips remain closed.")
    seconds = clip.get("request_seconds")
    retention = []
    for ref in (clip.get("references") or []):
        if ref.get("role") == "character" and ref["name"] in subject_of:
            n = subject_of[ref["name"]]
            retention.append(f"<Subject {n}>: fully_preserved - the identity, face, hair and clothing of <Picture {n}>; one instance in every shot it appears in.")
    # The task prefix names what the references actually are; "audio reference" only when a voice
    # reference is really attached.
    has_audio = any(ref.get("role") == "voice" for ref in (clip.get("references") or []))
    task = "[reference generation + audio reference]" if has_audio else "[reference generation]"
    return ("subject_definitions:\n" + "\n".join(defs)
            + f"\n\nsummary:\n{task} A continuous {seconds}-second Chinese "
              f"animated short-drama shot in {len(stages)} stages."
            + (f" Direction for this take: {note}" if note else "") + "\n\n"
              "retention_analysis:\n" + "\n".join(retention) + ("\n" if retention else "")
            + "The setting: fully_preserved - its architecture, fixed props and light come from its own "
              "picture, and none of the people in it.\n\n"
              "detailed_description:\n" + "\n".join(body)
            + "\n\noverall_soundscape:\nContinuous room tone for this setting, with the physical sounds of "
              "the action described above: footsteps, the rustle of clothing, the handling of objects, and the "
              "air of the space.\n\n"
              "non_diegetic_music:\nNone.")


def tag_names(text: str, naming: str) -> str:
    """Resolve full names and unambiguous short forms to their supplied subject tags."""
    names, shorts = {}, {}
    for line in naming.splitlines():
        if " = " not in line:
            continue
        name, tag = (part.strip() for part in line.split(" = ", 1))
        if name:
            names[name] = tag
            short = name.split("·")[0]
            if len(short) >= 2:
                shorts.setdefault(short, set()).add(tag)
    mapping = {**{name: next(iter(tags)) for name, tags in shorts.items() if len(tags) == 1}, **names}
    if not mapping:
        return text
    # One longest-first replacement: 林凡 must not consume 林凡青, and a
    # shared abbreviated name must not silently choose one of two people.
    pattern = re.compile("|".join(re.escape(name) for name in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda match: mapping[match.group()], text)


META = re.compile(r"tag list|translat|original text|system prompt|the user|please verify|contradiction|instruction says", re.I)


def clean_note(text: str, naming: str) -> str:
    """The instruction without the model's asides.  Asked to map names to tags itself, the model answered with
    commentary - "a character name (莱恩·格雷) that is not in the provided tag list, I have translated it as
    <Subject 1>", "the user's request contains a contradiction" - and the CJK check threw the whole answer away
    three tries in a row (雾月 pilot, 2026-09-13: six episodes looping).  Names become tags, a sentence that
    talks about the task or still carries Chinese is dropped, the rest must be non-empty."""
    text = tag_names(text, naming)
    kept = [part for part in re.split(r"(?<=[.;!?])\s+", text) if part.strip() and not CJK.search(part) and not META.search(part)]
    return " ".join(kept).strip()


def final_dialogue_issues(clip: dict) -> list[str]:
    if 'dialogue_bindings' not in clip or not clip.get('prompt_h3'):
        return []
    subjects = subject_map(clip)
    body = clip['prompt_h3'].split('detailed_description:',1)[-1].split('overall_soundscape:',1)[0]
    actual = []
    for block in re.finditer(r'\[Shot (\d+)\](.*?)(?=\[Shot \d+\]|\Z)', body, re.S):
        for speech in re.finditer(r'(?:(?:<Subject (\d+)>)|(?:An off-screen voice))\s+\(S\d+\)\s+says'
                                  r'( in an off-screen voiceover)?\s*<d>\[Chinese\]\s*(.*?)</d>', block[2], re.S):
            actual.append((int(block[1]), int(speech[1]) if speech[1] else None,
                           bool(speech[2]) or speech[1] is None, normalize_text(speech[3])))
    expected = [(r['stage'], subjects.get(r['speaker_name']),
                 r['delivery_mode']=='offscreen_dialogue' or r['speaker_name'] not in subjects,
                 normalize_text(r['text'])) for r in clip['dialogue_bindings']]
    if body.count('<d>') != len(actual):
        return ['dialogue binding: final request contains speech without an identified owner']
    if len(actual) != len(expected):
        return [f'dialogue binding: expected {len(expected)} lines, final request has {len(actual)}']
    return [f'dialogue binding: line {i} disagrees with the packed source owner, stage or words'
            for i, (want, got) in enumerate(zip(expected, actual), 1) if want != got]


def request_issues(clip: dict) -> list[str]:
    text = str(clip.get('prompt_h3') or '')
    body = text.split('detailed_description:',1)[-1]
    plural = re.compile(r'\b(?:two|three|four|five|six|several|multiple|[2-9])\s+'
                        r'(?:(?:identical|different|distinct)\s+)?(?:(?:copies|instances)\s+of\s+)?'
                        r'<Subject\s+(\d+)>',re.I)
    issues = [f'subject {n} is one identity but is requested as multiple people'
              for n in sorted(set(plural.findall(body)))]
    definitions = text.split('summary:', 1)[0]
    declared = set(DECLARED_SUBJECT.findall(definitions))
    # Existing clips use the previous declaration; their saved requests stay inspectable.
    declared.update(re.findall(r'<Subject\s+(\d+)> is the character\b', definitions))
    if declared or 'subject_definitions:' in text:
        issues.extend(f'subject {n} has no identity definition' for n in sorted(set(re.findall(r'<Subject\s+(\d+)>',body))-declared))
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
