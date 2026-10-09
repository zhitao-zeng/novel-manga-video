"""Canonical source proposal effects shared by receipts and recovery permissions."""
import copy

def _phase_groups(row):
    phases = copy.deepcopy(row.get('phases') or {})
    characters = phases.get('characters') if isinstance(phases, dict) else None
    if isinstance(characters, dict):
        for name, value in characters.items():
            if isinstance(value, dict):
                characters[name] = [value]
    return phases


def effects(row):
    result = {'shots': sorted(row.get('shots') or [], key=lambda shot: shot['index']),
              'phases': _phase_groups(row), 'locations': row.get('locations') or {}}
    if row.get('characters'):
        result['characters'] = row['characters']
    if row.get('clip_ids'):
        result['clip_ids'] = sorted(row['clip_ids'])
    if isinstance(result['phases'], dict) and not any(result['phases'].values()):
        result['phases'] = {}
    if not any(result.values()):
        result['clip_id'] = row.get('clip_id')  # a wearing check without edits belongs to this clip
    return result

