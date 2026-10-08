"""Scene-value semantics shared by authored drafts and scoped repairs."""
from .fields import ACTION_KEYS, TURN_KEYS, TURN_REQUIRED_TEXT


def require_text(value, field, *, empty=True):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{field} requires {'a' if empty else 'a nonempty'} string")
    return value


def require_action(value):
    if not isinstance(value, dict):
        raise ValueError('action requires an object')
    for key in ACTION_KEYS:
        require_text(value.get(key), f'action.{key}')
    return value


def action_strings(value):
    try:
        require_action(value)
    except ValueError:
        return False
    return True


def require_wears(value):
    if not isinstance(value, dict):
        raise ValueError('wears requires an object')
    for name, prop in value.items():
        require_text(name, 'wears wearer', empty=False)
        if prop is not None:
            require_text(prop, 'wears prop', empty=False)
    return value


def wears_values(value):
    try:
        require_wears(value)
    except ValueError:
        return False
    return True


def patch_turn_values(value, modes):
    return (isinstance(value, dict) and not set(value) - set(TURN_KEYS)
            and all(isinstance(value.get(k), str) for k in TURN_REQUIRED_TEXT)
            and value['delivery_mode'] in modes and bool(value['text'].strip()))


def delivery_issue(turn):
    if turn.get('inner_monologue') and turn['delivery_mode'] != 'offscreen_dialogue':
        return 'inner_monologue requires offscreen_dialogue'
    if turn['delivery_mode'] in {'silent_action', 'title_card'} and turn['speaker_name']:
        return 'silent_action/title_card have an empty speaker_name'
    return None
