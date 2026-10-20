"""Whole-movie audits are requested work; native content, speech and final QC remain mandatory."""
from tests.test_episode_agent_assembly import host
from novel_manga.application.review import final_cut
from novel_manga.util import atomic_write_json,read_json


def test_new_episode_finishes_with_native_review_and_final_qc_without_whole_movie_call(host):
    ctx,tools,events,state=host
    (ctx.episode_dir/'work/review/final_movie_policy.json').unlink()
    result=tools.dispatch('render')
    assert result['quality_passed'] and state['final_reviews']==[]
    assert not (ctx.episode_dir/'final_cut_review.json').exists()
    quality=read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']
    assert quality['final_cut_status']=='not_required' and quality['final_cut_passed'] is None
    assert tools.dispatch('finish')['passed']
    assert state['final_reviews']==[] and events==['render','review','assemble']


def test_existing_confirmed_movie_failure_is_not_lost_when_default_becomes_optional(host):
    ctx,tools,events,state=host
    state['final_feedback']={'clip_01':'wrong person receives the object'}
    assert tools.dispatch('render')['quality_passed'] is False
    (ctx.episode_dir/'work/review/final_movie_policy.json').unlink()
    assert final_cut.required(ctx.episode_dir)
    result=tools.dispatch('finish')
    assert result.get('passed') is not True
    assert 'clip_01' in read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']['remaining']


def test_explicit_user_scope_records_skipped_movie_audit_without_inventing_pass(host):
    ctx,tools,events,state=host
    atomic_write_json(ctx.episode_dir/'work/review/final_movie_policy.json',
                      {'required':False,'authorization':'explicit test authorization'})
    assert tools.dispatch('render')['quality_passed']
    quality=read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']
    assert quality['final_cut_status']=='skipped_by_user' and quality['final_cut_passed'] is None
    assert state['final_reviews']==[]


def test_optional_movie_review_never_clears_native_or_speech_failures(host):
    ctx,tools,events,state=host
    (ctx.episode_dir/'work/review/final_movie_policy.json').unlink()
    state['feedback']={'clip_01':'action by the wrong person'}
    state['speech_failed']=['clip_02']
    assert tools.dispatch('render')['quality_passed'] is False
    result=tools.dispatch('finish')
    assert result.get('passed') is not True
    quality=read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']
    assert set(quality['remaining']) >= {'clip_01','clip_02'}


def test_postproduction_receipt_requests_movie_audit_even_without_registration(tmp_path):
    assert final_cut.required(tmp_path) is False
    assert final_cut.required(tmp_path,receipt={'packaging':{'captions':True}}) is True


def test_explicit_required_review_without_result_cannot_finish_or_publish(host,monkeypatch):
    from novel_manga.application.repair.delivery import publish_if_ready
    ctx,tools,events,state=host
    monkeypatch.setattr(tools.media,'review_final',lambda report,*a:report)
    assert tools.dispatch('render')['quality_passed'] is False
    quality=read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']
    assert quality['native_content_passed'] and quality['final_cut_complete'] is False
    assert tools.dispatch('finish')['passed'] is False
    assert publish_if_ready(ctx.episode_dir,read_json(ctx.episode_dir/'episode_review.json'),{}) is False
