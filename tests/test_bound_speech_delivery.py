import pytest
from novel_manga.story.h3 import final_dialogue_issues


def request(phrase, *, words='我在门外。', stage=1):
    return {'references': [], 'dialogue_bindings': [
        {'stage': 1, 'speaker_name': '乙', 'text': '我在门外。', 'delivery_mode': 'offscreen_dialogue'}],
        'prompt_h3': f'detailed_description:\n[Shot {stage}] {phrase} <d>[Chinese] {words}</d>\noverall_soundscape:\nroom tone'}


@pytest.mark.parametrize('phrase', [
    'An off-screen voice (S1) says, in order,',
    'The off-screen voice (S1) delivers the existing line',
    'The off-screen voice (S1) delivers the existing line in a quiet tone,',
])
def test_explicit_source_owner_survives_actual_delivery_wording(phrase):
    assert final_dialogue_issues(request(phrase)) == []


@pytest.mark.parametrize('clip', [
    request('A voice says,'),
    request('The off-screen voice S1 delivers the existing line'),
    request('The off-screen voice (S1) delivers the existing line', words='换了一句话。'),
    request('The off-screen voice (S1) delivers the existing line', stage=2),
    request('<Subject 1> (S1) says'),
    request('An off-screen voice (S1) says. Another person answers'),
])
def test_delivery_grammar_does_not_accept_missing_owner_or_changed_source(clip):
    assert final_dialogue_issues(clip)
