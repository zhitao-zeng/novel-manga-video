"""Repeated source mentions require a semantic decision; names alone do not merge bodies."""
from novel_manga.application.identity.flow import reconcile_repeated_actors
from novel_manga.llm import client


def actor(sid, name, presence, paragraph):
    return {'source_id': sid, 'name': name, 'presence': presence, 'kind': 'individual', 'count': 1,
            'paragraphs': [paragraph], 'appearance': '',
            'forms': [{'form': name, 'kind': 'proper', 'paragraphs': [paragraph]}]}


def test_mention_and_later_appearance_merge_only_after_source_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: {'groups': [
        {'name': '黑胡子', 'same_person': True, 'paragraphs': [1, 2], 'reason': '前面提及，随后出场'}]})
    result = reconcile_repeated_actors(tmp_path, {'actors': [actor(1, '黑胡子', 'mentioned', 1),
        actor(2, '黑胡子', 'on_stage', 2)]}, [{'text': '谈到黑胡子。'}, {'text': '随后黑胡子走了进来。'}])
    assert len(result['actors']) == 1
    assert result['actors'][0]['presence'] == 'on_stage'
    assert result['actors'][0]['forms'][0]['paragraphs'] == [1, 2]
    assert (tmp_path / 'identity_repeated_actors.json').exists()


def test_simultaneous_bodies_keep_their_source_ids_even_with_the_same_name(tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: {'groups': [
        {'name': '沈玄川', 'same_person': False, 'paragraphs': [1], 'reason': '本体和分身各自行动'}]})
    actors = [actor(1, '沈玄川', 'on_stage', 1), actor(2, '沈玄川', 'on_stage', 1)]
    result = reconcile_repeated_actors(tmp_path, {'actors': actors}, [
        {'text': '沈玄川本体挡在门口，沈玄川分身走向窗边。'}])
    assert result['actors'] == actors
