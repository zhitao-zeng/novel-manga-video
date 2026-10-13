"""754 regression: a named identity is not the count of visible physical bodies."""
import pytest
from novel_manga.review import policy, contracts

def answer(**kw):
    return {'verdict':'fine','people':[],**{k:False for k in contracts.VERIFY_FLAGS},
            'ghost_text':False,'evidence':'同一帧左前背影与右侧正面同时可见','instruction':'',**kw}

def test_identity_count_cannot_hide_two_simultaneous_bodies():
    row={'count_kind':'characters','entity':'同一人物','frames':'图3','expected_min':1,'expected_max':1,
         'observed':1,'instances':['左前方独立的肩背','右侧正面的独立身体'],'reason':'同一个名字'}
    with pytest.raises(ValueError,match='instance'):
        policy.verify_to_verdict(answer(count_checks=[row]))

def test_observed_physical_instances_fail_even_when_model_says_fine():
    row={'count_kind':'characters','entity':'同一人物','frames':'图3','expected_min':1,'expected_max':1,
         'observed':2,'instances':['左前方肩背','右侧正面身体'],'reason':'同一帧两具身体'}
    v=policy.verify_to_verdict(answer(count_checks=[row]))
    assert v['severity']=='fail'

def test_scene_disappearance_is_not_an_unmeasured_location():
    v=policy.verify_to_verdict(answer(scene_missing=True,evidence='街道消失，人物站在纯色背景前',instruction='保留实际街道环境。'))
    assert v['severity']=='fail' and v['location_ok'] is False
    assert '街道消失' in v['location_issue']

def test_sequential_turnaround_and_an_evidenced_mirror_do_not_inflate_body_count():
    row={'count_kind':'characters','entity':'甲','frames':'图4','expected_min':1,'expected_max':1,
         'observed':1,'instances':['画面中央的同一身体，镜中像不另计'],'reason':'图3背对、图4转身；图4镜面有明确边框'}
    assert policy.verify_to_verdict(answer(count_checks=[row]))['severity']=='pass'


def test_background_crowd_does_not_require_a_description_per_person():
    row = {'count_kind': 'background_people', 'entity': '远处群众', 'frames': '3',
           'expected_min': 100, 'expected_max': 200, 'observed': 50, 'instances': [], 'reason': '群众已出现'}
    data = answer(count_checks=[row])
    assert policy.count_mismatches(data, require_instances=True) == []
    assert policy.verify_to_verdict(data)['severity'] == 'minor'


def test_new_individual_count_requires_physical_evidence_but_old_result_can_be_read():
    row = {'count_kind': 'characters', 'entity': '甲', 'frames': '3',
           'expected_min': 1, 'expected_max': 1, 'observed': 1, 'reason': '甲在中央'}
    data = answer(count_checks=[row])
    with pytest.raises(ValueError, match='instance'):
        policy.count_mismatches(data, require_instances=True)
    assert policy.verify_to_verdict(data)['severity'] == 'pass'
