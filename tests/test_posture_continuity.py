"""The real regression is a wrong inherited state reaching a request, not just a ledger file existing."""
import copy
import json

import pytest

from novel_manga.application.packing import posture
from novel_manga.application.planning.posture import check_draft
from novel_manga.planning.decisions import patch_targets
from novel_manga.story.posture import phrase
from support.posture import person, answer


def shot(index, text, *, people=('席勒',), **extra):
    return {'index': index, 'label': f'clip_01 stage {index}', 'location': '诊所', 'scene_id': 'clinic',
            'in_frame': list(people), 'visual_prompt': text, 'motion_prompt': '', 'end_state': '', **extra}


def fill(tmp_path, shots, answers, **kwargs):
    return posture.fill(tmp_path, script={'shots': shots}, segments=[],
                        ask=lambda prompt, schema: {'stages': copy.deepcopy(answers)}, **kwargs)


@pytest.mark.parametrize('opening', ['第二天早晨，席勒接过报告', '回忆里，席勒接过报告', '回到现实，席勒接过报告'])
def test_same_place_and_scene_id_do_not_carry_across_a_time_cut(tmp_path, opening):
    shots = [shot(1, '深夜，席勒坐在椅子上'), shot(2, opening)]
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', where='椅子上', entry='reset'),
                                   answer(2, boundary='reset', entry='reset')])
    assert states['stages']['2']['start']['席勒']['posture'] is None
    assert states['stages']['2']['start']['席勒']['where'] == ''
    assert not states['issues']


def test_reverse_angle_and_cutaway_preserve_continuous_pose_without_adding_people(tmp_path):
    shots = [shot(1, '席勒坐在桌后'), shot(2, '杯子的特写', people=()),
             shot(3, '反打，席勒点头', camera='桌子另一侧')]
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', entry='reset', where='桌后椅子上'),
                                   answer(2, persons=[]), answer(3)])
    parsed = posture.attach(shots, {'shots': shots}, states)
    assert phrase(parsed[0]) == ''  # already authored; no second instruction
    assert phrase(parsed[1]) == '' and parsed[1]['in_frame'] == []
    assert phrase(parsed[2]) == '席勒坐着（桌后椅子上）'
    assert states['stages']['3']['start']['席勒']['inherited_from'] == 1


def test_person_reentry_cannot_reuse_the_seat_left_behind(tmp_path):
    shots = [shot(1, '席勒坐在椅子上'), shot(2, '托尼站着看向门口', people=('托尼',)),
             shot(3, '席勒重新走进诊室')]
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', entry='reset', where='椅子上'),
                                   answer(2, persons=[person('站', name='托尼', entry='reset')]),
                                   answer(3, entry='reset')])
    assert states['stages']['3']['start']['席勒']['posture'] is None
    assert not states['stages']['3']['start']['席勒']['where']


@pytest.mark.parametrize('boundary,entry', [('unknown', 'continue'), ('continuous', 'unknown')])
def test_ambiguity_returns_to_existing_patch_loop_without_rewriting_or_publishing(tmp_path, boundary, entry):
    shots = [shot(1, '席勒坐在椅子上'), shot(2, '席勒点头')]
    original = copy.deepcopy(shots)
    states, issues = check_draft(tmp_path, shots, [], ask=lambda p, s: {'stages': [
        answer(1, '坐', boundary='reset', entry='reset'), answer(2, boundary=boundary, entry=entry)]})
    missing, faulty = patch_targets(issues)
    assert not missing and set(faulty) == {'clip_01 stage 2'}
    assert shots == original and not (tmp_path / posture.FILE).exists()
    with pytest.raises(ValueError, match='姿态交接需修正'):
        posture.attach(shots, {'shots': shots}, states)


def test_another_person_moving_cannot_excuse_a_posture_jump(tmp_path):
    shots = [shot(1, '席勒坐在桌后'), shot(2, '席勒站在桌前，托尼坐着，随后托尼站起', people=('席勒', '托尼'))]
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', entry='reset'),
        answer(2, persons=[person('站'), person('坐', name='托尼', entry='reset', end='站', transition='托尼站起')])])
    assert len(states['issues']) == 1 and '席勒' in states['issues'][0]['detail']


def test_explicit_transition_updates_end_but_does_not_replay_opening_in_later_split(tmp_path):
    shots = [shot(1, '席勒坐在桌后'), shot(2, '席勒举起文件', motion_prompt='席勒站起')]
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', entry='reset'),
                                   answer(2, end='站', transition='席勒站起')])
    assert not states['issues']
    parsed = posture.attach(shots, {'shots': shots}, states)
    assert phrase(parsed[1]) == '席勒坐着'
    assert phrase({**parsed[1], 'split_part': [2, 2]}) == ''
    assert states['stages']['2']['end']['席勒']['where'] == ''


def test_cached_reading_tracks_time_source_and_upstream_state_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(posture, 'BATCH', 1)
    shots = [shot(1, '席勒坐在椅子上', segment_id='seg_1'), shot(2, '席勒点头', segment_id='seg_2')]
    segments = [{'segment_id': 'seg_1', 'text': '席勒坐下。'}, {'segment_id': 'seg_2', 'text': '他随即点头。'}]
    calls = []
    def ask(prompt, schema):
        inputs = json.loads(prompt.split('\n')[-1])
        calls.append(inputs)
        s = inputs['shots'][0]
        return {'stages': [answer(s['index'], '坐' if s['index'] == 1 else '未写明',
                                  boundary='reset' if s['index'] == 1 else 'continuous',
                                  entry='reset' if s['index'] == 1 else 'continue')]}
    first = posture.fill(tmp_path, script={'shots': shots}, segments=segments, ask=ask)
    calls.clear()
    assert posture.fill(tmp_path, script={'shots': shots}, segments=segments, ask=ask) == first
    assert not calls
    shots[0]['scene_time'] = '昨夜'
    posture.fill(tmp_path, script={'shots': shots}, segments=segments, ask=ask, write=False)
    assert len(calls) == 2  # changed adjacent scene context is sent too
    shots[0].pop('scene_time')
    calls.clear()
    segments[1]['text'] = '次日他点头。'
    posture.fill(tmp_path, script={'shots': shots}, segments=segments, ask=ask, write=False)
    assert len(calls) == 1 and calls[0]['shots'][0]['source'][0]['text'] == '次日他点头。'


def test_changing_upstream_shot_invalidates_inherited_state_even_if_consumer_is_unchanged(tmp_path):
    shots = [shot(1, '席勒坐在椅子上'), shot(2, '席勒点头')]
    before = copy.deepcopy(shots)
    states = fill(tmp_path, shots, [answer(1, '坐', boundary='reset', entry='reset'), answer(2)])
    shots[0]['visual_prompt'] = '席勒站在窗前'
    with pytest.raises(ValueError, match='已过期'):
        posture.attach(shots, {'shots': shots}, states)
    assert states['inputs'] == posture.material({'shots': before})


def test_changed_posture_affects_only_continuous_dependent_shots(tmp_path):
    shots = [shot(1, '席勒坐在桌后'), shot(2, '席勒点头'), shot(3, '次日席勒站着看报告'), shot(4, '席勒点头')]
    answers = [answer(1, '坐', boundary='reset', entry='reset'), answer(2),
               answer(3, '站', boundary='reset', entry='reset'), answer(4)]
    before = fill(tmp_path, shots, answers, write=False)
    answers[0]['people'][0]['start']['posture'] = '站'
    answers[0]['people'][0]['start']['quote'] = '站'
    shots[0]['visual_prompt'] = '席勒站在桌后'
    after = fill(tmp_path, shots, answers, write=False)
    assert posture.changed_stages(before, after) == {2}


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'invented_transition'])
def test_incomplete_or_invented_evidence_does_not_overwrite_saved_ledger(tmp_path, bad):
    shots = [shot(1, '席勒坐在椅子上')]
    answers = [answer(1, '坐', boundary='reset', entry='reset')]
    fill(tmp_path, shots, answers)
    old = (tmp_path / posture.FILE).read_bytes()
    if bad == 'missing':
        answers = []
    elif bad == 'duplicate':
        answers *= 2
    else:
        shots[0]['motion_prompt'] = '席勒站起'
        answers[0]['people'][0]['end'] = {'posture': '站', 'where': '', 'quote': '站起'}
        answers[0]['people'][0]['transition'] = '席勒跳起来'
    with pytest.raises(ValueError):
        fill(tmp_path, shots, answers, previous={})
    assert (tmp_path / posture.FILE).read_bytes() == old


def test_reader_corrects_all_wrong_quotes_together_then_marks_the_states_as_inherited(tmp_path):
    shots = [shot(1, '席勒坐在椅子上'), shot(2, '席勒举起一根手指'), shot(3, '席勒点头')]
    good = [answer(1, '坐', boundary='reset', entry='reset'), answer(2), answer(3)]
    wrong = copy.deepcopy(good)
    for row in wrong[1:]:
        row['people'][0]['start'] = {'posture': '坐', 'where': '', 'quote': '坐在椅子上'}
    prompts = []
    def ask(prompt, schema):
        prompts.append(prompt)
        return {'stages': wrong if len(prompts) == 1 else good}
    states = posture.fill(tmp_path, script={'shots': shots}, segments=[], ask=ask, write=False)
    assert len(prompts) == 2
    assert '镜2 席勒的quote' in prompts[1] and '镜3 席勒的quote' in prompts[1]
    assert all(states['stages'][str(i)]['start']['席勒']['stated'] is False for i in (2, 3))
