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
        # The target may already sit inside the verb phrase (指着空机甲说话 has 空机甲 mid-verb);
        # appending it again produced 指着空机甲说话空机甲 in a written-back repair.  A target
        # counts as expressed when it appears anywhere in the phrase, not only at its end.
        append = target and target not in verb
        lines.append(f"{action.get('actor', '')}{verb}" + (target if append else ''))
    return '；'.join(lines)


def action_participants(actions):
    return {a.get(field) for a in actions for field in ('actor', 'target') if a.get(field)}


def anchored_event(actions, event):
    """Use the authored event, without synthesizing another action before it.

    Only older action-only records need the display fallback. A conflict between
    structured attribution and prose belongs to semantic review, not string matching.
    """
    return event or action_text(actions)
