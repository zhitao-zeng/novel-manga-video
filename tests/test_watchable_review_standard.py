from types import SimpleNamespace
import pytest
from novel_manga.application.preparation import request_check
from novel_manga.review import policy,contracts


def finding(aspect='wearing',scope='advisory'):
    return {'basis':'reference','aspect':aspect,'picture':1,'lines':[1],
            'subject_specific':True,'relation':'contradiction',
            'reason':'Only the robe color differs; no story consequence.', 'scope_recheck':scope}


def test_input_and_generation_share_cosmetic_warnings(monkeypatch,tmp_path):
    answer=request_check.interpret({'findings':[finding()]},['A robe'],1)
    assert answer['consistent'] is True and not answer['problems']
    assert answer['warnings'][0]['aspect']=='wearing'
    monkeypatch.setattr(request_check,'cached_consistency',lambda *a,**k:answer)
    ctx=SimpleNamespace(novel_dir=tmp_path,work=tmp_path,options=SimpleNamespace(prepared_requests_only=False),
                        state=SimpleNamespace(blocked_clips={}))
    assert request_check.before_generation(ctx,{'clip_id':'clip_01'}, {})==answer


@pytest.mark.parametrize('aspect,scope',[('identity','advisory'),('action','advisory'),('wearing','uncertain'),('wearing','in_scope')])
def test_cosmetic_label_does_not_release_material_or_unknown_input(aspect,scope):
    item=finding(aspect,scope)
    if aspect=='action':item.update(basis='request',picture=0)
    answer=request_check.interpret({'findings':[item]},['Current instruction'],1)
    assert answer['consistent'] is False and answer['problems']


def answer(kind='background_people',observed=7):
    return {'verdict':'subtle','evidence':'Nonessential crowd count differs.',
        **{flag:False for flag in contracts.VERIFY_FLAGS},'ghost_text':False,
        'count_checks':[{'count_kind':kind,'entity':'unnamed background crowd','frames':'1-4',
                         'expected_min':20,'expected_max':30,'observed':observed,'reason':'The group is visibly present; its exact size is not a story fact.'}]}


def test_noncritical_crowd_count_is_a_warning():
    result=policy.verify_to_verdict(answer())
    assert result['severity']=='minor' and result['warnings']
    assert not policy.count_mismatches(result['verify'])


@pytest.mark.parametrize('kind,observed',[('characters',7),('background_people',0),('worn_equipment',7),('independent_objects',7)])
def test_missing_group_or_material_counts_remain_failures(kind,observed):
    assert policy.verify_to_verdict(answer(kind,observed))['severity']=='fail'


@pytest.mark.parametrize('flag',contracts.VERIFY_FLAGS)
def test_crowd_warning_never_clears_an_independent_major_flag(flag):
    raw=answer();raw[flag]=True
    assert policy.verify_to_verdict(raw)['severity']=='fail'


def test_unknown_major_observation_cannot_enable_crowd_warning():
    raw=answer();raw.pop('body_artifact')
    assert policy.verify_to_verdict(raw)['severity']=='fail'


def test_watchable_standard_does_not_turn_incomplete_review_into_a_pass():
    raw=answer();raw['verdict']=None
    with pytest.raises(ValueError):policy.verify_to_verdict(raw)


def test_cosmetic_clothing_does_not_become_wrong_identity_via_old_text_heuristics():
    raw=answer();raw['count_checks']=[];raw['evidence']='林齐穿了别人的衣服，只有配色不同，没有身份或剧情影响。'
    result=policy.verify_to_verdict(raw)
    bible=SimpleNamespace(characters=[SimpleNamespace(name='林齐',role='主角')])
    assert policy.fix_tier(result,bible)=='optional'


def test_warning_never_removes_a_separate_speech_failure():
    from novel_manga.review.acceptance import evaluate_acceptance
    review=policy.verify_to_verdict(answer());review['tier']='optional'
    outcome=evaluate_acceptance({'clip_01'},
        {'gate_failed_clips':['clip_01'],'assembly':{'thin_passed':True}},
        {'clips':{'clip_01':review},'feedback':{}})
    assert outcome.passed is False and outcome.remaining==['clip_01']
