"""Two persisted activity allowances, using the host's existing active deadline."""
from copy import deepcopy


def load(saved, *, preparation_seconds, production_seconds, media_exists=False):
    if saved:
        # A legacy single clock does not establish when preparation ended.
        # Retain its remaining allowance until actual evidence is reconciled.
        return deepcopy(saved.get('phase_budget'))
    return {'phase': 'production' if media_exists else 'preparation',
            'preparation': {'limit_seconds': preparation_seconds, 'used_seconds': 0},
            'production': {'limit_seconds': production_seconds, 'used_seconds': 0}}


def account(budget, remaining):
    if budget is not None:
        row = budget[budget['phase']]
        row['used_seconds'] = max(row['used_seconds'], row['limit_seconds']-remaining)


def begin_generation(budget, remaining):
    if budget is None or budget['phase'] == 'production':
        return None
    account(budget, remaining)
    if remaining <= 0:
        raise TimeoutError('episode preparation timeout exhausted')
    budget['phase'] = 'production'
    production = budget['production']
    return max(0, production['limit_seconds']-production['used_seconds'])
