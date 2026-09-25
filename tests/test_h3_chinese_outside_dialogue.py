"""Chinese outside the bound dialogue is a request problem for every clip - H3 reads it aloud - and one that
translating again fixes: it never routes a clip to re-planning or draws the identity correction.

美漫 ch12 clip_20 (2026-09-25) carried 48 characters of the naming table's traits after a subject tag and passed
as "ok", because the check only ran for authored scenes."""
import json

import novel_manga.application.repair.managed as managed
from novel_manga.story.h3 import CHINESE_OUTSIDE_DIALOGUE, correction, identity_issues, request_issues
from support.managed_episode import fixture_episode

# How every translation made before 2026-09-17 declares its subjects: by their Chinese names.
LEGACY = ('subject_definitions:\n<Subject 1> is the character 莱恩·格雷, shown in <Picture 1>. '
          'Take only the face, hair, build and clothing from <Picture 1>.\n\nsummary:\nA scene.\n\n'
          'detailed_description:\n[Shot 1] <Subject 1> waits by the door.\n')
CURRENT = ('subject_definitions:\n<Subject 1> is the person shown in <Picture 1>. '
           'Take only the face, hair, build and clothing from <Picture 1>.\n\nsummary:\nA scene.\n\n'
           'detailed_description:\n[Shot 1] <Subject 1> waits by the door.\n'
           '<Subject 1> (S1) says <d>[Chinese] 谁在那里？</d>\n')
REFS = [{'role': 'character', 'name': '莱恩·格雷', 'path': 'c1.jpeg'}]


def test_chinese_outside_the_dialogue_is_reported_for_a_planned_clip():
    assert CHINESE_OUTSIDE_DIALOGUE in request_issues({'prompt_h3': LEGACY, 'references': REFS})


def test_chinese_inside_the_dialogue_is_not():
    assert request_issues({'prompt_h3': CURRENT, 'references': REFS}) == []


def test_it_is_not_an_identity_problem_and_draws_no_identity_correction():
    clip = {'prompt_h3': LEGACY, 'references': REFS}
    assert identity_issues(clip) == [] and correction(clip) == ''


def test_a_clip_whose_only_problem_is_chinese_is_not_sent_to_repair(tmp_path):
    d, clips, reviews = fixture_episode(tmp_path)
    clips[0].update(prompt_h3=LEGACY, references=REFS)
    (d / 'clip_plan.json').write_text(json.dumps({'clips': clips}))
    review = json.loads((d / 'episode_review.json').read_text())
    review['feedback'] = {'b': 'wrong'}
    (d / 'episode_review.json').write_text(json.dumps(review))
    assert managed.candidates(d)[0] == ['b']
