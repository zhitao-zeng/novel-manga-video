"""Card review uses the selected judge, including during a staged asset update."""
from pathlib import Path

from novel_manga.application.review import judges
from novel_manga.models.bible import Character


def test_character_candidate_review_uses_the_selected_endpoint(monkeypatch):
    selected = object()
    captured = {}
    monkeypatch.setattr(judges, 'judge_settings', lambda: selected)
    monkeypatch.setattr(judges.model_client, 'image_part', lambda *a: {'type': 'image_url'})
    def reply(parts, schema, **kwargs):
        captured.update(kwargs)
        return {'matches_description': True}
    monkeypatch.setattr(judges.model_client, 'ask_json', reply)
    judges.judge_character_cards(Character(name='科查', appearance='永久毁容', wardrobe='黑袍'), [Path('candidate.jpeg')])
    assert captured['settings'] is selected and captured['name'] == 'character_card'


def test_location_candidate_review_uses_the_selected_endpoint(monkeypatch):
    selected = object()
    captured = {}
    monkeypatch.setattr(judges, 'judge_settings', lambda: selected)
    monkeypatch.setattr(judges.model_client, 'image_part', lambda *a: {'type': 'image_url'})
    monkeypatch.setattr(judges.model_client, 'ask_json', lambda *a, **k: captured.update(k) or {'has_people': False})
    judges.judge_location_card('大殿：烛火', '夜晚', Path('candidate.jpeg'))
    assert captured['settings'] is selected and captured['name'] == 'location_card'
