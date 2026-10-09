"""The existing scoped input-repair argument contract."""
from __future__ import annotations
import copy
from .arguments import RepairArgumentError, validate_resume
from novel_manga.story.fields import ACTION_KEYS
from novel_manga.story.field_validation import action_strings, wears_values, patch_turn_values, delivery_issue
from .input_records import _phase_groups
from novel_manga.application.identity.phases import LOOK_FIELDS

SHOT_NAMES = {'in_frame', 'extras', 'listeners'}
SHOT_FIELDS = {'location', 'light', 'scene_id', 'scene_time', 'scene_transition',
               'wears', 'props', 'scene_objects', 'visual_prompt', 'motion_prompt',
               'start_state', 'event', 'end_state', 'sfx', 'actions', 'speaker_map', 'turn_texts', 'turns', *SHOT_NAMES}



def expand_shared_scope(row, plan):
    scope = row.get('clip_ids')
    if scope is None:
        return row
    addresses = {i for c in plan['clips'] if c['clip_id'] in scope for i in c.get('shot_indexes', [])}
    touched = {p['index'] for p in row.get('shots') or []} & addresses
    shared = [c['clip_id'] for c in plan['clips'] if touched.intersection(c.get('shot_indexes', []))]
    return {**row, 'clip_ids': list(dict.fromkeys([*scope, *shared]))}


def normalize_arguments(row):
    """Reject malformed interface values before receipts, models or asset work."""
    validate_resume(row)
    allowed = {'clip_id','action','reason','source_quote','clip_ids','shots','phases','locations','characters','resume'}
    unknown = set(row)-allowed
    if unknown:
        raise RepairArgumentError(f'unsupported input fields: {sorted(unknown)}; '
                                  'use shots, characters, phases.characters and locations for adopted input repairs')
    row = copy.deepcopy(row)
    scope = row.get('clip_ids')
    if scope is not None and (not isinstance(scope, list) or not scope
            or any(not isinstance(key, str) or not key.strip() for key in scope)):
        raise RepairArgumentError('clip_ids must be a nonempty list of clip ID strings, including the target clip')
    patches = row.get('shots')
    if patches is not None and not isinstance(patches, list):
        raise RepairArgumentError('shots must be a list of patch objects with index and supported source fields')
    for number, patch in enumerate(patches or []):
        path = f'shots[{number}]'
        if not isinstance(patch, dict) or type(patch.get('index')) is not int:
            raise RepairArgumentError(f'{path} must be an object with an integer index')
        if extra := set(patch)-{'index'}-SHOT_FIELDS:
            raise RepairArgumentError(f'{path} unsupported fields {sorted(extra)}; allowed: {sorted(SHOT_FIELDS)}')
        for field, value in patch.items():
            if field == 'turns':
                from novel_manga.planning.constants import DELIVERY_MODES
                if not isinstance(value,list):
                    raise RepairArgumentError(f'{path}.turns must be an ordered list of source-correct turns')
                for turn in value:
                    if not patch_turn_values(turn, DELIVERY_MODES):
                        raise RepairArgumentError(f'{path}.turns needs speaker_name, delivery_mode and nonempty text')
                    if issue := delivery_issue(turn):
                        raise RepairArgumentError(issue)
            elif field == 'turn_texts':
                if not isinstance(value, list) or not value or any(not isinstance(t, str) or not t.strip() for t in value):
                    raise RepairArgumentError(f'{path}.turn_texts must list each shortened complete spoken turn in original order')
            elif field == 'speaker_map':
                if not isinstance(value, dict) or not value or any(
                        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                        for k, v in value.items()):
                    raise RepairArgumentError(f'{path}.speaker_map must map existing speech owners to source-correct names')
            elif field == 'actions':
                if not isinstance(value, list) or any(not action_strings(v) or set(v) != set(ACTION_KEYS) for v in value):
                    raise RepairArgumentError(f'{path}.actions must contain actor/action/target text objects')
            elif field in SHOT_NAMES:
                if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                    raise RepairArgumentError(f'{path}.{field} must be a list of nonempty names')
            elif field == 'wears':
                if value is not None and not wears_values(value):
                    raise RepairArgumentError(f'{path}.wears must map character names to worn prop names, '
                        'for example {"Character":"Armor"}; use {} or null to clear. '
                        'Put ordinary clothing in phases.characters.<name>.base_costume and body traits in appearance.')
            elif field in {'props','scene_objects'}:
                if value is not None and (not isinstance(value,list) or any(not isinstance(v,str) for v in value)):
                    raise RepairArgumentError(f'{path}.{field} must be a list of names; use [] or null to clear')
            elif field != 'index' and not isinstance(value, str):
                raise RepairArgumentError(f'{path}.{field} must be text; use an empty string to clear it')
    phases = row.get('phases')
    if phases is not None:
        if not isinstance(phases,dict) or set(phases)-{'characters'} or not isinstance(phases.get('characters',{}),dict):
            raise RepairArgumentError('phases must contain a characters object; put scene descriptions in locations')
        row['phases'] = _phase_groups(row)
        for name, entries in row['phases'].get('characters',{}).items():
            if not isinstance(entries,list) or len(entries)!=1 or not isinstance(entries[0],dict):
                raise RepairArgumentError(f'phases.characters.{name} must be one phase object or a one-item list of phase objects')
            phase = entries[0]
            if extra := set(phase)-set(LOOK_FIELDS)-{'from','to','asset_id','label','wears','closed','reuse_asset_id'}:
                raise RepairArgumentError(f'phases.characters.{name} unsupported appearance fields: {sorted(extra)}')
            if 'reuse_asset_id' in phase:
                if (not isinstance(phase['reuse_asset_id'], str) or not phase['reuse_asset_id'].strip()
                        or set(phase)-{'reuse_asset_id','from','to'}):
                    raise RepairArgumentError(f'phases.characters.{name}.reuse_asset_id must select an existing '
                        'state without new appearance/clothing fields; reuse its saved design or describe a new state')
            if phase.get('wears') is not None and not isinstance(phase['wears'],str):
                raise RepairArgumentError(f'phases.characters.{name}.wears must be a registered worn prop name')
    locations = row.get('locations')
    if locations is not None and (not isinstance(locations,dict) or any(
            not isinstance(name,str) or not (isinstance(value,str) or
                isinstance(value,dict) and set(value)=={'reuse_asset_id'} and
                isinstance(value['reuse_asset_id'],str) and value['reuse_asset_id'].strip())
            for name,value in locations.items())):
        raise RepairArgumentError('locations must map selected location names to empty-setting descriptions')
    if 'characters' in row:
        from novel_manga.models.bible import Character
        characters = row['characters']
        if not isinstance(characters, dict) or not characters:
            raise RepairArgumentError('characters must map missing source actors to appearance/wardrobe designs')
        for name, design in characters.items():
            if (not isinstance(name, str) or not name.strip() or not isinstance(design, dict)
                    or set(design)-{'gender', 'age', 'appearance', 'wardrobe'}):
                raise RepairArgumentError('characters must describe named source actors using gender, age, appearance, wardrobe')
            try:
                Character(name=name, **design)
            except ValueError as error:
                raise RepairArgumentError(f'characters.{name}: {error}') from error
        if set(characters) & set((row.get('phases') or {}).get('characters', {})):
            raise RepairArgumentError('request a missing character with its intended design, or an existing phase, not both')
    return row
