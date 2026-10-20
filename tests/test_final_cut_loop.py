import json
from types import SimpleNamespace
from pathlib import Path
import pytest

from novel_manga.application.rendering import reviewed
from novel_manga.media.context import RenderContext, RenderOptions


def fixture(tmp_path,monkeypatch):
    from novel_manga.application.review.movie_context import assembly_inputs
    clip={'clip_id':'a','kind':'video','shot_indexes':[1]}
    plan={'clips':[clip]};movie=tmp_path/'movie.mp4';movie.write_bytes(b'assembled')
    video=tmp_path/'clip.mp4';video.write_bytes(b'current native take')
    report={'clips':[{'clip_id':'a','selected':{'video':str(video)}}],
            'assembly':{'thin_passed':True,'final_video':str(movie)},
            'clip_plan_fingerprint':reviewed.plan_fingerprint(plan)}
    report['assembly']['source_inputs']=assembly_inputs(plan,report)
    native={'clips':{'a':{'severity':'pass','video':str(video),'take':[1,2,3]}},'feedback':{}}
    for name,data in [('clip_plan.json',plan),('chapter_script.json',{}),
                      ('review_feedback.json',{}),('thin_media_report.json',report),('episode_review.json',native)]:
        (tmp_path/name).write_text(json.dumps(data))
    ctx=RenderContext(episode_dir=tmp_path,clip_plan=plan,options=RenderOptions(cache_only=False))
    (tmp_path/'work/review').mkdir(parents=True,exist_ok=True)
    (tmp_path/'work/review/final_movie_policy.json').write_text(json.dumps(
        {'required':True,'authorization':'explicit full-movie repair loop test'}))
    monkeypatch.setattr(reviewed,'_review',lambda *a,**k:json.loads(json.dumps(native)))
    monkeypatch.setattr(reviewed.history,'adopt_reviewed_run',lambda *a:None)
    monkeypatch.setattr(reviewed.history,'load',lambda *a:{})
    monkeypatch.setattr(reviewed.history,'generation_counts',lambda *a:{'a':1})
    return SimpleNamespace(context=ctx,run=lambda:report),report,movie


def verdict(movie,passed):
    return {'complete':True,'passed':passed,'movie':str(movie),'frames':[],'errors':[],
            'issues':[] if passed else [{'clip_ids':['a'],'problem':'camera sees its own recording phone',
                 'source_basis':'only this phone records','evidence':'movie frame','frame_paths':[]}]}


def test_final_failure_after_native_pass_enters_existing_repair(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch);events=[]
    def final(media):
        events.append('final');return verdict(movie,len(events)>2)
    monkeypatch.setattr(reviewed.managed,'candidates',lambda d,r: (list(r['feedback']),{}))
    def prepare(d,ids):
        events.append('prepare');assert ids==['a'];return {'changed':['a'],'skip_render':False}
    monkeypatch.setattr(reviewed.managed,'prepare',prepare)
    def render():events.append('render');return report
    runner.run=render
    out=reviewed.run(runner,repair_existing=True,final_review=final)
    assert events==['final','prepare','render','final']
    assert out['quality_review']['passed'] is True
    assert out['quality_review']['final_cut_passed'] is True


def test_final_failure_is_not_cleared_by_native_keep(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch);events=[]
    monkeypatch.setattr(reviewed.managed,'candidates',lambda d,r: (list(r['feedback']),{}))
    def prepare(d,ids):events.append('keep');return {'accepted':['a'],'changed':[],'skip_render':True}
    monkeypatch.setattr(reviewed.managed,'prepare',prepare)
    out=reviewed.run(runner,repair_existing=True,final_review=lambda m:verdict(movie,False))
    assert events==['keep']
    assert out['quality_review']['passed'] is False
    assert out['quality_review']['remaining']==['a']


def test_old_failed_final_does_not_erase_native_readiness_for_new_assembly(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(reviewed.managed,'candidates',lambda *a:([],{}))
    out=reviewed.run(runner,repair_existing=True,final_review=lambda m:verdict(movie,False))
    assert out['quality_review']['native_content_passed'] is True
    native=json.loads((tmp_path/'episode_review.json').read_text())
    native['feedback']={};native.pop('final_cut_feedback',None);native.pop('native_feedback_before_final',None)
    report['clips'][0]['selected']['video']=str(tmp_path/'new-take.mp4')
    (tmp_path/'new-take.mp4').write_bytes(b'new native take')
    # A fresh native recheck has no final feedback for the changed take.
    native['clips']['a'].pop('final_cut',None)
    from novel_manga.application.review.execution import save_result
    out=save_result(tmp_path,report,native,plan=runner.context.clip_plan)
    assert out['quality_review']['native_content_passed'] is True
    assert out['quality_review']['content_passed'] is False


def test_incomplete_final_review_does_not_start_blind_repair(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(reviewed.managed,'candidates',lambda *a:([],{}))
    monkeypatch.setattr(reviewed.managed,'prepare',lambda *a:pytest.fail('blind repair'))
    out=reviewed.run(runner,repair_existing=True,final_review=lambda m:{**verdict(movie,True),
                            'complete':False,'passed':False,'errors':['review service unavailable']})
    assert out['quality_review']['passed'] is False
    assert out['quality_review']['final_cut_complete'] is False


def test_exhausted_counts_remain_blocked_after_final_feedback(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(reviewed.managed,'candidates',lambda *a:([],{'a':'effective clip retry budget used'}))
    monkeypatch.setattr(reviewed.managed,'prepare',lambda *a:pytest.fail('exhausted repair'))
    out=reviewed.run(runner,repair_existing=True,final_review=lambda m:verdict(movie,False))
    assert not out['quality_review']['passed']
    assert out['quality_review']['blocked']['a']=='effective clip retry budget used'


def test_partial_final_findings_do_not_start_blind_repair(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(reviewed.managed,'candidates',lambda d,r:(list(r.get('feedback',{})),{}))
    monkeypatch.setattr(reviewed.managed,'prepare',lambda *a:pytest.fail('partial review must complete first'))
    out=reviewed.run(runner,repair_existing=True,final_review=lambda m:{**verdict(movie,False),
                            'complete':False,'errors':['later frame group service failed']})
    assert out['quality_review']['passed'] is False
    assert out['quality_review']['final_cut_complete'] is False


def test_partial_repair_success_does_not_retry_unavailable_agent_in_same_run(tmp_path,monkeypatch):
    runner,report,movie=fixture(tmp_path,monkeypatch);events=[]
    runner.context.clip_plan['clips'].append({'clip_id':'b','kind':'video','shot_indexes':[2]})
    (tmp_path/'clip_plan.json').write_text(json.dumps(runner.context.clip_plan))
    report['clip_plan_fingerprint']=reviewed.plan_fingerprint(runner.context.clip_plan)
    (tmp_path/'thin_media_report.json').write_text(json.dumps(report))
    native=json.loads((tmp_path/'episode_review.json').read_text())
    native['clips']['b']={**native['clips']['a']}
    monkeypatch.setattr(reviewed,'_review',lambda *a,**k:json.loads(json.dumps(native)))
    monkeypatch.setattr(reviewed.managed,'candidates',lambda d,r:(list(r['feedback']),{}))
    def final(media):
        result=verdict(movie,False)
        if not events:result['issues'].append({**result['issues'][0],'clip_ids':['b']})
        return result
    def prepare(d,ids):
        events.append(('prepare',ids))
        return {'changed':['b'],'skip_render':False,'blocked':{'a':'API unavailable'},
                'service_failures':{'a':'API unavailable'}}
    monkeypatch.setattr(reviewed.managed,'prepare',prepare)
    def render():events.append(('render','b'));movie.write_bytes(b'repaired b');return report
    runner.run=render
    out=reviewed.run(runner,repair_existing=True,final_review=final)
    assert events==[('prepare',['a','b']),('render','b')]
    assert out['quality_review']['blocked']['a']=='API unavailable'
