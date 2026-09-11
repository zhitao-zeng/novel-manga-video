import copy

import pytest

from scripts.comic_pilot import require_clean, scene_durations


def passing_source():
    reviewed = {'severity': 'pass', 'video': '/fixture/clip.mp4', 'identity_ok': True,
                'location_ok': True, 'time_of_day_ok': True, 'chat_text_ok': True,
                'text_or_watermark': False, 'visual_defects': False}
    return ({'flags': [], 'clips': {'clip_01': reviewed}},
            {'assembly': {'thin_passed': True}, 'failed_clips': [], 'gate_failed_clips': [],
             'clips': [{'clip_id': 'clip_01', 'selected': {'video': '/fixture/clip.mp4', 'passed': True}}]})


def test_accepts_a_fully_reviewed_issue_free_source():
    require_clean(*passing_source())


@pytest.mark.parametrize('problem', ['flag', 'minor', 'stale_review', 'speech'])
def test_refuses_problematic_or_stale_source(problem):
    review, media = passing_source()
    if problem == 'flag':
        review['flags'] = ['identity issue']
    elif problem == 'minor':
        review['clips']['clip_01']['severity'] = 'minor'
    elif problem == 'stale_review':
        review['clips']['clip_01']['video'] = '/fixture/old.mp4'
    else:
        media['gate_failed_clips'] = ['clip_01']
    with pytest.raises(ValueError):
        require_clean(review, media)


def test_timeline_matches_reference_and_does_not_clip_speech():
    manifest = {'source_duration_seconds': 20.013, 'scenes': [{'id': 1, 'video_span': 12}, {'id': 2, 'video_span': 8}]}
    audio = {1: {'audio_seconds': 8}, 2: {'audio_seconds': 4}}
    result = scene_durations(manifest, audio)
    assert sum(round(v*25) for v in result.values()) == round(20.013*25)
    assert all(result[i] >= audio[i]['audio_seconds']+.65 for i in audio)
    longer = copy.deepcopy(audio)
    longer[1]['audio_seconds'] = 25
    result = scene_durations(manifest, longer)
    assert result[1] >= 25.65
    assert sum(result.values()) > manifest['source_duration_seconds']
