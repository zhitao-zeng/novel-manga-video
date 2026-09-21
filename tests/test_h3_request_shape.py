"""What the H3 request says about the medium, the people in it and how their lines are spoken.

Each case here is a field that crossed a module boundary and arrived meaning something else: a style
package's name read as a rendering medium, a second reference photo read as a second person, and a
line's vocal delivery read as a facial expression.
"""
from __future__ import annotations

import pytest

from novel_manga.application.profiles import h3_compile_inputs, h3_prompt_outdated, h3_source_digest
from novel_manga.story.dialogue import character_pictures, subject_map
from novel_manga.story.h3 import compose, render_family, request_issues, stages_of, subject_lines

PROMPT = ('【阶段1】梁舟站在门边。声音：中文普通话，压低声音且迟疑，梁舟开口说：{谁在那里？}'
          '结束时：门打开。画面呈现')


def scene_clip(**extra) -> dict:
    return {'request_seconds': 10, 'scene_ids': ['s1'], 'shot_timing': [{'seconds': 10, 'cut': ''}],
            'references': [{'role': 'character', 'name': '梁舟', 'path': 'series_assets/characters/c1/turnaround.jpeg'}],
            **extra}


# --- the medium -------------------------------------------------------------------------------

@pytest.mark.parametrize('family, expected', [
    ('3d', '3D animation'), ('2d', '2D animation'), ('photo', 'photographed live action')])
def test_every_render_family_reaches_the_request_as_its_own_medium(family, expected):
    clip = scene_clip(render_family=family)
    request = compose(clip, ['<Subject 1> waits by a door.'], stages_of(PROMPT))
    assert expected in request
    assert ('live-action short-drama' in request) == (family == 'photo')


@pytest.mark.parametrize('legacy', ['weimei', '3d-guoman', 'live', 'meiman'])
def test_a_style_package_name_is_never_read_as_a_medium(legacy):
    """These are package names, not media.  Compared against the literal "3d" they all fell to 2D, so a
    book drawn in 唯美 (render_family 3d) or photographed in 真人 asked H3 for 2D animation while its
    own reference cards were made in something else.  Without a family we say nothing at all."""
    clip = scene_clip(animation_style=legacy)
    assert render_family(clip) == ''
    request = compose(clip, ['<Subject 1> waits by a door.'], stages_of(PROMPT))
    assert '2D animation' not in request and '3D animation' not in request


def test_the_two_legacy_values_still_mean_what_they_always_meant():
    assert render_family({'animation_style': '3d'}) == '3d'
    assert render_family({'animation_style': '2d'}) == '2d'


def test_the_stamp_covers_the_medium_but_only_where_the_compiler_reads_it():
    """Correcting the medium has to recompile the clips that carry the sentence - and must NOT mark the
    finished books stale, because they never had one and re-rendering them would buy nothing."""
    scene = scene_clip(render_family='2d', prompt=PROMPT, prompt_h3='english')
    scene['prompt_h3_of'] = h3_source_digest(PROMPT, '', None, h3_compile_inputs(scene))
    assert not h3_prompt_outdated(scene)
    assert h3_prompt_outdated({**scene, 'render_family': '3d'})

    old = {'prompt': PROMPT, 'prompt_h3': 'english', 'animation_style': '3d'}
    old['prompt_h3_of'] = h3_source_digest(PROMPT)
    assert h3_compile_inputs(old) == {}
    assert not h3_prompt_outdated(old)


# --- one person, two pictures ------------------------------------------------------------------

def two_view_clip() -> dict:
    return scene_clip(references=[
        {'role': 'character', 'name': '梁舟', 'path': 'series_assets/characters/c1/turnaround.jpeg'},
        {'role': 'character', 'name': '梁舟', 'path': 'series_assets/characters/c1/expressions.jpeg'},
        {'role': 'character', 'name': '阿岚', 'path': 'series_assets/characters/c2/turnaround.jpeg'},
        {'role': 'location', 'name': '门廊', 'path': 'series_assets/locations/l1/establishing.jpeg'}])


def test_two_views_of_one_actor_are_one_subject_with_two_pictures():
    clip = two_view_clip()
    assert subject_map(clip) == {'梁舟': 1, '阿岚': 2}
    assert [n for n, _ in character_pictures(clip)['梁舟']] == [1, 2]
    defs, _ = subject_lines(clip)
    assert defs[0].startswith('<Subject 1> is the person shown in <Picture 1> and <Picture 2>.')
    # each picture keeps its own job: the bust stops at the collar and cannot answer for a costume
    assert 'face, hair, age and skin tone from <Picture 2>' in defs[0]
    assert 'body proportions, garment cut, main colours and accessories from <Picture 1>' in defs[0]
    assert sum(d.startswith('<Subject') for d in defs) == 2  # two people, not three


def test_a_second_photo_declared_as_a_second_person_is_reported():
    clip = two_view_clip()
    clip['prompt_h3'] = ('subject_definitions:\n'
                         '<Subject 1> is the person shown in <Picture 1>.\n'
                         '<Subject 2> is the person shown in <Picture 2>.\n'
                         '<Subject 3> is the person shown in <Picture 3>.\n\n'
                         'summary:\nx\n\ndetailed_description:\n[Shot 1] <Subject 1> waits.')
    assert any('reference images were declared as separate people' in issue
               for issue in request_issues(clip))


def test_a_single_view_clip_is_worded_exactly_as_before():
    clip = scene_clip(render_family='2d')
    defs, subjects = subject_lines(clip)
    assert subjects == {'梁舟': 1}
    assert defs[0].startswith('<Subject 1> is the person shown in <Picture 1>.')
    assert not request_issues({**clip, 'prompt_h3': compose(clip, ['<Subject 1> waits.'], stages_of(PROMPT))})


# --- how the line is spoken ---------------------------------------------------------------------

def test_the_vocal_manner_survives_the_stage_parse():
    (_, turns), = stages_of(PROMPT)
    assert turns == [('梁舟', '谁在那里？', False, '压低声音且迟疑')]


def test_two_performances_of_one_line_no_longer_compile_to_the_same_request():
    """Held apart only by the delivery, these used to be byte-identical: the manner was captured and
    dropped, and the one route left to it was the shot translation, which is asked for what the camera
    SEES - it turned 恐惧与无奈 into "with a determined expression", a face, with the valence reversed."""
    loud = PROMPT.replace('压低声音且迟疑', '高声愤怒')
    clip, english = scene_clip(render_family='2d'), ['<Subject 1> waits by a door.']
    delivery = {'压低声音且迟疑': 'The line is delivered in a low, hesitant voice.',
                '高声愤怒': 'The line is delivered as a furious shout.'}
    quiet_request = compose(clip, english, stages_of(PROMPT), '', delivery)
    loud_request = compose(clip, english, stages_of(loud), '', delivery)
    assert quiet_request != loud_request
    assert 'low, hesitant voice' in quiet_request and 'furious shout' in loud_request
    # the words themselves are still the packed ones, and the checker still recognises the syntax
    assert '<Subject 1> (S1) says <d>[Chinese] 谁在那里？</d>' in quiet_request
    assert not request_issues({**clip, 'prompt_h3': quiet_request})


def test_the_manner_travels_with_the_binding_not_with_the_prose():
    clip = scene_clip(render_family='2d', dialogue_bindings=[
        {'stage': 1, 'speaker_name': '梁舟', 'delivery_mode': 'visible_dialogue',
         'text': '谁在那里？', 'emotion': '压低声音且迟疑'}])
    request = compose(clip, ['<Subject 1> waits by a door.'], stages_of(PROMPT), '',
                      {'压低声音且迟疑': 'The line is delivered in a low, hesitant voice.'})
    assert 'low, hesitant voice' in request
    assert not request_issues({**clip, 'prompt_h3': request})


def test_an_untranslated_manner_costs_the_line_its_performance_and_nothing_else():
    clip = scene_clip(render_family='2d')
    request = compose(clip, ['<Subject 1> waits by a door.'], stages_of(PROMPT), '', {})
    assert '<Subject 1> (S1) says <d>[Chinese] 谁在那里？</d>' in request
    assert not request_issues({**clip, 'prompt_h3': request})
