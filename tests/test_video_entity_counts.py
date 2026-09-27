import pytest
from novel_manga.review.policy import verify_to_verdict, count_mismatches, fix_tier
from novel_manga.models.bible import StoryBible


def counted(entity, expected, actual):
    return {'entity': entity, 'frames': '3–5', 'expected_min': expected, 'expected_max': expected,
            'observed': actual, 'reason': '按本镜动作与当前帧核对'}


def test_count_mismatch_is_a_failure_even_when_the_summary_says_fine():
    raw = {'people': [], 'verdict': 'fine', 'evidence': '', 'instruction': '托尼只有一个穿甲身体。',
           'count_checks': [counted('人物', 2, 3), counted('独立空甲', 0, 1)]}
    result = verify_to_verdict(raw)
    assert result['severity'] == 'fail' and result['verify']['verdict'] == 'obvious'
    assert '实际3' in result['defect_issue']
    bible = StoryBible(novel_title='n', genre='g', visual_style='2d', palette='', style_fingerprint='n')
    assert fix_tier(result, bible) == 'must_fix'


def test_wearer_plus_one_summoned_empty_suit_is_legal():
    raw = {'people': [], 'verdict': 'fine', 'count_checks': [
        counted('人物', 2, 2), counted('穿戴装甲', 1, 1), counted('召来后的独立空甲', 1, 1)]}
    assert not count_mismatches(raw)
    assert verify_to_verdict(raw)['severity'] == 'pass'


def test_closeup_range_does_not_force_offscreen_people_into_frame():
    row = counted('本镜人物', 0, 1); row['expected_max'] = 2
    assert not count_mismatches({'count_checks': [row]})
    row['expected_min'] = 3
    with pytest.raises(ValueError, match='inverted'):
        count_mismatches({'count_checks': [row]})


def test_count_mismatch_cannot_be_excused_as_a_scripted_suit():
    from novel_manga.review.policy import fix_tier
    from novel_manga.models.bible import StoryBible
    verdict = {'scripted': True, 'verify': {'count_checks': [
        {'entity': '无人空甲', 'frames': '1', 'expected_min': 1, 'expected_max': 1, 'observed': 2, 'reason': '多出一台'}]}}
    assert fix_tier(verdict, StoryBible(novel_title='t', genre='g', visual_style='v', palette='p', style_fingerprint='f')) == 'must_fix'


def test_empty_equipment_has_a_non_actor_outlet_and_is_not_a_visible_person():
    def entity(name, kind):
        return {'who': name, 'entity_kind': kind, 'gender': '不明', 'is_animal': False,
                'doing': '站着', 'frames': '1-4'}
    result = verify_to_verdict({'verdict': 'fine', 'people': [entity('席勒', 'character'),
                              entity('托尼', 'character'), entity('无人空甲', 'object')],
                              'count_checks': [counted('人物', 2, 2), counted('独立空甲', 1, 1)]})
    assert result['visible_people'] == 2
    assert len(result['verify']['people']) == 2
    assert result['verify']['objects'][0]['who'] == '无人空甲'
    assert result['severity'] == 'pass'
