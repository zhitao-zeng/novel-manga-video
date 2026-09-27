from novel_manga.models.bible import StoryBible
from novel_manga.review.policy import verify_to_verdict, fix_tier


def answer(**changes):
    return dict(people=[], same_person_twice=False, species_or_gender_wrong=False,
                action_by_wrong_person=False, actor_missing=False, lead_face_swapped=False,
                ghost_text=False, verdict='fine', evidence='', instruction='', **changes)


def test_unchecked_dimensions_do_not_become_passed():
    verdict = verify_to_verdict(answer())
    for field in ['location_ok', 'time_of_day_ok', 'chat_text_ok', 'visual_defects', 'story_ok']:
        assert verdict[field] is None
    assert verdict['severity'] == 'pass'


def test_extra_body_is_not_reported_as_wrong_action_ownership():
    raw = answer(); raw.update(same_person_twice=True, verdict='obvious', evidence='出现两个相同的人')
    verdict = verify_to_verdict(raw)
    assert verdict['identity_ok'] is False
    assert verdict['story_kind'] != '动作落在错误的人物身上'
    bible = StoryBible(novel_title='t', genre='g', visual_style='2d', palette='', style_fingerprint='t')
    assert fix_tier(verdict, bible) == 'must_fix'
    assert fix_tier({**verdict, 'scripted': {'evidence': '原文明确分身'}}, bible) == 'optional'
