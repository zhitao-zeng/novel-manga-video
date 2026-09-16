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
    return '；'.join(f"{a.get('actor', '')}{a.get('action', '')}{a.get('target', '')}" for a in actions)


def action_participants(actions):
    return {a.get(field) for a in actions for field in ('actor', 'target') if a.get(field)}
