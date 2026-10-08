"""One model-authored source draft; the existing host owns adoption and execution."""
from __future__ import annotations

import json
import copy
import math
import os
import re
import time

from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.util import atomic_write_json, read_json
from novel_manga.story.h3 import CJK
from novel_manga.story.fields import ACTION_KEYS


def primary_assets(catalog):
    """Start with the published primary view; the repair Agent retains all views."""
    chosen = {}
    for row in catalog:
        chosen.setdefault((row['role'], row['name']), row)
    return list(chosen.values())


def usable_assets(ctx, catalog):
    """A broken picture is a missing asset, not a reason to abort the episode."""
    selected, pictures, broken = {}, {}, []
    for row in catalog:
        key = (row['role'], row['name'])
        if key in selected:
            continue
        try:
            if row['role'] != 'voice':
                pictures[row['id']] = image_part(ctx.work_dir/row['image'], 512)
        except (OSError, ValueError) as error:
            broken.append({'id': row['id'], 'role': row['role'], 'name': row['name'],
                           'error': f'{type(error).__name__}: unusable reference image'})
            continue
        selected[key] = row
    if broken:
        bad = {r['id'] for r in broken}
        usable = [r for r in catalog if r['id'] not in bad]
        atomic_write_json(ctx.work_dir/'input/unusable-assets.json', broken)
        atomic_write_json(ctx.run_dir/'catalog.json', usable)
        atomic_write_json(ctx.work_dir/'input/catalog.json', usable)
    return list(selected.values()), pictures


def clip_budget(target_seconds, clip_seconds_max):
    """Allocate the fixed production duration before the model chooses its scenes."""
    total = sum(target_seconds)/2
    count = math.ceil(total/clip_seconds_max)
    seconds = round(total/count, 3)
    return [seconds]*(count-1) + [round(total-seconds*(count-1), 3)]


def source_schema(clip_count):
    text = {'type': 'string'}
    english = {'type': 'string', 'description': 'English prose only; Chinese is allowed only in speech.text.'}
    names = {'type': 'array', 'items': text}
    speech = obj({'speaker_name': text, 'text': text,
                  'kind': {'enum': ['speech', 'inner_voice', 'chat_message']},
                  'emotion': text, 'chat_target': text}, ['speaker_name', 'text', 'kind'])
    shot = obj({
        **{k: text for k in ('location', 'start_state', 'event', 'end_state', 'camera', 'light', 'sfx')},
        **{k: names for k in ('segment_ids', 'in_frame', 'extras', 'props', 'scene_objects',
                             'listeners', 'independent_props')},
        'wears': {'type': 'object'},
        'actions': {'type': 'array', 'items': obj({k: text for k in ACTION_KEYS})},
        'duration_seconds': {'type': 'number'},
        'beats': {'type': 'array', 'minItems': 1, 'items': {'oneOf': [obj({'direction': english}), speech]}},
    }, ['segment_ids', 'in_frame', 'location', 'start_state', 'event', 'end_state', 'camera', 'beats'])
    return obj({'title': text, 'adaptation_notes': names,
                'clips': {'type': 'array', 'minItems': clip_count, 'maxItems': clip_count,
                          'items': obj({'reference_ids': names, 'soundscape': english,
                                        'shots': {'type': 'array', 'minItems': 1, 'items': shot}})}})


def bind_source(raw, budget, profile):
    """Bind the normal writer's ordered beats to the existing public episode contract.

    The writer owns content, source, visibility and staging. Addresses, fixed clip
    slots, speech visibility and H3 dialogue markers come from those choices once.
    Existing public drafts never pass through this normal-production adapter.
    """
    if len(raw['clips']) != len(budget):
        raise ValueError(f'normal source writer must fill {len(budget)} allocated clip slots')
    shots, clips = [], []
    for number, block in enumerate(raw['clips']):
        seconds = budget[number]
        indexes, bodies = [], []
        for authored in block['shots']:
            shot = copy.deepcopy(authored)
            if 'turns' in shot:
                raise ValueError('normal source writer supplies speech in beats, not a second turns array')
            beats = shot.pop('beats')
            turns, body, native = [], [], 0
            for beat in beats:
                if 'direction' in beat:
                    if set(beat) != {'direction'} or not isinstance(beat['direction'], str):
                        raise ValueError('a direction beat must contain only its English direction')
                    body.append(beat['direction'])
                    continue
                if (not {'kind', 'speaker_name', 'text'} <= set(beat)
                        or set(beat)-{'kind', 'speaker_name', 'text', 'emotion', 'chat_target'}):
                    raise ValueError('speech beat requires its declared kind and speech fields only')
                kind, owner = beat['kind'], beat['speaker_name']
                turn = {k: beat[k] for k in ('speaker_name', 'text', 'emotion', 'chat_target') if k in beat}
                if kind == 'inner_voice':
                    turn.update(delivery_mode='offscreen_dialogue', inner_monologue=True)
                elif kind == 'speech':
                    turn['delivery_mode'] = 'visible_dialogue' if owner in shot['in_frame'] else 'offscreen_dialogue'
                elif kind == 'chat_message':
                    turn['delivery_mode'] = kind
                else:
                    raise ValueError(f'unknown speech beat kind: {kind}')
                turns.append(turn)
                if (kind == 'speech' or (kind == 'inner_voice' and profile.get('inner_voice_delivery') != 'postmix')):
                    native += 1
                    body.append(f'<DIALOGUE_{native}>')
            index = len(shots)+1
            shot.update(index=index, turns=turns,
                        characters=list(dict.fromkeys([*shot.get('characters', []), *shot['in_frame'],
                                                       *(t['speaker_name'] for t in turns)])))
            if len(block['shots']) == 1:
                shot['duration_seconds'] = seconds
            shots.append(shot); indexes.append(index); bodies.append(' '.join(body))
        clips.append({'shot_indexes': indexes, 'request_seconds': seconds,
                      'reference_ids': copy.deepcopy(block['reference_ids']),
                      'h3': {'shots': bodies, 'soundscape': block['soundscape']}})
    return {'title': raw['title'], 'adaptation_notes': copy.deepcopy(raw['adaptation_notes']),
            'chapter_script': {'shots': shots}, 'clips': clips}


def translate_directions(draft, *, timeout, settings=None):
    """One bounded language pass on H3 prose; authored speech/layout is inaccessible."""
    fields = {}
    for i, clip in enumerate(draft['clips'], 1):
        for j, value in enumerate(clip['h3']['shots'], 1):
            if CJK.search(value):
                fields[f'clip_{i:02d}.shot_{j:02d}'] = (i-1, j-1, value)
        if CJK.search(clip['h3']['soundscape']):
            fields[f'clip_{i:02d}.soundscape'] = (i-1, None, clip['h3']['soundscape'])
    if not fields:
        return draft, {'status': 'skipped', 'fields': 0}
    if timeout <= 0:
        raise TimeoutError('normal drafting budget exhausted before H3 language conversion')
    parts = [{'type': 'text', 'text':
        'Translate only these keyed H3 visual/sound descriptions into clear English. '
        'This is translation of an existing production draft, not new story writing. '
        'Keep every actor, target, action, camera choice, physical sound and event order. '
        'Keep all <Subject N>, <Picture N>, <Audio N> and <DIALOGUE_N> markers exactly '
        'once in their original order and positions relative to actions. Do not invent '
        'markers, add dialogue, section headers, instructions to the operator or new events. '
        'Translate or transliterate proper names and technical terms into English too; '
        'do not append the original Chinese in parentheses. '
        'Return each supplied id exactly once with its English-only text_en.\n'
        + json.dumps([{'id': key, 'text': value[2]} for key, value in fields.items()], ensure_ascii=False)}]
    schema = obj({'translations': {'type': 'array', 'items': obj({
        'id': {'type': 'string'}, 'text_en': {'type': 'string', 'description': 'English only'}})}})
    response = ask_json(parts, schema, name='episode_source_translation', max_tokens=12000,
                        timeout=timeout, retry_truncated=False, settings=settings, json_object=True)
    rows = response['translations']
    translated = {row['id']: row['text_en'] for row in rows}
    if len(translated) != len(rows) or set(translated) != set(fields):
        raise ValueError('translation must name each requested H3 field exactly once')
    tags = re.compile(r'<(?:(?:Subject|Picture|Audio) \d+|DIALOGUE_\d+)>')
    accepted, rejected = {}, {}
    for key, value in translated.items():
        if not isinstance(value, str) or not value.strip() or CJK.search(value):
            rejected[key] = 'translation must contain English prose'
        elif tags.findall(value) != tags.findall(fields[key][2]):
            rejected[key] = 'translation changed an authored binding marker'
        else:
            accepted[key] = value
    candidate = copy.deepcopy(draft)
    for key, value in accepted.items():
        clip, shot, _ = fields[key]
        if shot is None:
            candidate['clips'][clip]['h3']['soundscape'] = value
        else:
            candidate['clips'][clip]['h3']['shots'][shot] = value
    status = 'translated' if not rejected else 'partial' if accepted else 'failed'
    return candidate, {'status': status, 'fields': len(fields), 'accepted': list(accepted),
                       'rejected': rejected, 'proposed': translated}


def propose(ctx, *, settings=None, timeout=300):
    """Create a candidate once, keeping any existing draft/session authoritative.

    Structural, source, reference and media checks run through EpisodeTools.
    A failed model call or invalid draft is evidence for the repair Agent, never
    a reason to repeat this normal production step or reset a generation budget.
    """
    target = ctx.work_dir/'output/episode.json'
    receipt = ctx.run_dir/'source-draft-call.json'
    if ctx.input_mode != 'source':
        raise ValueError('direct source drafting requires source input mode')
    if target.exists() or receipt.exists() or (ctx.work_dir/'episode-session.json').exists():
        return {'skipped': True, 'reason': 'existing source work retained'}
    manifest = read_json(ctx.work_dir/'input/manifest.json')
    if not manifest.get('ready'):
        raise ValueError('source drafting needs prepared inputs')
    assets, pictures = usable_assets(ctx, read_json(ctx.run_dir/'catalog.json'))
    profile = read_json(ctx.novel_dir/'profile.json')
    from novel_manga.application.profiles import load_style
    budget = clip_budget(manifest['target_seconds'], manifest['clip_seconds_max'])
    names = {r['name'] for r in assets if r['role'] == 'character'}
    bible = read_json(ctx.novel_dir/'story_bible.json')
    from novel_manga.models.bible import Character
    from novel_manga.application.identity.phases import load_phases, phase_for, phased, chapter_of
    phases = load_phases(ctx.novel_dir)
    chapter = chapter_of(ctx.episode_dir)
    characters = [phased(Character.model_validate({'appearance': '', 'wardrobe': '', **c}),
                         phase_for(phases, c['name'], chapter)).model_dump()
                  for c in bible.get('characters', []) if c['name'] in names]
    context = {
        'title': manifest['title'], 'source': (ctx.work_dir/'input/source.txt').read_text(),
        'source_segments': read_json(ctx.episode_dir/'segments.json'),
        'production_profile': profile, 'visual_style': load_style(profile, ctx.novel_dir),
        'target_seconds': manifest['target_seconds'],
        'clip_seconds_max': manifest['clip_seconds_max'],
        'clip_budget': budget,
        'maximum_chinese_dialogue_characters_per_clip': [int(seconds*6) for seconds in budget],
        'source_reading': read_json(ctx.work_dir/'input/reading/chapter-extract.json', {}).get('answer', {}),
        'available_assets': [{k: r.get(k) for k in ('id', 'role', 'name')} for r in assets],
        'unusable_assets': read_json(ctx.work_dir/'input/unusable-assets.json', []),
        'character_designs': [{k: c.get(k) for k in ('name', 'role', 'gender', 'age', 'appearance', 'wardrobe', 'hair')}
                              for c in characters],
    }
    prompt = (
        'You are adapting one complete novel chapter into a short cinematic episode. '
        'Write the ordered clips, shots and performance beats in the supplied JSON schema in one response. '
        'Adapt dialogue into concise complete sentences, around 4–5 Chinese characters per second; '
        'never exceed the supplied per-clip speech budgets. Long source quotations are facts to adapt, '
        'not instructions to recite every word. Keep decisive questions, answers, negations, conditions and speaker ownership. '
        'The source alone owns identities, events, causal conditions and the dialogue meaning. '
        'source_reading contains previously extracted chapter evidence: use its people/presence, '
        'appearance and state changes to find source facts efficiently. Verify it against the '
        'complete source when it conflicts with a library description; it is not a cast mandate. '
        'The asset catalogue is a menu, not an on-screen cast list; do not add a character '
        'because a short name happens to occur inside another word. '
        'Inspect the supplied actual primary images for stable appearance. Reference pose, '
        'camera and background occupants are not required in the new shot. '
        'Use the actual visual_style specification and production frame. Describe directions positively. '
        'Choose concise, complete Chinese dialogue and show the essential causes, decisions '
        'and ending on screen; the adaptation notes do not replace what a viewer can hear/see. '
        'The host has allocated clip_budget: produce one clip block per entry in order. '
        'Each block already owns its fixed request duration; do not return request_seconds, '
        'shot_indexes, index or a separate chapter_script. You choose its story content, references '
        'and one or more shots. A single-shot block automatically owns the whole slot. '
        'For a multi-shot block only, give each shot duration_seconds summing to that slot. '
        'Each clip has one location. Reference actual source segment IDs for every shot. '
        'Keep complete causes, questions/answers, decisions and the ending, compressing dialogue '
        'meaning faithfully instead of copying long speeches into a short slot. Plan around '
        'four spoken Chinese characters per second with room for turns and action; this is '
        'pacing guidance, not a request to shorten duration numbers or drop required information. '
        'Distribute talk across the scene instead of long silent holds followed by an overloaded final clip. '
        'Avoid repeated reactions and duplicated actions. '
        'Within each shot, beats is the actual playback order. A direction beat contains only '
        'direction: English camera/acting prose using the selected <Subject N> tags. '
        'A speech beat contains speaker_name, exact chosen Chinese text and kind: speech, '
        'inner_voice or chat_message. Describe wordless humming and other nonverbal vocals '
        'in English direction/soundscape with the performing Subject tag, not as a speech beat. '
        'Both direction and soundscape are English only; '
        'soundscape describes physical environment sounds, not another recital of planned speech. '
        'Do not put spoken words into direction or soundscape. '
        'Do not supply dialogue placeholders, delivery_mode, a second turns list, H3 headers, '
        'speaker IDs, cut times or numbered shot headers: the host binds them once from beats. '
        'in_frame is your authoritative list of visible named characters. A speech owner in '
        'that list is visibly speaking; an owner outside it is heard off screen. Inner_voice is '
        'audience-only and is never heard by other characters. Silent shots contain directions only. '
        'Postmix inner voice requires a pure thought clip, its thinker card and voice; stage the '
        'thinker without visible speech and keep its words in the inner_voice beat. '
        'Each clip selects actual reference_ids, in order: unique character primary cards first, '
        'then setting/prop cards, then optional voices. Subject IDs number unique character names '
        'first in that image order, then other image assets. H3 bodies use those <Subject N> tags. '
        'Every in_frame character needs its correct character card. Choose visibility for the '
        'story and camera, never turn intended visible speech into offscreen speech just to hide a missing card. '
        'If a needed asset is unavailable, explain the concrete problem in adaptation_notes; '
        'never invent a reference_id. Return only the complete JSON object.\n\n'
        + 'Actual chapter and inputs:\n' + json.dumps(context, ensure_ascii=False)
    )
    parts = [{'type': 'text', 'text': prompt}]
    for row in assets:
        if row['role'] == 'voice':
            continue
        parts.append({'type': 'text', 'text': f"Asset {row['id']}: {row['role']} / {row['name']}"})
        parts.append(pictures[row['id']])
    schema = source_schema(len(budget))
    record = {'status': 'running', 'format': 'source-beats-v1', 'started_at': time.time(),
              'model': settings.model if settings else os.environ.get('QWEN38_LOCAL_MODEL'),
              'assets': [row['id'] for row in assets], 'target_seconds': manifest['target_seconds']}
    atomic_write_json(receipt, record)
    started = time.monotonic()
    try:
        raw = ask_json(parts, schema, name='episode_source_draft', max_tokens=16000, timeout=timeout,
                       retry_truncated=False, settings=settings, json_object=True)
        atomic_write_json(ctx.work_dir/'input/source-draft-raw.json', raw)
        draft = bind_source(raw, budget, profile)
        atomic_write_json(target, draft)
        record.update(status='candidate', output='output/episode.json')
        record['draft_seconds'] = round(time.monotonic()-started, 3)
        atomic_write_json(receipt, record)
        language_started = time.monotonic()
        try:
            draft, language = translate_directions(draft, settings=settings,
                timeout=min(120, timeout-(time.monotonic()-started)))
            if language['status'] in {'translated', 'partial'}:
                atomic_write_json(target, draft)
        except Exception as error:
            language = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
        language['seconds'] = round(time.monotonic()-language_started, 3)
        record['translation'] = {k: v for k, v in language.items() if k != 'proposed'}
        atomic_write_json(ctx.run_dir/'source-draft-translation.json', language)
    except Exception as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}')
    finally:
        record['seconds'] = round(time.monotonic()-started, 3)
        atomic_write_json(receipt, record)
    return record
