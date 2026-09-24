"""Scene action references shared by planning, repairs and prompt packing."""
from __future__ import annotations

from .identity import canonical_name


def normalize_extras(values):
    return list(dict.fromkeys(str(e).strip()[:80] for e in values or [] if str(e).strip()))[:3]


def normalize_actions(values, *, aliases=None, extras=()):
    return [{'actor': canonical_name(a.get('actor'), aliases, extras),
             'action': str(a.get('action') or '').strip()[:40],
             'target': canonical_name(a.get('target'), aliases, extras)}
            for a in values or [] if str(a.get('action') or '').strip()]


def action_text(actions):
    lines = []
    for action in actions:
        verb, target = action.get('action', ''), action.get('target', '')
        lines.append(f"{action.get('actor', '')}{verb}" + (target if target and not verb.endswith(target) else ''))
    return '；'.join(lines)


def action_participants(actions):
    return {a.get(field) for a in actions for field in ('actor', 'target') if a.get(field)}


def anchored_event(actions, event):
    """Add missing attribution without turning two descriptions into two physical actions."""
    line = action_text([a for a in actions if action_text([a]) not in event])
    return f'{line}。{event}' if line and event else (line or event)
