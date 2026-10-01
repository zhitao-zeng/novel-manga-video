"""Affirmative instructions cover every H3 section while dialogue remains exact."""
from __future__ import annotations

import pytest

from novel_manga.story.h3 import compose, positive_direction_issues, request_issues, REQUEST_CONTENT_RULES


@pytest.mark.parametrize('sentence', [
    'No extra people appear.', 'The suit is not a person.', 'Ignore the model face.',
    'Avoid sparks.', 'Never move the desk.', 'A room without people.',
    "The actor doesn't speak.", 'The actor won’t turn.', 'Nobody moves.', 'None of the figures speaks.',
])
def test_negative_instruction_shape_is_rejected_with_generic_feedback(sentence):
    assert positive_direction_issues(sentence) == ['Write affirmative visible facts.']


def test_dialogue_negation_and_official_empty_markers_remain_valid():
    text = ('The unoccupied suit has a closed helmet. An off-screen voiceover accompanies the empty room.\n'
            '<d>[Chinese] 不要过来。No, I won\'t. Don\'t move.</d>\n'
            'overall_soundscape:\nNone.\nnon_diegetic_music:\nNone.')
    assert positive_direction_issues(text) == []


@pytest.mark.parametrize('family', ['2d', '3d', 'photo'])
def test_complete_six_sections_are_affirmative_and_keep_identity_voice_counts(family):
    refs = [
        {'role': 'character', 'name': '甲', 'path': 'actor.jpeg'},
        {'role': 'location', 'name': '诊室', 'path': 'room.jpeg'},
        {'role': 'prop', 'name': '银甲', 'path': 'armor.jpeg', 'wearers': ['甲']},
        {'role': 'voice', 'name': '甲', 'path': 'voice.wav'},
    ]
    words = "No, I won't. Don't move. 不要过来。"
    clip = {'request_seconds': 5, 'render_family': family, 'references': refs,
            'dialogue_bindings': [{'stage': 1, 'speaker_name': '甲', 'text': words,
                                   'delivery_mode': 'offscreen_dialogue', 'inner_monologue': True}]}
    text = compose(clip, ['<Subject 1> holds a steady posture beside the independent <Subject 3>.\n<DIALOGUE_1>'], [('', [])])
    assert positive_direction_issues(text) == [] and request_issues({**clip, 'prompt_h3': text}) == []
    assert text.count('<d>') == 1 and "<d>[Chinese] No, I won't. Don't move. 不要过来.</d>" in text
    assert '<Audio 1> is the voice-timbre reference for <Subject 1> (S1)' in text
    assert 'audience-only private thought in their own voice' in text and 'jaw stays still' in text
    assert 'When visible in a shot, exactly one <Subject 1> is present' in text
    assert 'wearer and worn garment form one body' in text
    assert '<Subject 3>: fully_preserved - the prop design' in text
    assert 'overall_soundscape:\nNone.' in text and 'non_diegetic_music:\nNone.' in text
    assert all(f'{section}:\n' in text for section in ['subject_definitions', 'summary', 'retention_analysis',
                                                     'detailed_description', 'overall_soundscape', 'non_diegetic_music'])


def test_all_sections_and_cached_final_request_use_the_shared_shape_check():
    clean = compose({'request_seconds': 4, 'references': []}, ['The door remains closed.'], [('', [])])
    for section in ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                    'overall_soundscape', 'non_diegetic_music']:
        dirty = clean.replace(section + ':\n', section + ':\nAvoid sparks. ')
        assert 'Write affirmative visible facts.' in request_issues({'prompt_h3': dirty})


def test_shared_authoring_rules_and_crowd_reference_are_affirmative():
    assert positive_direction_issues(REQUEST_CONTENT_RULES) == []
    crowd = {'request_seconds': 4, 'references': [{'role': 'character', 'name': '护卫', 'path': 'uniform.jpeg'}],
             'crowd_roles': {'护卫': {'count': 2}}}
    text = compose(crowd, ['Two supporting people stand beside the door.'], [('', [])])
    assert positive_direction_issues(text) == [] and 'for 2 distinct unnamed supporting people' in text


def test_negative_style_input_is_preserved_as_evidence_and_rejected():
    clip = {'request_seconds': 4, 'render_family': '2d', 'references': [],
            'h3_style_line': 'No 3D rendering.'}
    text = compose(clip, ['The closed door fills the frame.'], [('', [])])
    assert 'No 3D rendering.' in text  # compilation does not silently cut a supplied condition
    assert request_issues({**clip, 'prompt_h3': text}) == ['Write affirmative visible facts.']
