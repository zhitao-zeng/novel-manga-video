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


def _action_inside(action, event: str) -> bool:
    """Whether the event's prose already carries this action: the actor followed by the verb's
    head, or the verb's distinctive run - and the action's target, when it names one.  托尼·斯塔克
    打响指 vs 打了个响指 share no prefix and no whole verb, which is why anchored_event used to
    staple the action in front of an event that already had it - and reorder the stage's own
    trigger sequence doing so.  A different target (灰色野山羊 vs 山羊) is a different action."""
    actor, verb = str(action.get('actor') or ''), str(action.get('action') or '')
    target = str(action.get('target') or '')
    if not verb:
        return True
    if target and target not in event:
        return False
    if actor and actor + verb[:2] in event:
        return True
    head = verb[:4] if len(verb) >= 4 else verb
    return bool(head) and head in event


def anchored_event(actions, event):
    """Add missing attribution without turning two descriptions into two physical actions.

    A written-back repair once turned 托尼打响指，空机甲飞入，席勒指着它说话 into
    空机甲飞入并停稳；席勒指着空机甲说话空机甲。托尼打响指… - the same snap twice, the
    arrival before its trigger - because 'already in the event' meant 'verbatim in the event'.
    The test is semantic enough to trust the event's own order: only actions the prose does
    not carry at all are spoken in front.
    """
    missing = [a for a in actions if not _action_inside(a, event)]
    line = action_text(missing)
    return f'{line}。{event}' if line and event else (line or event)
