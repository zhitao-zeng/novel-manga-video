"""The status payload must expose source-family accounting, not only the view limit."""
from novel_manga.application.repair import history


def test_extra_status_exposes_source_limit_used_and_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'conditional_extra', lambda d, c: {
        'limit': 3, 'source_limit': 5, 'scope_roots': ['clip_08'],
        'authorized_additional_takes': 2, 'prepared': True, 'activated': True})
    monkeypatch.setattr(history, 'source_budget', lambda d, c, **k: {
        'roots': ['clip_08'], 'constraints': {'clip_08': {'used': 4, 'pending': 1, 'remaining': 0}},
        'remaining': 0})
    monkeypatch.setattr(history, 'generation_counts', lambda d: {'clip_08': 3})
    row = history.extra_status(tmp_path, 'clip_08')
    assert row['limit'] == 3 and row['remaining'] == 0
    assert row['source_limit'] == 5
    assert row['source_used'] == 4 and row['source_pending'] == 1 and row['source_remaining'] == 0
    assert row['authorized_additional_takes'] == 2


def test_extra_status_keeps_clip_level_grants_simple(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'conditional_extra', lambda d, c: {
        'limit': 4, 'prepared': False, 'activated': False})
    monkeypatch.setattr(history, 'generation_counts', lambda d: {'clip_17': 3})
    row = history.extra_status(tmp_path, 'clip_17')
    assert row == {'limit': 4, 'remaining': 1, 'authorized_additional_takes': None,
                   'prepared': False, 'activated': False}
