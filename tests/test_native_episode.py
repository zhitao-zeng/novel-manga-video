from types import SimpleNamespace
import pytest

from novel_manga.application.production import native_episode
from novel_manga.util import atomic_write_json, read_json


def context(tmp_path):
    ctx=SimpleNamespace(run_dir=tmp_path/'run',episode_dir=tmp_path/'run/book/book_1',
        novel_dir=tmp_path/'run/book',work_dir=tmp_path/'run/sandbox',
        source_episode_dir=tmp_path/'original/book_1',input_mode='source')
    ctx.episode_dir.mkdir(parents=True)
    (ctx.work_dir/'output').mkdir(parents=True)
    (ctx.work_dir/'input').mkdir(parents=True)
    (ctx.run_dir/'source.txt').write_text('原始正文')
    return ctx


def test_existing_takes_bypass_preparation_and_replanning(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    tools=SimpleNamespace(_main_media_started=lambda:True)
    monkeypatch.setattr(native_episode,'prepare_one',lambda *a,**k: (_ for _ in ()).throw(AssertionError('replanned filmed episode')))
    result=native_episode.prepare(ctx,tools)
    assert result['ready'] and result['existing_media']


def test_native_preparation_failure_never_becomes_agent_authoring(tmp_path,monkeypatch):
    ctx=context(tmp_path);calls=[]
    tools=SimpleNamespace(_main_media_started=lambda:False)
    monkeypatch.setattr(native_episode,'prepare_one',lambda *a,**k:{'status':'needs_repair','reason':'missing scene'})
    monkeypatch.setattr(native_episode,'patch_inputs',lambda *a:calls.append('native_patch') or {'ok':False})
    result=native_episode.prepare(ctx,tools)
    assert not result['ready'] and result['stage']=='preparation'
    assert calls==['native_patch']
    assert read_json(ctx.run_dir/'pipeline-production.json')['status']=='blocked'


def test_native_ready_marks_inputs_without_video_or_agent(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    tools=SimpleNamespace(_main_media_started=lambda:False)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01'}]})
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[{'index':1}]})
    monkeypatch.setattr(native_episode,'prepare_one',lambda *a,**k:{'status':'ready'})
    monkeypatch.setattr(native_episode,'refresh_inputs',lambda *a:None)
    result=native_episode.prepare(ctx,tools)
    assert result['ready']
    assert read_json(ctx.run_dir/'prepared.json')['method']=='native production pipeline'
    assert not (ctx.work_dir/'episode-session.json').exists()


def test_review_phase_archives_authoring_session_once_but_retains_work(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'author'})
    atomic_write_json(ctx.work_dir/'output/episode.json',{'keep':'draft'})
    assert native_episode.begin_review(ctx)
    assert not (ctx.work_dir/'episode-session.json').exists()
    assert read_json(ctx.run_dir/'pipeline-authoring-session.json')['session_id']=='author'
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'reviewer'})
    assert not native_episode.begin_review(ctx)
    assert read_json(ctx.work_dir/'episode-session.json')['session_id']=='reviewer'
    assert read_json(ctx.work_dir/'output/episode.json')=={'keep':'draft'}


def test_a_task_receipt_is_not_reviewable_media(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4.task.json',{'task_id':'submitted'})
    assert not native_episode.reviewable(ctx)


def test_native_pending_marker_cannot_submit_video(tmp_path,monkeypatch):
    from support.episode_existing import prepared
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,_,_=prepared(tmp_path,monkeypatch)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'production_mode':'pipeline_review'})
    atomic_write_json(ctx.run_dir/'prepared.json',{'ready':False,'native_preparation_pending':True})
    monkeypatch.setattr(EpisodeTools,'_runner',lambda *a: (_ for _ in ()).throw(AssertionError('video before full preparation')))
    answer=EpisodeTools(ctx).dispatch('render',{})
    assert not answer['ok'] and answer['error']=='native_preparation_required'


@pytest.mark.parametrize('old_prompt',['','invalid English H3 text'])
def test_native_bad_english_uses_plan_contract_and_explicit_preparation_target(tmp_path,monkeypatch,old_prompt):
    from support.episode_existing import prepared
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,plan,prompts=prepared(tmp_path,monkeypatch)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'production_mode':'pipeline_review','memory_scope':'post_render_repair'})
    atomic_write_json(ctx.run_dir/'prepared.json',{'ready':False,'native_preparation_pending':True})
    plan['clips'][0]['prompt_h3']=old_prompt
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    native_episode.preparation_targets(ctx,{'reason':'English prompt incomplete'})
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[{
        'clip_id':'clip_01','action':'edit','reason':'complete missing request','prompt_h3':prompts['clip_01']}]})
    tools=EpisodeTools(ctx)
    assert tools.dispatch('check',{'file':'output/repair.json'})['valid']
    assert tools.dispatch('apply',{})['changed']==['clip_01']


def test_source_correction_schema_excludes_english_edits_and_unaffected_clips(tmp_path,monkeypatch):
    from support.episode_existing import prepared
    import json
    ctx,plan,_=prepared(tmp_path,monkeypatch,old_english=False)
    (ctx.run_dir/'source.txt').write_text('原文事实')
    monkeypatch.setattr(native_episode,'ask_json',lambda *a,**k:pytest.fail('semantic work went to ordinary translator'))
    tools=SimpleNamespace(status=lambda payload:{})
    outcome=native_episode.patch_inputs(ctx,tools,{'status':'needs_repair','audit':{'issues':[
        {'kind':'speaker','stage':1,'problem':'wrong owner'}]}})
    assert outcome['needs_semantic_repair'] and not outcome['ok']


def test_semantic_failure_goes_to_targeted_agent_not_ordinary_replanner(tmp_path,monkeypatch):
    ctx=context(tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','shot_indexes':[1]}]})
    failure={'status':'needs_repair','audit':{'source_readable':True,'issues':[
        {'stage':1,'kind':'speaker','problem':'private thought became public speech'}]}}
    monkeypatch.setattr(native_episode,'prepare_one',lambda *a,**k:failure)
    monkeypatch.setattr(native_episode,'patch_inputs',lambda *a:pytest.fail('ordinary model rewriting dialogue'))
    monkeypatch.setattr('novel_manga.application.preparation.actions.replan',lambda *a:pytest.fail('automatic whole replan'))
    result=native_episode.prepare(ctx,SimpleNamespace(_main_media_started=lambda:False))
    assert result['needs_semantic_repair'] and not result['ready']
    assert native_episode.semantic_pending(ctx)


def test_reference_content_risk_uses_the_real_readiness_list_shape():
    assert native_episode.semantic_failure({'status':'needs_repair','blocks':{
        'clip_01':['risk: 场景卡含有与本镜不符的人物']}})


@pytest.mark.parametrize('semantic',[False,True])
def test_translation_producer_distinguishes_format_errors_from_source_conflicts(tmp_path,monkeypatch,semantic):
    from novel_manga.application.preparation import actions
    ctx=context(tmp_path)
    clip={'clip_id':'clip_01','kind':'video','shot_indexes':[1]}
    monkeypatch.setattr('novel_manga.application.profiles.h3_prompt_outdated',lambda *a,**k:True)
    def failed(clip,**kwargs):
        message='The source assigns this speech to another person' if semantic else 'ValueError: lost dialogue slots'
        kwargs['problems'].append(message)
        if semantic:
            kwargs.get('source_conflicts',[]).append(message)
        return False
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',failed)
    failure=actions.translate_and_check(ctx.episode_dir,{'clips':[clip]}, {'source_readable':True,'issues':[]})
    assert native_episode.semantic_failure(failure) is semantic
    assert ('request_conflicts' in failure) is semantic
    assert ('request_errors' in failure) is not semantic


def editorial_case(tmp_path):
    ctx=context(tmp_path)
    atomic_write_json(ctx.run_dir/'pipeline-production.json',{'status':'semantic_repair','failure':{
        'audit':{'issues':[{'kind':'speaker','stage':1,'problem':'wrong delivery'}]}}})
    return ctx,SimpleNamespace(_main_media_started=lambda:False,_archive_adoption=lambda:None)


def test_only_explicit_agent_brief_invokes_one_whole_replan(tmp_path,monkeypatch):
    ctx,tools=editorial_case(tmp_path);calls=[]
    monkeypatch.setattr('novel_manga.application.preparation.actions.replan',lambda *a:calls.append(a))
    row={'clip_id':'clip_01','action':'replan','reason':'missing causal scene','source_quote':'原始正文',
         'instructions':'Keep the valid ending and restore the cause before it.'}
    result=native_episode.editorial_request(ctx,tools,row)
    assert result['replanned'] and len(calls)==1
    assert calls[0][1][0]['instructions']==row['instructions']
    with pytest.raises(ValueError,match='already been used'):
        native_episode.editorial_request(ctx,tools,row)


def test_input_recheck_is_cached_and_does_not_accept_video(tmp_path,monkeypatch):
    ctx,tools=editorial_case(tmp_path);calls=[]
    monkeypatch.setattr('novel_manga.application.preparation.audit.audit',
        lambda *a,**k:calls.append(k) or {'source_readable':True,'issues':[]})
    row={'clip_id':'clip_01','action':'recheck_input','reason':'offscreen delivery retains the actual owner',
         'source_quote':'原始正文'}
    first=native_episode.editorial_request(ctx,tools,row)
    second=native_episode.editorial_request(ctx,tools,row)
    assert first['source_passed'] and second['source_passed'] and len(calls)==1
    assert 'passed' not in first and not (ctx.run_dir/'final-candidate').exists()
