"""Review precedes expensive assembly; delivery still requires full technical QC."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history
from novel_manga.review.contracts import REVIEW_POLICY
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


def test_first_pass_returns_after_media_without_review_or_assembly(host, monkeypatch):
    ctx, _, events, _ = host
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY', '1')
    result = EpisodeTools(ctx).render({})
    assert events == ['render']
    assert result['review_deferred'] is True and result['first_pass_remaining'] == []
    assert not result.get('quality_passed')
    with pytest.raises(ValueError, match='first pass'):
        EpisodeTools(ctx).finish({})


@pytest.fixture
def host(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    events = []
    state = {'feedback': {}, 'speech_failed': [], 'missing': [], 'review_missing': [],
             'review_error': [], 'technical_passed': True}

    class Runner:
        def __init__(self):
            self.context = SimpleNamespace(episode_dir=ctx.episode_dir)

        def run(self, *, assemble=True):
            events.append('render')
            plan = read_json(ctx.episode_dir/'clip_plan.json')
            rows = []
            for clip in plan['clips']:
                cid = clip['clip_id']
                if cid in state['missing']:
                    rows.append({'clip_id':cid,'error':'missing current video','selected':None})
                    continue
                video = ctx.episode_dir/f'work/clips/{cid}/attempt_01/clip.mp4'
                video.parent.mkdir(parents=True,exist_ok=True)
                request = history.generation_request(ctx.episode_dir,video,{'clip_id':cid},
                    expected_plan=plan_fingerprint(plan),expected_notes={})
                atomic_write_json(video.parent/'request.json',request)
                video.write_bytes(b'current selected video')
                history.complete_generation(ctx.episode_dir,cid,video,duration=15)
                rows.append({'clip_id':cid,'attempts':[], 'selected':{'video':str(video),
                    'passed':cid not in state['speech_failed'], 'duration':15, 'chunks':[]}})
            report = {'status':'clips_failed' if state['missing'] else 'clips_ready',
                'clips':rows,'failed_clips':list(state['missing']),
                'gate_failed_clips':list(state['speech_failed']),'blocked_clips':{},
                'clip_plan_fingerprint':plan_fingerprint(plan),
                'prompt_h3_fingerprint':h3_prompt_fingerprint(plan), 'review_feedback':{},
                'assembly':None, 'elapsed_seconds':3}
            if assemble and not state['missing']:
                report.update(status='assembled',assembly=self.assemble(rows))
            atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
            history.record_render(ctx.episode_dir,report)
            return report

        def assemble(self, rows):
            events.append('assemble')
            assert all(Path(r['selected']['video']).is_file() for r in rows)
            video = ctx.episode_dir/'candidate.mp4'
            video.write_bytes(b'assembled candidate')
            return {'final_video':str(video),'thin_passed':state['technical_passed'],
                    'media_qc_passed':state['technical_passed']}

    def review(directory, *, fresh):
        events.append('review')
        assert fresh is False
        report = read_json(directory/'thin_media_report.json')
        rows = {r['clip_id']:{'video':r['selected']['video'],
                  'take':take_identity(Path(r['selected']['video'])),
                  'severity':'review_error' if r['clip_id'] in state['review_error'] else
                             'fail' if r['clip_id'] in state['feedback'] else 'pass'}
                for r in report['clips'] if r.get('selected') and r['clip_id'] not in state['review_missing']}
        value = {'policy':REVIEW_POLICY,'review_mode':'classic','clips':rows,'feedback':copy.deepcopy(state['feedback'])}
        atomic_write_json(directory/'episode_review.json',value)
        return value

    monkeypatch.setattr(EpisodeTools,'_runner',lambda self:Runner())
    monkeypatch.setattr(EpisodeTools,'_review_evidence',lambda self,review,report,**kwargs:{'feedback':review.get('feedback',{})})
    monkeypatch.setattr('novel_manga.application.review.execution.review_saved',review)
    return ctx,EpisodeTools(ctx),events,state


def test_accepted_render_assembles_after_independent_review_and_finish_reuses_it(host):
    ctx,tools,events,_ = host
    result = tools.dispatch('render')
    assert result['ok'] and result['quality_passed']
    assert events == ['render','review','assemble']
    counts = history.generation_counts(ctx.episode_dir)
    assert counts and set(counts.values()) == {1}
    assert tools.dispatch('finish')['passed']
    assert events == ['render','review','assemble']
    assert history.generation_counts(ctx.episode_dir) == counts


@pytest.mark.parametrize('failure,allowed', [('silence_ratio',True),('long_silence',True),
    ('black_frames',True),('long_freeze',True),('resolution',False)])
def test_final_clip_technical_failure_can_reach_bounded_repair(host,failure,allowed):
    ctx,tools,events,state=host
    state['technical_passed']=False
    tools.dispatch('render')
    path=ctx.episode_dir/'thin_media_report.json';report=read_json(path)
    report['assembly']['media_qc']={'checks':{failure:{'passed':False,'detail':'actual final QC finding'}}}
    atomic_write_json(path,report)
    counts=history.generation_counts(ctx.episode_dir)
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'retake','reason':'the current clip evidence explains the final technical failure'}]})
    result=tools.dispatch('apply')
    assert ('clip_01' in result['changed']) is allowed
    if allowed:assert result['ok'] and not result['blocked']
    if not allowed:assert 'eligible failure' in result['blocked']['clip_01']
    assert history.generation_counts(ctx.episode_dir)==counts
    assert events==['render','review','assemble']


@pytest.mark.parametrize('block',['used_budget','stale_media','passed_technical_gate'])
def test_technical_repair_never_bypasses_budget_or_current_failure(host,block):
    ctx,tools,events,state=host
    state['technical_passed']=False
    tools.dispatch('render')
    path=ctx.episode_dir/'thin_media_report.json';report=read_json(path)
    report['assembly']['media_qc']={'checks':{'black_frames':{'passed':False}}}
    if block=='stale_media':report['clip_plan_fingerprint']='old plan'
    elif block=='passed_technical_gate':report['assembly']['thin_passed']=True
    else:atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'*':{'limit':1,'reason':'one actual take only'}})
    atomic_write_json(path,report)
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'retake','reason':'technical candidate'}]})
    counts=history.generation_counts(ctx.episode_dir)
    result=tools.dispatch('apply')
    assert result['changed']==[] and 'clip_01' in result['blocked']
    assert history.generation_counts(ctx.episode_dir)==counts


@pytest.mark.parametrize('verdict,technical_passed,accepted', [
    ('fine',True,True),('obvious',True,False),('fine',False,False),
])
def test_precise_finish_honors_existing_publication_gate(host,monkeypatch,verdict,technical_passed,accepted):
    ctx,tools,events,state=host
    state['technical_passed']=technical_passed
    tools.dispatch('render')
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    report=read_json(ctx.episode_dir/'thin_media_report.json')
    candidate=ctx.episode_dir/'repair_candidate'/f'{ctx.episode_dir.name}.mp4'
    candidate.parent.mkdir();candidate.write_bytes(b'current assembled candidate')
    final=ctx.episode_dir/f'{ctx.episode_dir.name}.mp4';final.write_bytes(b'previous final')
    report['assembly'].update(final_video=str(candidate),pending_publish=True)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    review=read_json(ctx.episode_dir/'episode_review.json');review['review_mode']='verify'
    for row in review['clips'].values():
        row.update(verify={'verdict':verdict},severity='pass' if verdict=='fine' else 'fail',
                   tier='optional',scripted={'evidence':'source excerpt','note':'source exception'})
    atomic_write_json(ctx.episode_dir/'episode_review.json',review)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *args:'done')
    counts=history.generation_counts(ctx.episode_dir)
    result=tools.dispatch('finish')
    if accepted:
        assert result['ok'] and result['passed']
        assert final.read_bytes()==b'current assembled candidate'
        assert (ctx.episode_dir/'repair_history/previous_final.mp4').read_bytes()==b'previous final'
        assert read_json(ctx.run_dir/'final-candidate/result.json')['assembly']['final_video']==str(final)
        assert not read_json(ctx.episode_dir/'thin_media_report.json')['assembly']['pending_publish']
    else:
        assert not result.get('passed')
        assert final.read_bytes()==b'previous final'
        assert candidate.read_bytes()==b'current assembled candidate'
        if technical_passed:
            assert result['ok'] is False and result['error']=='publication_gate'
            assert set(result['clips'])==set(review['clips'])
            assert not (ctx.work_dir/'input/latest-finish.json').exists()
            checked=[]
            def recheck(directory,cid):
                checked.append(cid)
                updated=read_json(directory/'episode_review.json')
                updated['clips'][cid].update(severity='pass',verify={'verdict':'fine'})
                atomic_write_json(directory/'episode_review.json',updated)
                return {'accepted':[cid],'changed':[]}
            monkeypatch.setattr('novel_manga.application.review.execution.recheck_current_clip',recheck)
            atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
                {'clip_id':cid,'action':'keep','reason':'check the current source and take'} for cid in result['clips']]})
            applied=tools.dispatch('apply')
            assert applied['ok'] and set(applied['accepted'])==set(review['clips'])
            assert set(checked)==set(review['clips']) and len(checked)==len(review['clips'])
            assert tools.dispatch('finish')['passed'] is True
            assert final.read_bytes()==b'current assembled candidate'
    assert history.generation_counts(ctx.episode_dir)==counts
    assert events==['render','review','assemble']


@pytest.mark.parametrize('recorded_mode', [None, 'verify'])
def test_finish_and_status_require_the_current_review_mode(host, recorded_mode, monkeypatch):
    ctx,tools,events,_=host
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    tools.dispatch('render')
    path=ctx.episode_dir/'episode_review.json';review=read_json(path)
    if recorded_mode is None:review.pop('review_mode')
    else:review['review_mode']=recorded_mode
    atomic_write_json(path,review)
    counts=history.generation_counts(ctx.episode_dir)
    assert tools.status({})['review_required'] is True
    finish=tools.dispatch('finish')
    assert finish['ok'] is False and finish['required_action']=='review'
    tools.dispatch('review')
    assert tools.dispatch('finish')['passed'] is True
    assert events==['render','review','assemble','review']
    assert history.generation_counts(ctx.episode_dir)==counts


def test_wrong_mode_cannot_finish_from_an_old_movie_when_raw_takes_are_missing(host,monkeypatch):
    ctx,tools,events,_=host
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    tools.dispatch('render')
    path=ctx.episode_dir/'episode_review.json';review=read_json(path)
    review['review_mode']='verify'
    for row in review['clips'].values():Path(row['video']).unlink()
    atomic_write_json(path,review)
    result=tools.dispatch('finish')
    assert result['ok'] is False and result['required_action']=='review'
    assert events==['render','review','assemble']


@pytest.mark.parametrize('failure',['feedback','speech_failed','review_error'])
def test_failed_render_returns_evidence_before_encoding_then_finishes_candidate_once(host,failure):
    ctx,tools,events,state = host
    state[failure] = {'clip_01':'actual unresolved picture issue'} if failure=='feedback' else ['clip_01']
    rendered = tools.dispatch('render')
    assert rendered['ok'] and not rendered['quality_passed']
    assert events == ['render','review']
    assert read_json(ctx.episode_dir/'thin_media_report.json')['assembly'] is None
    counts = history.generation_counts(ctx.episode_dir)
    finished = tools.dispatch('finish')
    assert finished['ok'] and not finished['passed']
    assert Path(finished['movie']).is_file()
    assert events == ['render','review','assemble']
    assert tools.dispatch('finish')['passed'] is False
    assert events == ['render','review','assemble']
    assert history.generation_counts(ctx.episode_dir)==counts


def test_content_pass_cannot_bypass_failed_final_technical_qc(host):
    ctx,tools,events,state = host
    state['technical_passed']=False
    result=tools.dispatch('render')
    assert result['ok'] and result['quality_passed'] is False
    assert read_json(ctx.episode_dir/'thin_media_report.json')['quality_review']['content_passed'] is True
    assert events==['render','review','assemble']
    assert tools.dispatch('finish')['passed'] is False


def test_missing_review_does_not_encode_or_deliver_unchecked_video(host):
    _,tools,events,state=host
    state['review_missing']=['clip_01']
    assert tools.dispatch('render')['quality_passed'] is False
    assert events==['render','review']
    result=tools.dispatch('finish')
    assert not result['ok'] and result['required_action']=='review'
    assert events==['render','review']


def test_finish_does_not_assemble_old_takes_under_a_changed_request(host):
    ctx,tools,events,state=host
    state['feedback']={'clip_01':'unresolved'}
    tools.dispatch('render')
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    plan['clips'][0]['prompt_h3']+=' A close-up shows the reaction.'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    result=tools.dispatch('finish')
    assert not result['ok'] and result['required_action']=='render'
    assert events==['render','review']


def test_missing_take_keeps_partial_work_without_generation_or_assembly_at_finish(host):
    ctx,tools,events,state=host
    state['missing']=['clip_01']
    result=tools.dispatch('render')
    assert result['ok'] and not result['quality_passed']
    counts=history.generation_counts(ctx.episode_dir)
    finished=tools.dispatch('finish')
    assert finished['ok'] and not finished['passed'] and finished['movie'] is None
    assert events==['render','review']
    assert history.generation_counts(ctx.episode_dir)==counts


def test_unadopted_empty_episode_is_not_reported_as_content_accepted(host):
    ctx,tools,events,_=host
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[]})
    result=tools.dispatch('finish')
    assert result['ok'] and not result['passed']
    assert read_json(ctx.run_dir/'result.json')['quality_review']['content_passed'] is False
    assert events==[]


@pytest.mark.parametrize('postmix',[False,True])
def test_finish_requires_current_take_review_after_interrupted_retake_review(host,postmix):
    ctx,tools,events,state=host
    state['technical_passed']=False
    tools.dispatch('render')
    media=read_json(ctx.episode_dir/'thin_media_report.json')
    review=read_json(ctx.episode_dir/'episode_review.json')
    selected=media['clips'][0]['selected']
    video=Path(selected['video'])
    if postmix:
        video=video.with_name('postmix.mp4');video.write_bytes(b'current mixed take')
        selected['postmix_video']=str(video)
        review['clips']['clip_01'].update(video=str(video),take=take_identity(video))
        atomic_write_json(ctx.episode_dir/'episode_review.json',review)
        atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    assert tools.status({})['review_required'] is False
    # A render can persist a replacement take before its independent review fails.
    # Its path and review policy stay the same; the actual file is a new take.
    video.write_bytes(b'new unreviewed generated take after retry')
    media.update(status='clips_ready',assembly=None)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    status=tools.status({})
    assert status['review_required'] and status['unreviewed_clips']==['clip_01']
    result=tools.dispatch('finish')
    assert not result['ok'] and result['required_action']=='review'
    assert events==['render','review','assemble']


@pytest.mark.parametrize('advisory,other_failure,expected', [
    (True,None,True),(True,'long_silence',False),(True,'black_frames',False),
    (True,'audio_level',False),(False,None,False)])
def test_finish_rechecks_existing_movie_when_book_qc_policy_changes(host, monkeypatch, advisory, other_failure, expected):
    ctx,tools,events,state=host
    state['technical_passed']=False
    tools.dispatch('render')
    path=ctx.episode_dir/'thin_media_report.json';media=read_json(path);assembly=media['assembly']
    original=Path(assembly['final_video']).read_bytes()
    assembly.update(cover=str(ctx.episode_dir/'cover.jpeg'),ending=str(ctx.episode_dir/'ending.jpeg'),
                    ass=str(ctx.episode_dir/'subtitles.ass'),silent_outro_seconds=4,subtitle_events=8)
    checks={'silence_ratio':{'passed':not advisory,'detail':{'ratio':0.6}},
            'long_silence':{'passed':True},'black_frames':{'passed':True},'audio_level':{'passed':True},
            'long_freeze':{'passed':True,'detail':{'max_freeze_seconds':0}}}
    if not advisory:checks['silence_ratio']['waived']=True
    if other_failure:checks[other_failure]['passed']=False
    assembly['media_qc']={'passed':not advisory,'checks':checks}
    atomic_write_json(path,media)
    atomic_write_json(ctx.novel_dir/'profile.json',{'frame':'16:9','qc_ignore':['silence_ratio'] if advisory else []})
    calls=[]
    def inspect(video, cover, ending, ass, settings, report, **kwargs):
        calls.append(kwargs)
        assert (settings.width,settings.height,settings.fps)==(1920,1080,25)
        result=copy.deepcopy(checks)
        result['silence_ratio'].pop('waived',None)
        result['silence_ratio']['passed']=False
        for key in kwargs['ignore_checks']:
            result[key].update(passed=True,waived=True)
        if other_failure:result[other_failure]['passed']=False
        return {'passed':all(v['passed'] for v in result.values()),'checks':result}
    monkeypatch.setattr('novel_manga.qc.inspect_media',inspect)
    monkeypatch.setattr(EpisodeTools,'_runner',lambda *a:pytest.fail('QC re-evaluation cannot render/re-encode'))
    before_counts=history.generation_counts(ctx.episode_dir)
    result=tools.dispatch('finish')
    assert result['ok'] and result['passed'] is expected
    assert len(calls)==(0 if advisory else 1)
    if calls:assert calls[0]['ignore_checks']==()
    assert Path(assembly['final_video']).read_bytes()==original
    assert history.generation_counts(ctx.episode_dir)==before_counts
    assert read_json(path)['assembly']['media_qc']['checks']['silence_ratio']['detail']['ratio']==0.6
    if advisory and not other_failure:
        assert read_json(path)['assembly']['media_qc']['checks']['silence_ratio']['measured_passed'] is False
        atomic_write_json(ctx.novel_dir/'profile.json',{'frame':'16:9','qc_ignore':[]})
        assert tools.dispatch('finish')['passed'] is False
        assert calls==[]  # the original measurement survives turning the advisory back off
