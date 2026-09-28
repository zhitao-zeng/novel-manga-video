import json
from types import SimpleNamespace
from novel_manga.application.rendering import reviewed
from novel_manga.application.review import execution as review_execution


def test_existing_repair_uses_full_preparation_and_preserves_budget(tmp_path,monkeypatch):
    clips=[{'clip_id':'a','kind':'video'},{'clip_id':'b','kind':'video'}]
    report={'assembly':{'thin_passed':True},'clips':[{'clip_id':'a'},{'clip_id':'b'}],
            'clip_plan_fingerprint':reviewed.plan_fingerprint({'clips':clips})}
    for name,data in [('clip_plan.json',{'clips':clips}),('chapter_script.json',{}),('review_feedback.json',{}),('thin_media_report.json',report)]:
        (tmp_path/name).write_text(json.dumps(data))
    ctx=SimpleNamespace(episode_dir=tmp_path,clip_plan={'clips':clips},cache_only=False)
    seen=[];reviews=[]
    monkeypatch.setattr(reviewed.history,'adopt_reviewed_run',lambda *a:None)
    monkeypatch.setattr(reviewed.history,'load',lambda *a:{})
    monkeypatch.setattr(reviewed.managed,'generated_counts',lambda *a:{'a':2,'b':1})
    monkeypatch.setattr(reviewed.managed,'candidates',lambda *a: (['a'],{}) if not seen else ([],{}))
    def prepare(*args):seen.append('full repair');return {'changed':['a'],'skip_render':False}
    monkeypatch.setattr(reviewed.managed,'prepare',prepare)
    def review(*args,**kwargs):
        reviews.append(kwargs)
        return {'clips':{'a':{'severity':'pass'},'b':{'severity':'pass'}},'feedback':{'a':'wrong'} if len(reviews)==1 else {}}
    monkeypatch.setattr(review_execution,'review_episode',review)
    def render():
        assert ctx._managed_remaining=={'a':1,'b':2}
        seen.append('render');return report
    out=reviewed.run(SimpleNamespace(context=ctx,run=render),repair_existing=True)
    assert seen==['full repair','render'] and out['quality_review']['passed']
    assert len(reviews)==2 and all(r['fresh'] is False for r in reviews)


def test_cache_miss_does_not_write_or_review(tmp_path,monkeypatch):
    ctx=SimpleNamespace(episode_dir=tmp_path,cache_only=True)
    def forbidden(*a,**kw):raise AssertionError('not allowed')
    monkeypatch.setattr(review_execution,'review_episode',forbidden)
    out=reviewed.run(SimpleNamespace(context=ctx,run=lambda:{'status':'cache_miss'}))
    assert not out['quality_review']['passed'] and not list(tmp_path.iterdir())


def test_existing_repair_cannot_accept_media_from_an_older_plan(tmp_path):
    import pytest
    (tmp_path/'thin_media_report.json').write_text(json.dumps({'clips':[{'clip_id':'a'}],'clip_plan_fingerprint':'old'}))
    ctx=SimpleNamespace(episode_dir=tmp_path,clip_plan={'clips':[{'clip_id':'a','kind':'video','prompt':'changed'}]})
    with pytest.raises(ValueError,match='predates this plan'):
        reviewed.run(SimpleNamespace(context=ctx),repair_existing=True)


def test_first_take_input_repair_runs_before_waiting_for_a_video_verdict(tmp_path, monkeypatch):
    clips = [{'clip_id': 'a', 'kind': 'video'}]
    for name, data in [('clip_plan.json', {'clips': clips}), ('chapter_script.json', {}), ('review_feedback.json', {})]:
        (tmp_path / name).write_text(json.dumps(data))
    ctx = SimpleNamespace(episode_dir=tmp_path, clip_plan={'clips': clips}, cache_only=False, feedback={})
    events = []
    monkeypatch.setattr(reviewed.history, 'adopt_reviewed_run', lambda *a: None)
    monkeypatch.setattr(reviewed.history, 'begin_trial', lambda *a, **k: None)
    monkeypatch.setattr(reviewed.history, 'load', lambda *a: {'trials': [{}]})
    monkeypatch.setattr(reviewed.history, 'save', lambda *a: None)
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda *a: ([], {}))
    def prepare(*args):
        events.append('repair input')
        return {'changed': ['a'], 'blocked': {}}
    monkeypatch.setattr(reviewed.managed, 'prepare_request_conflicts', prepare)
    def render():
        events.append('render')
        if len(events) == 1:
            return {'clips': [], 'failed_clips': ['a'], 'blocked_clips': {'a': ['request: wrong clothing']}}
        return {'clips': [{'clip_id': 'a'}], 'assembly': {'thin_passed': True}, 'failed_clips': [], 'blocked_clips': {}}
    monkeypatch.setattr(reviewed, '_review', lambda *a, **k:
                        {'clips': {'a': {'severity': 'pass'}} if len(events) > 1 else {}, 'feedback': {}})
    result = reviewed.run(SimpleNamespace(context=ctx, run=render))
    assert events == ['render', 'repair input', 'render']
    assert result['quality_review']['passed']
