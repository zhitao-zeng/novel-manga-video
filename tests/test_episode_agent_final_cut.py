import copy
from pathlib import Path

from tests.test_episode_agent_assembly import host
from novel_manga.application.agents.episode.evidence import EpisodeEvidence
from novel_manga.application.repair import delivery, history
from novel_manga.application.review import final_cut
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json

REAL_REVIEW_EVIDENCE = EpisodeEvidence.review_evidence


def test_render_returns_new_final_feedback_instead_of_overwriting_with_native_pass(host):
    ctx,tools,events,state=host
    before=read_json(ctx.episode_dir/'chapter_script.json')
    state['final_feedback']={'clip_01':'still in water when source says dry platform'}
    result=tools.dispatch('render')
    assert result['ok'] and result['quality_passed'] is False
    assert '全片终审' in result['review']['feedback']['clip_01']
    report=read_json(ctx.episode_dir/'thin_media_report.json')
    assert report['quality_review']['final_cut_passed'] is False
    assert report['quality_review']['remaining']==['clip_01']
    assert events==['render','review','assemble'] and len(state['final_reviews'])==1
    assert read_json(ctx.episode_dir/'chapter_script.json')==before


def test_new_render_after_failed_final_review_assembles_and_rechecks_new_take(host):
    ctx,tools,events,state=host
    state['final_feedback']={'clip_01':'current final shot does not meet its camera constraint'}
    assert tools.dispatch('render')['quality_passed'] is False
    previous=read_json(ctx.episode_dir/'episode_review.json')['clips']['clip_01']['take']
    counts=history.generation_counts(ctx.episode_dir)
    clip=next(c for c in read_json(ctx.episode_dir/'clip_plan.json')['clips'] if c['clip_id']=='clip_01')
    prompt=clip['prompt_h3'].replace('firm grip','relaxed grip')
    assert prompt!=clip['prompt_h3']
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'edit','reason':'clarify the existing visual direction','prompt_h3':prompt}]})
    assert tools.dispatch('apply')['changed']==['clip_01']
    state['final_feedback']={}
    rendered=tools.dispatch('render')
    assert rendered['ok'] and rendered['quality_passed'] is True
    assert events==['render','review','assemble','render','review','assemble']
    assert len(state['final_reviews'])==2
    after=read_json(ctx.episode_dir/'thin_media_report.json')
    assert after['quality_review']['native_content_passed'] is True
    assert after['quality_review']['final_cut_passed'] is True
    assert read_json(ctx.episode_dir/'episode_review.json')['clips']['clip_01']['take']!=previous
    assert history.generation_counts(ctx.episode_dir)['clip_01']==counts['clip_01']+1


def test_review_preserves_new_final_failure_after_native_review_passes(host):
    ctx,tools,events,state=host
    assert tools.dispatch('render')['quality_passed'] is True
    counts=history.generation_counts(ctx.episode_dir)
    media=read_json(ctx.episode_dir/'thin_media_report.json')
    Path(media['assembly']['final_video']).write_bytes(b'changed final movie, unchanged native clips')
    state['final_feedback']={'clip_01':'postproduction scene continuity failure'}
    review=tools.dispatch('review')
    assert '全片终审' in review['feedback']['clip_01']
    assert read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']['passed'] is False
    assert history.generation_counts(ctx.episode_dir)==counts
    assert len(state['final_reviews'])==2 and events==['render','review','assemble','review']


def test_finish_existing_assembly_requires_final_review_without_new_generation(host):
    ctx,tools,events,state=host
    tools.dispatch('render');counts=history.generation_counts(ctx.episode_dir)
    media=read_json(ctx.episode_dir/'thin_media_report.json');media.pop('final_cut_review')
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    (ctx.episode_dir/'final_cut_review.json').unlink()
    state['final_feedback']={'clip_01':'final movie goal conflicts with source'}
    result=tools.dispatch('finish')
    assert result['ok'] and result['passed'] is False
    assert result['quality_review']['remaining']==['clip_01']
    assert history.generation_counts(ctx.episode_dir)==counts
    assert events==['render','review','assemble'] and len(state['final_reviews'])==2


def test_incomplete_final_service_result_prevents_finish_without_blind_render(host):
    ctx,tools,events,state=host;state['final_complete']=False
    rendered=tools.dispatch('render');counts=history.generation_counts(ctx.episode_dir)
    assert rendered['quality_passed'] is False
    assert tools.dispatch('finish')['passed'] is False
    assert events==['render','review','assemble'] and history.generation_counts(ctx.episode_dir)==counts


def precise_candidate(ctx):
    media=read_json(ctx.episode_dir/'thin_media_report.json')
    source=ctx.episode_dir/'repair_candidate'/f'{ctx.episode_dir.name}.mp4'
    source.parent.mkdir();source.write_bytes(b'current assembled candidate')
    old=ctx.episode_dir/f'{ctx.episode_dir.name}.mp4';old.write_bytes(b'previous final')
    media['assembly'].update(final_video=str(source),pending_publish=True)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    review=read_json(ctx.episode_dir/'episode_review.json');review['review_mode']='verify'
    for row in review['clips'].values():row.update(verify={'verdict':'fine'},severity='pass',tier='optional')
    atomic_write_json(ctx.episode_dir/'episode_review.json',review)
    return media,review,source,old


def test_finish_publication_relocates_final_review_to_controlled_copy_and_reuses_it(host,monkeypatch):
    ctx,tools,events,state=host;tools.dispatch('render')
    media,review,source,old=precise_candidate(ctx)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'done')
    counts=history.generation_counts(ctx.episode_dir)
    assert tools.dispatch('finish')['passed'] is True
    promoted=read_json(ctx.episode_dir/'thin_media_report.json')
    final=promoted['final_cut_review']
    assert final['movie']==str(old) and final_cut.current(ctx.episode_dir,promoted,final)
    assert read_json(ctx.episode_dir/'final_cut_review.json')==final
    assert not source.exists() and old.read_bytes()==b'current assembled candidate'
    calls=len(state['final_reviews'])
    assert tools.dispatch('finish')['passed'] is True
    assert len(state['final_reviews'])==calls
    assert history.generation_counts(ctx.episode_dir)==counts


def test_direct_native_precise_publication_cannot_bypass_failed_final_review(host,monkeypatch):
    ctx,tools,events,state=host;tools.dispatch('render')
    media,review,source,old=precise_candidate(ctx)
    final={'policy':final_cut.POLICY,'movie':str(source),'context':final_cut.context(ctx.episode_dir,media,source),
           'complete':True,'passed':False,'issues':[{'clip_ids':['clip_01'],'problem':'current final failed'}],'errors':[]}
    media['final_cut_review']=final;atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'done')
    takes={cid:{'video':r['video'],'take':r['take']} for cid,r in review['clips'].items()}
    assert final_cut.current(ctx.episode_dir,media,final)
    assert delivery.publish_if_ready(ctx.episode_dir,review,takes) is False
    assert source.exists() and old.read_bytes()==b'previous final'


def test_persistent_episode_sandbox_receives_relative_final_cut_frames_and_global_times(host,monkeypatch,tmp_path):
    ctx,tools,events,state=host;tools.dispatch('render')
    media=read_json(ctx.episode_dir/'thin_media_report.json');review=read_json(ctx.episode_dir/'episode_review.json')
    image=tmp_path/'real-global-frame.jpg';image.write_bytes(b'global movie frame bytes')
    issue={'clip_ids':['clip_01'],'problem':'final body wet on dry platform','source_basis':'approved dry platform',
           'evidence':'global frame','frame_paths':[str(image)]}
    review['feedback']={'clip_01':'全片终审：dry platform conflict'}
    review['clips']['clip_01']['final_cut']={'movie':media['assembly']['final_video'],'issues':[issue],
        'frames':[{'path':str(image),'clip_id':'clip_01','shot_indexes':[1],'seconds':131.25}]}
    def native_frames(directory,cid,video,target):
        target.mkdir(parents=True,exist_ok=True);frame=target/'native.jpg';frame.write_bytes(b'native frame')
        return [frame],'fixture native'
    monkeypatch.setattr('novel_manga.application.repair.agent.provided_frames',native_frames)
    result=REAL_REVIEW_EVIDENCE(EpisodeEvidence(ctx),review,media,pending_only=True)
    packet=read_json(ctx.work_dir/result['evidence'])
    target=next(t for t in packet['targets'] if t['clip']['clip_id']=='clip_01')
    final=target['final_cut'];path=final['frames'][0]['path']
    assert path.startswith('input/latest-review/clip_01/final-cut/') and not Path(path).is_absolute()
    assert (ctx.work_dir/path).read_bytes()==image.read_bytes()
    assert final['frames'][0]['seconds']==131.25
    assert target['verdict']['final_cut']==final and 'Read the supplied final-cut JPEGs first' in target['final_cut_instruction']


def test_highlevel_native_keep_does_not_clear_current_final_cut_findings(host,monkeypatch):
    ctx,tools,events,state=host;state['final_feedback']={'clip_01':'dry platform missing in actual final movie'}
    tools.dispatch('render');counts=history.generation_counts(ctx.episode_dir)
    monkeypatch.setattr('novel_manga.application.review.execution.recheck_current_clip',
                        lambda *a: (_ for _ in ()).throw(AssertionError('Native keep is not movie evidence')))
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'keep','reason':'native take already passed'}]})
    result=tools.dispatch('apply')
    assert result['ok'] is False and 'native keep cannot resolve current final-cut' in result['blocked']['clip_01']
    assert result['accepted']==[] and history.generation_counts(ctx.episode_dir)==counts
    assert read_json(ctx.episode_dir/'episode_review.json')['final_cut_feedback']['clip_01']


def test_independent_art_findings_block_novel_finish_and_reach_episode_agent(host, monkeypatch, tmp_path):
    from novel_manga.application.review import independent
    ctx,tools,events,state=host
    tools.dispatch('render'); counts=history.generation_counts(ctx.episode_dir)
    final=read_json(ctx.episode_dir/'final_cut_review.json')
    image=tmp_path/'actual-final.jpg'; image.write_bytes(b'actual assembled frame fixture')
    frame={'path':str(image),'clip_id':'clip_01','seconds':2.5,'label':'middle','shot_indexes':[1]}
    final['frames']=[frame]; atomic_write_json(ctx.episode_dir/'final_cut_review.json',final)
    raw={'movie':final['movie'],'complete':True,'passed':False,'observations':['the sole camera phone is visible'],
         'frames':[frame],'issues':[{'id':'phone','clip_ids':['clip_01'],'problem':'sole recording phone visible',
         'source_basis':'approved single phone viewpoint','evidence':'actual current final frame',
         'frame_paths':[str(image)]}]}
    independent.register(ctx.episode_dir,read_json(ctx.episode_dir/'thin_media_report.json'),raw)
    # A Qwen-only final result must not bypass a required independent result.
    assert tools.dispatch('finish')['passed'] is False
    fake_qwen=final_cut.review_assembly
    def both(runner,report,**kwargs):
        result=fake_qwen(runner,report,**kwargs); result['frames']=[frame]
        return independent.combine(ctx.episode_dir,result)
    monkeypatch.setattr(final_cut,'review_assembly',both)
    review=tools.dispatch('review')
    assert 'sole recording phone visible' in review['feedback']['clip_01']
    assert tools.dispatch('finish')['passed'] is False
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'keep','reason':'native take already passed'}]})
    applied=tools.dispatch('apply')
    assert not applied['ok'] and 'native keep cannot resolve' in applied['blocked']['clip_01']
    assert history.generation_counts(ctx.episode_dir)==counts
