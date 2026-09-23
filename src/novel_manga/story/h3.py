"""Pure H3 request expression and checks, sharing picture and dialogue bindings."""
from __future__ import annotations
import re
from novel_manga.runtime_backends import normalize_text
from .dialogue import character_pictures, indexed_pictures, subject_map

# One subject can be shown by more than one picture, so the declaration names them all.  The pattern
# still matches the older one-picture wording, which is what every saved request contains.
SUBJECT_DECLARATION = '<Subject {subject}> is the person shown in {pictures}.'
DECLARED_SUBJECT = re.compile(re.escape(SUBJECT_DECLARATION)
                             .replace(re.escape('{subject}'), r'(\d+)')
                             .replace(re.escape('{pictures}'), r'[^.]+'))

CHARACTER_TRAIT = re.compile(r"([^\s，。；<>]{1,12})的辨识特征[:：]\s*([^。\n]{1,80})")


STAGE = re.compile(r"【阶段[^】]*】(.*?)(?=【阶段|画面呈现|$)", re.S)


SOUND = re.compile(r"声音：.*?(?=结束时：|$)", re.S)


TURN = re.compile(r"中文普通话，([^，]*)，(.*?)(?:开口说|说)：\{([^}]*)\}(，画面中无人开口)?")


CJK = re.compile(r"[\u4e00-\u9fff]")


# The medium this book is rendered in, and the one sentence that tells H3 what it is.  Packing resolves
# the style package to its family; a clip packed before it did carries the package's own name, and then
# we say nothing rather than assert the wrong medium - the reference cards already carry the look, and a
# sentence that contradicts them is worse than no sentence.
MEDIUM_SENTENCE = {
    '3d': ' All subjects, animals, props and environments share one consistent stylized 3D animation appearance.',
    '2d': ' All subjects, animals, props and environments share one consistent stylized 2D animation appearance.',
    'photo': ' Everything is photographed live action: real people, real fabrics and real light, with no animation, '
             'no CG characters and no illustrated rendering.',
}


def render_family(clip: dict) -> str:
    """How this clip is rendered - 3d, 2d, photo - or '' when the packed plan cannot say.

    The field this replaced held profile.style, a style package name ("weimei", "live"), and was compared
    against the literal "3d".  Only the two legacy values happen to name a family, so only those are read
    from the old field; \u552f\u7f8e and 3D\u56fd\u6f2b are both rendered in 3d and \u771f\u4eba is photographed.
    """
    family = str(clip.get('render_family') or '')
    if family:
        return family
    legacy = str(clip.get('animation_style') or '')
    return legacy if legacy in {'3d', '2d'} else ''


def stages_of(prompt: str) -> list[tuple[str, list[tuple[str, str, bool, str]]]]:
    """Each stage as (visual Chinese text, [(speaker, line, offscreen, vocal manner)]).

    The manner is the storyboard's 情绪 for that line.  It used to be captured and thrown away, and the
    only route left to it was the shot translation, which is asked for what the camera SEES: 恐惧与无奈
    came back as "with a determined expression" - a face, with the wrong valence, and nothing at all for
    the voice H3 generates.  It is Chinese, so compose() emits it only once it has been translated.
    """
    out = []
    for block in STAGE.findall(prompt):
        # Everything between the delivery manner and 开口说 is captured together, and the storyboard
        # puts the performance notes there as well as the name: "眉头微皱，眼神低垂，尾巴轻摆。，洛恩".
        # The name is the last comma-separated piece; taking the whole run meant it never matched the
        # subject map, and the line was written as a disembodied voiceover with the on-screen mouths
        # explicitly told to stay closed - 41% of all spoken lines across 雾月 and 星海.
        turns = [(who.strip().split("，")[-1].strip(" 。"), text.strip(), bool(off), manner.strip())
                 for manner, who, text, off in TURN.findall(block)]
        visual = re.sub(r"\s+", " ", SOUND.sub("", block)).strip(" 。")
        out.append((visual, turns))
    return out


def view_of(ref: dict) -> str:
    """Which card this picture is: 'expressions' is the chest-up portrait, 'turnaround' the full figure."""
    return str(ref.get('view') or '').strip() or str(ref.get('path') or '').rsplit('/', 1)[-1].split('.')[0]


def picture_phrase(numbers) -> str:
    tags = [f'<Picture {n}>' for n in numbers]
    return tags[0] if len(tags) == 1 else ' and '.join([', '.join(tags[:-1]), tags[-1]])


def subject_lines(clip: dict) -> tuple[list[str], dict]:
    """The subject_definitions block, and the name -> <Subject N> map the shots will use."""
    defs, subject_of = [], subject_map(clip)
    pictures, declared = character_pictures(clip), set()
    for picture, ref in indexed_pictures(clip):
        if ref.get("role") == "character":
            crowd=clip.get('crowd_roles',{}).get(ref['name'])
            if crowd:
                defs.append(f"<Picture {picture}> provides shared clothing only for {crowd['count'] or 'several'} distinct unnamed supporting people. "
                            'Their faces and hairstyles must be different from each other and must not copy the face in this picture. '
                            'For a pair, one has a narrow face and the other a broad face. This is a clothing reference, not one repeated identity.')
                continue
            name = ref['name']
            if name in declared:
                continue  # a second view of someone already declared: same subject, another picture
            declared.add(name)
            subject, own = subject_of[name], pictures[name]
            numbers = [n for n, _ in own]
            # One instance, and nobody else wears this face: 雾月's most common defect (321 clips on 2026-09-14) was
            # the lead's face or coat on a second person, and the Chinese binding's "只出现一次" never reached H3.
            # The name is decorative here (shots address <Subject N>), and H3 reads it out: the
            # speech invented in wordless shots was largely these names.  The guide also asks for
            # English everywhere outside <d>.
            face = next((n for n, r in own if view_of(r) == 'expressions'), None)
            body = next((n for n, r in own if view_of(r) != 'expressions'), numbers[0])
            if face is not None and len(numbers) > 1:
                # Each picture's job, the way the Chinese binding already splits them: the bust stops at the
                # collar, so asking it for the cut of a costume it does not show leaves the model to invent one.
                take = (f"Take the face, hair, age and skin tone from <Picture {face}>, and the body proportions, "
                        f"garment cut, main colours and accessories from <Picture {body}>.")
            else:
                take = f"Take only the face, hair, build and clothing from <Picture {body}>."
            defs.append(SUBJECT_DECLARATION.format(subject=subject, pictures=picture_phrase(numbers)) + ' ' + take +
                        f" Exactly one <Subject {subject}> appears in the video; no other person has "
                        f"<Subject {subject}>'s face, hair or clothes.")
            if clip.get('scene_ids'):
                defs[-1] = defs[-1].replace(
                    f'Take only the face, hair, build and clothing from <Picture {body}>.',
                    f'Take the face, hair, build and base garment design from <Picture {body}>. '
                    'The authored shot determines which garments are currently worn, removed or wet.')
        elif ref.get("role") == "location":
            if clip.get('scene_ids'):
                defs.append(f"<Picture {picture}> defines the setting's architecture and terrain. "
                            'Use the time of day and lighting specified in each shot; people and portable objects come from the shot description.')
            else:
                defs.append(f"<Picture {picture}> is the setting shown in it: take its architecture, ground, "
                            f"fixed props and light from it, and none of the people in it.")
        elif ref.get("role") == "prop":
            # Not a subject: no face, no single-instance clause, no part in the shot's <Subject N> addressing.
            # The Chinese binding says the same thing - appearance, material, structure, unchanged scale.
            defs.append(f"<Picture {picture}> is a prop shown in it: take its appearance, material and structure "
                        'exactly as drawn, at its drawn scale relative to the people; it is neither a person nor a subject.')
    return defs, subject_of


def voice_lines(clip: dict, subject_of: dict, speaker_ids: dict) -> list[str]:
    """`<Audio N> is the voice-timbre reference for <Subject N> (Sx).`

    The guide asks for the speaker id here, not only the subject: "reuse that speaker's global ID in
    the definition... The ID comes from the target video's global speaker order and is not
    independently assigned or renumbered in the audio definition."  That is the one binding the
    community reports as fixing two-speaker timbre swapping, and we wrote the line without it - which
    left H3 to pair <Audio 1> with a speaker by position.  The ids are only known after the body has
    been written, so this is built there rather than in subject_lines.
    """
    lines, voice = [], 0
    for ref in (clip.get("references") or []):
        if ref.get("role") != "voice":
            continue
        voice += 1
        name = ref.get("name")
        if name not in subject_of:
            continue
        # A voice bound to someone who never opens their mouth in this clip has no speaker id, and
        # the guide forbids inventing one; the timbre still belongs to that subject.
        said = f" {speaker_ids[name]}" if name in speaker_ids else ""
        lines.append(f"<Audio {voice}> is the voice-timbre reference for <Subject {subject_of[name]}>{said}.")
    return lines


def compose(clip: dict, english: list[str], stages: list, note: str = "", delivery: dict | None = None) -> str:
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
            turns = [(r['speaker_name'],r['text'],r['delivery_mode']=='offscreen_dialogue',r.get('emotion') or '')
                     for r in clip['dialogue_bindings'] if r['stage']==index]
        timing = clip.get('shot_timing', [])
        if clip.get('scene_ids') and len(timing) == len(stages):
            text = f"Planned duration: {timing[index-1]['seconds']:g} seconds. " + text
        body.append(f"[Shot {index}] {text}")
        for who, line, offscreen, *rest in turns:
            # How the line is spoken, in English, after the bound words: the dialogue syntax H3 was trained
            # on - and the checker below - want <d> directly after `says`, so the performance goes next to
            # the line rather than inside the clause that carries it.
            manner = (delivery or {}).get(rest[0] if rest else '', '')
            said = f" {manner}" if manner else ""
            if who in subject_of and not offscreen:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says <d>[Chinese] {line}</d>{said}")
            elif who in subject_of:
                body.append(f"<Subject {subject_of[who]}> {sid(who)} says in an off-screen voiceover <d>[Chinese] {line}</d>{said} "
                            "The on-screen characters' lips remain closed.")
            else:
                body.append(f"An off-screen voice {sid(who or 'off-screen')} says in an off-screen voiceover <d>[Chinese] {line}</d>{said} "
                            "The on-screen characters' lips remain closed.")
    seconds = clip.get("request_seconds")
    retention = []
    for name, own in character_pictures(clip).items():
        n, shown = subject_of[name], picture_phrase([p for p, _ in own])
        retention.append(f"<Subject {n}>: fully_preserved - the identity, face, hair and clothing of {shown}; one instance in every shot it appears in.")
        if clip.get('scene_ids'):
            retention[-1] = (f'<Subject {n}>: fully_preserved - the identity, face, hair and build of {shown}; '
                             'base garment design is retained when worn, while current clothing state follows the authored shot. '
                             'One instance in every shot it appears in.')
    # The task prefix names what the references actually are; "audio reference" only when a voice
    # reference is really attached.
    has_audio = any(ref.get("role") == "voice" for ref in (clip.get("references") or []))
    task = "[reference generation + audio reference]" if has_audio else "[reference generation]"
    animation = ''
    setting_retention = ('The setting: fully_preserved - its architecture, fixed props and light come from its own '
                         'picture, and none of the people in it.')
    family = render_family(clip)
    if clip.get('scene_ids'):
        animation = MEDIUM_SENTENCE.get(family, '')
        setting_retention = ('The setting retains the referenced architecture and terrain; lighting, time of day and '
                             'portable object states follow the authored shots.')
    kind = 'live-action' if family == 'photo' else 'animated'
    summary = (f'A continuous {seconds}-second Chinese {kind} short-drama shot in {len(stages)} stages.'
               if not clip.get('scene_ids') else
               f'A {seconds}-second Chinese {kind} short-drama scene edited into {len(stages)} authored shots.')
    return ("subject_definitions:\n" + "\n".join(defs + voice_lines(clip, subject_of, speaker_ids))
            + f"\n\nsummary:\n{task} " + summary + animation
            + (f" Direction for this take: {note}" if note else "") + "\n\n"
              "retention_analysis:\n" + "\n".join(retention) + ("\n" if retention else "")
            + setting_retention + "\n\n"
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
    # A visible speaker the references cannot show is an upstream miss (a dropped card, a renamed
    # cast), and compiling it as an off-screen voice hides it behind closed lips.  The turn was
    # planned VISIBLE: say so instead of silently degrading it.  A genuine offscreen turn keeps
    # its right to stay out of frame.
    unseen = sorted({r['speaker_name'] for r in clip['dialogue_bindings']
                     if r.get('delivery_mode') == 'visible_dialogue' and r['speaker_name'] not in subjects})
    if unseen:
        return [f"dialogue binding: {', '.join(unseen)} speaks visibly but has no subject picture; "
                "fix the references or the turn, do not demote it to a voiceover"]
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
    # Every picture of one actor belongs to one subject.  Declaring more subjects than the clip has
    # characters means somebody's second reference image became a second person - one told to appear
    # exactly once and that nobody else may share its face, i.e. a twin who must not look like itself.
    people = character_pictures(clip)
    if people and len(declared) > len(people):
        issues.append(f'{len(declared)} subjects are declared for {len(people)} characters: '
                      "one person's reference images were declared as separate people")
    if clip.get('scene_ids') and CJK.search(re.sub(r'<d>.*?</d>', '', text, flags=re.S)):
        issues.append('authored H3 description contains Chinese outside bound dialogue')
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
