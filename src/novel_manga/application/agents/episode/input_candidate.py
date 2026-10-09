"""Prepare a source-scoped scene and asset candidate without publishing it."""
from __future__ import annotations
import copy
from dataclasses import dataclass
from .arguments import RepairArgumentError
from .input_contracts import SHOT_NAMES, SHOT_FIELDS
from .input_compilation import InputCompilation
from novel_manga.application.identity.phases import LOOK_FIELDS, chapter_of
from novel_manga.application.repair import history
from novel_manga.util import read_json


@dataclass(frozen=True)
class PreparedInput:
    compilation: InputCompilation
    submitted_row: dict
    chapter: int
    bible: dict
    base_ids: dict
    reused: dict


def prepare_candidate(ctx, row, plan, script, target, addresses, scope, matching):
    directory, cid = ctx.episode_dir, row['clip_id']
    raw_phases = row.get('phases') or {}
    candidate_script = copy.deepcopy(script)
    shots = {s.get('index', i): s for i, s in enumerate(candidate_script['shots'], 1)}
    card_names = {c['name'] for c in read_json(ctx.novel_dir/'story_bible.json')['characters']}
    card_names.update(row.get('characters') or {})
    patches = row.get('shots') or []
    seen = set()
    for patch in patches:
        index = patch.get('index')
        if index not in addresses or index in seen:
            raise RepairArgumentError(f'input shot index {index} is outside the allowed scope or repeated; '
                                      f'allowed indexes: {sorted(addresses)}; fields: {sorted(SHOT_FIELDS)}')
        seen.add(index)
        speaker_map = patch.get('speaker_map') or {}
        owners = {t.get('speaker_name') for t in shots[index].get('turns', [])}
        if set(speaker_map)-owners:
            raise RepairArgumentError(f'speaker_map names must be current shot speakers: {sorted(owners)}')
        if 'turn_texts' in patch:
            from .speech_budget import compress_turns
            dependent = [c for c in plan['clips'] if index in c.get('shot_indexes', [])]
            shots[index]['turns'] = compress_turns(ctx.input_mode, shots[index], patch['turn_texts'], dependent)
        shots[index].update({k: copy.deepcopy(v) for k, v in patch.items() if k not in {'index', 'speaker_map', 'turn_texts'}})
        for turn in shots[index].get('turns', []):
            if turn.get('speaker_name') in speaker_map:
                turn['speaker_name'] = speaker_map[turn['speaker_name']]
        if set(patch) & SHOT_NAMES or speaker_map or 'turns' in patch:
            # Extras remain described people, not registered portrait identities.
            # A source-described offscreen speaker keeps its identity in turns;
            # it does not acquire a portrait requirement merely by being named.
            shots[index]['characters'] = list(dict.fromkeys([
                *shots[index].get('in_frame', []),
                *(t['speaker_name'] for t in shots[index].get('turns', []) if t.get('speaker_name')
                  and (t['speaker_name'] in card_names or t.get('delivery_mode') == 'visible_dialogue'))]))
    if scope is not None:
        shared = {c['clip_id'] for c in plan['clips'] if seen.intersection(c.get('shot_indexes',[]))}
        if missing := shared-set(scope):
            raise RepairArgumentError('source shot edits also affect clips '+', '.join(sorted(missing))+
                '; include the required shared-shot clips in clip_ids and preserve their existing cuts')
    phases = copy.deepcopy(raw_phases.get('characters', {}))
    locations = copy.deepcopy(row.get('locations') or {})
    selected_locations = {str(shots[i].get('location') or '') for i in addresses}
    if (not isinstance(locations, dict) or any(not isinstance(name, str) or not name.strip()
            or '：' in name or not isinstance(description, str) or not description.strip()
            or name not in selected_locations for name, description in locations.items())):
        raise ValueError('locations must map this clip selected location names to nonempty descriptions')
    required_names = {name for i in addresses for name in shots[i].get('characters', [])}
    names = required_names | {r['name'] for r in target.get('references', []) if r.get('role') == 'character'}
    chapter = chapter_of(directory)
    bible = read_json(ctx.novel_dir/'story_bible.json')
    base_ids = {c['name']: f'character_{i:03d}' for i, c in enumerate(bible['characters'], 1)}
    submitted_row = copy.deepcopy(row)
    characters = copy.deepcopy(row.get('characters') or {})
    for name in list(characters):
        if name not in required_names:
            raise RepairArgumentError(f'characters.{name} is not a named visible actor or speech owner in the affected shots; '
                                      'correct in_frame/listeners/extras first; unnamed crowds stay extras')
        existing_card = (name in base_ids and
            (ctx.novel_dir/'series_assets/characters'/base_ids[name]/'turnaround.jpeg').is_file()) or any(
                ref.get('role') == 'character' and ref.get('name') == name
                and (ctx.novel_dir/ref['path']).is_file()
                for clip in plan['clips'] for ref in clip.get('references', []))
        retained_candidate_card = bool(matching and matching[1].get('assets')
            and name in (matching[1]['decision'].get('characters') or {}))
        if existing_card and not retained_candidate_card:
            # The requested design belongs to this existing actor. Route it to
            # a scoped phase instead of forcing another model turn for spelling.
            design = characters.pop(name)
            phases.setdefault(name, [{**design, 'label': str(row.get('reason') or 'Source-backed repaired appearance')[:180]}])
    if characters != (row.get('characters') or {}):
        row = {**row, 'characters': characters, 'phases': {'characters': phases}}
        if not characters:
            row.pop('characters')
    registered = read_json(ctx.novel_dir/'series_assets/phases.json', {}).get('characters', {})
    reused = {}
    for name, entries in phases.items():
        if name not in names:
            raise RepairArgumentError(f'input phase {name} must describe one selected character state; '
                                      f'characters selected by {cid}: {sorted(names)}')
        if name not in base_ids:
            raise RepairArgumentError(f'missing source actor {name}; use characters to request its card, '
                                      'or extras for an unpictured crowd, instead of phases for an unregistered actor')
        phase = entries[0]
        if phase.get('from', chapter) != chapter or phase.get('to', chapter) != chapter:
            raise RepairArgumentError(f'input phase scope must be the current chapter {chapter}; omit from/to or use that number')
        if asset_id := phase.get('reuse_asset_id'):
            saved = next((p for p in registered.get(name, []) if p.get('asset_id') == asset_id), None)
            card = ctx.novel_dir/'series_assets/characters'/asset_id/'turnaround.jpeg'
            if not saved or not card.is_file():
                choices = [p.get('asset_id') for p in registered.get(name, [])]
                raise RepairArgumentError(f'phases.characters.{name}.reuse_asset_id must select a registered '
                    f'state with an existing image for this character; available: {choices}. '
                    'Use resume for unfinished asset generation.')
            entries[0] = {**copy.deepcopy(saved), 'from': chapter, 'to': chapter}
            reused[name] = asset_id
    targets = {c['clip_id'] for c in plan['clips'] if c['clip_id'] == cid
               or seen.intersection(c.get('shot_indexes', []))
               or any((r.get('role') == 'character' and r.get('name') in phases)
                      or (r.get('role') == 'location' and r.get('name') in locations)
                      for r in c.get('references', []))}
    if scope is not None:
        targets &= set(scope)
    from .arguments import first_pass_scope
    first_pass_scope(ctx,targets)
    counts = history.generation_counts(directory)
    exhausted = sorted(key for key in targets if history.remaining_takes(directory,key,preparation=True)<=0)
    from .bounded_recovery import input_operation
    if exhausted and not input_operation(ctx,cid,row)['allowed']:
        raise ValueError('input repair cannot render affected clips within remaining video budget: '
                         + ', '.join(exhausted) + '. If the new state starts later, provide source-supported clip_ids for that state.')
    case = InputCompilation(ctx, row, plan, script, candidate_script, shots, patches,
        target, targets, counts, scope, phases, locations, characters, cid)
    return PreparedInput(case, submitted_row, chapter, bible, base_ids, reused)
