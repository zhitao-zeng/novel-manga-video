from novel_manga.application.agents.episode.media_operations import MediaOperations
"""Existing script integration: only final requests cross the agent boundary."""
import copy

import pytest

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.profiles import h3_stamp
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


def _retained_asr_assembly(tmp_path, monkeypatch, cache_state='current'):
    """Use the real analyser and subtitle boundary; only encoding/ASR service are fakes."""
    from pathlib import Path
    from types import SimpleNamespace
    from novel_manga.config import Settings
    from novel_manga.application.rendering.flow import ThinMediaRunner
    from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
    from novel_manga.media import analysis, postprocess, speech
    from novel_manga.media.context import RenderContext

    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    clip = plan['clips'][0]
    plan['clips'] = [clip]
    if cache_state in {'silent', 'speech_failed'}:
        clip.update(spoken_text='', lines=[], dialogue_bindings=[])
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    video = ctx.episode_dir/'work/clips/clip_01/attempt_02/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'the already selected take')
    (video.parent/'native.wav').write_bytes(b'existing complete audio')
    foreign = video.parent.parent/'attempt_01'
    foreign.mkdir()
    atomic_write_json(foreign/'asr.json', {'chunks': [{'start':0, 'end':1, 'hypothesis':'另一take的台词'}]})
    words = clip['spoken_text']
    chunks = [] if cache_state == 'silent' else [
        {'start':0.5, 'end':2.5, 'hypothesis':words, 'raw_hypothesis':words}]
    cached = {'clip_id':'old_clip_id', 'video':str(foreign/'clip.mp4'),
              'duration':15.0, **speech.evaluate(words, chunks, -20, -4)}
    if cache_state == 'stale_reference':
        cached.update(reference='old script wording', speech_evaluation_policy=-1,
                      passed=False, issues=['missing_dialogue'])
    if cache_state == 'speech_failed':
        cached.update(passed=False, issues=['excess_unplanned_speech'])
    if cache_state != 'missing':
        atomic_write_json(video.parent/'asr.json', cached)

    runner = object.__new__(ThinMediaRunner)
    runner.context = RenderContext(episode_dir=ctx.episode_dir, novel_dir=ctx.novel_dir,
        work=ctx.episode_dir/'work', settings=Settings(), clip_plan=plan,
        script=read_json(ctx.episode_dir/'chapter_script.json'),
        asr_python='fixture-asr-python', asr_helper=tmp_path/'fixture-asr-helper.py',
        renderer=SimpleNamespace(mux_visual_group=lambda video, wav, target:(video, 15.0)))
    monkeypatch.setattr(analysis, 'media_duration', lambda path:15.0)
    monkeypatch.setattr(analysis, 'audio_levels', lambda wav:(-20, -4))
    monkeypatch.setattr(analysis, 'speech_chunks', lambda wav:[[0.5, 2.5]])
    asr_calls, analysed, subtitles = [], [], []

    def transcribe(command, **kwargs):
        assert command[0] == 'fixture-asr-python'
        asr_calls.append(command)
        atomic_write_json(Path(command[-1]), {'segments':chunks})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(analysis.subprocess, 'run', transcribe)
    original_analyse = runner.analyse_clip

    def analyse(current, selected):
        analysed.append(selected)
        return original_analyse(current, selected)

    runner.analyse_clip = analyse
    runner.generate_clip = lambda *a, **kw:pytest.fail('assembly generated a new video')
    runner.run = lambda *a, **kw:pytest.fail('assembly restarted rendering')

    def encode(records):
        # story_segments really calls subtitle_events(..., selected), including chunks access.
        segments = postprocess.story_segments(runner.context, records)
        subtitles.extend(event for segment in segments for event in segment['subtitle_events'])
        return {'thin_passed':True, 'subtitle_events':len(subtitles)}

    runner.assemble = encode
    monkeypatch.setattr(MediaOperations, 'runner', lambda self:runner)
    history.save(ctx.episode_dir, {'trials':[], 'prior_generation_counts':{'clips':{'clip_01':2}}})
    report = {'status':'clips_ready', 'clip_plan_fingerprint':plan_fingerprint(plan),
              'prompt_h3_fingerprint':h3_prompt_fingerprint(plan), 'review_feedback':{},
              'clips':[{'clip_id':'clip_01', 'attempts':[], 'selected':{
                  'video':str(video), 'passed':True, 'duration':15, 'generated_this_run':False}}],
              'failed_clips':[], 'gate_failed_clips':[], 'assembly':None}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', report)
    return ctx, report, video, analysed, asr_calls, subtitles, cached


@pytest.mark.parametrize('cache_state', ['current', 'stale_reference', 'missing', 'silent'])
def test_assembly_restores_selected_take_asr_at_real_subtitle_boundary(tmp_path, monkeypatch, cache_state):
    ctx, report, video, analysed, asr_calls, subtitles, _ = _retained_asr_assembly(
        tmp_path, monkeypatch, cache_state)
    counts = history.generation_counts(ctx.episode_dir)
    movie = video.read_bytes()
    result = EpisodeTools(ctx).media.assemble(report)
    selected = result['clips'][0]['selected']
    assert analysed == [video]
    assert len(asr_calls) == (1 if cache_state == 'missing' else 0)
    assert selected['video'] == str(video) and selected['clip_id'] == 'clip_01'
    assert selected['generated_this_run'] is False
    assert selected['passed'] and result['gate_failed_clips'] == []
    assert selected['reference'] == read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['spoken_text']
    if cache_state == 'silent':
        assert selected['chunks'] == [] and subtitles == []
    else:
        assert subtitles and all(s['subtitle_source'] == 'native_audio_asr' for s in subtitles)
        assert all('另一take' not in s['text'] for s in subtitles)
    assert 'chunks' in read_json(ctx.episode_dir/'thin_media_report.json')['clips'][0]['selected']
    assert video.read_bytes() == movie and history.generation_counts(ctx.episode_dir) == counts


def test_assembly_keeps_complete_analysis_without_repeating_asr(tmp_path, monkeypatch):
    ctx, report, video, analysed, asr_calls, subtitles, cached = _retained_asr_assembly(tmp_path, monkeypatch)
    report['clips'][0]['selected'].update(cached, video=str(video), clip_id='clip_01')
    EpisodeTools(ctx).media.assemble(report)
    assert analysed == [] and asr_calls == [] and subtitles


def test_restored_asr_failure_still_blocks_content_acceptance(tmp_path, monkeypatch):
    from novel_manga.application.review.execution import save_result
    ctx, report, video, _, _, _, _ = _retained_asr_assembly(tmp_path, monkeypatch, 'speech_failed')
    counts = history.generation_counts(ctx.episode_dir)
    result = EpisodeTools(ctx).media.assemble(report)
    assert result['clips'][0]['selected']['passed'] is False
    assert result['gate_failed_clips'] == ['clip_01']
    accepted = save_result(ctx.episode_dir, result, {'clips':{'clip_01':{'severity':'pass'}}, 'feedback':{}})
    assert accepted['quality_review']['passed'] is False
    assert history.generation_counts(ctx.episode_dir) == counts and video.is_file()


def test_adopt_validates_and_saves_without_rendering_or_spending_a_take(tmp_path, monkeypatch):
    ctx, before, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(MediaOperations, 'runner', lambda *a: pytest.fail('adoption started video'))
    result = EpisodeTools(ctx).dispatch('adopt', {})
    assert result['ok'] and result['adopted']
    assert read_json(ctx.run_dir/'prepared.json')['ready']
    assert history.generation_counts(ctx.episode_dir) == {}
    assert not (ctx.episode_dir/'thin_media_report.json').exists()
    assert [c['references'] for c in read_json(ctx.episode_dir/'clip_plan.json')['clips']] == [c['references'] for c in before['clips']]


def test_adopt_cannot_reset_existing_physical_history(tmp_path, monkeypatch):
    ctx, before, _ = prepared(tmp_path, monkeypatch)
    history.save(ctx.episode_dir, {'trials': [], 'prior_generation_counts': {'clips': {'clip_01': 1}}})
    result = EpisodeTools(ctx).dispatch('adopt', {})
    assert result['ok'] is False and 'main video has started' in result['error']
    assert read_json(ctx.episode_dir/'clip_plan.json') == before
    assert history.generation_counts(ctx.episode_dir) == {'clip_01': 1}


def test_host_supplied_prior_counts_are_imported_after_valid_adoption_with_one_pending_take(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.run_dir/'prior-generation-counts.json', {
        'clips': {'clip_01':2}, 'authorization':'approved-safe-replacement',
        'reason':'One authorized replacement from the revised source draft'})
    assert history.generation_counts(ctx.episode_dir) == {}
    result = EpisodeTools(ctx).dispatch('adopt', {})
    assert result['ok'] and result['generation_counts']['clip_01'] == 2
    assert history.generation_limit(ctx.episode_dir,'clip_01') == 2
    assert history.preparation_limit(ctx.episode_dir,'clip_01') == 3
    assert history.conditional_extra(ctx.episode_dir,'clip_01')['prepared'] is True
    assert history.generation_limit(ctx.episode_dir,'clip_02') == 0
    assert history.preparation_limit(ctx.episode_dir,'clip_02') == 1
    assert history.activate_extra_take(ctx.episode_dir,'clip_01')
    assert history.generation_limit(ctx.episode_dir,'clip_01') == 3
    assert EpisodeTools(ctx).dispatch('adopt', {})['ok'] is False


def test_malformed_h3_repair_reports_authored_shot_shape_without_publishing(tmp_path,monkeypatch):
    ctx,before,_=prepared(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'feedback':{'clip_01':'needs repair'}})
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[{
        'clip_id':'clip_01','action':'edit','reason':'repair current evidence',
        'h3':{'shots':['First direction body.','Second direction body.'],'soundscape':'Wind.'}}]})
    result=EpisodeTools(ctx).dispatch('apply')
    assert result['ok'] is False
    message=result['argument_errors']['clip_01']
    assert 'source shot_indexes=[1]' in message and 'received=2' in message
    assert read_json(ctx.episode_dir/'clip_plan.json')==before
    assert history.generation_counts(ctx.episode_dir)=={}


def test_existing_draft_reuses_good_requests_and_identifies_only_missing_or_broken_clips(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.inputs import _request_draft
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    (ctx.work_dir/'output/requests.json').unlink()
    plan['clips'][0]['prompt_h3'] += '\nNo other people appear.'
    plan['clips'][1].pop('prompt_h3')
    plan['clips'][2]['prompt_h3'] = plan['clips'][2]['prompt_h3'].replace('你来啦。', '你回去。')
    before = copy.deepcopy(plan)
    info = _request_draft(ctx, plan)
    output = read_json(ctx.work_dir/info['file'])['prompts']
    assert plan == before
    assert output['clip_01'] == plan['clips'][0]['prompt_h3']
    assert output['clip_02'] == ''
    assert set(info['needs_work']) == {'clip_02', 'clip_03'}
    assert len(info['reusable']) == 6 and 'clip_01' in info['reusable']
    assert info['style_warnings']['clip_01'] == ['Write affirmative visible facts.']


def test_preparing_a_draft_preserves_agent_edits_and_reports_style_as_advice(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.inputs import _request_draft
    ctx, plan, prompts = prepared(tmp_path, monkeypatch)
    prompts['clip_01'] += '\nNo other people appear.'
    atomic_write_json(ctx.work_dir/'output/requests.json', {'prompts': prompts})
    before = (ctx.work_dir/'output/requests.json').read_bytes()
    info = _request_draft(ctx, plan)
    assert not info['needs_work'] and len(info['reusable']) == 8
    assert (ctx.work_dir/'output/requests.json').read_bytes() == before
    checked = EpisodeTools(ctx).dispatch('check')
    assert checked['valid'] is True
    assert checked['style_warnings']['clip_01'] == ['Write affirmative visible facts.']


def test_missing_requests_use_one_bounded_parallel_draft_and_preserve_the_plan(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.inputs import _request_draft
    from novel_manga.application.agents.episode.flow import _fill_missing_requests
    ctx, plan, prompts = prepared(tmp_path, monkeypatch)
    for clip in plan['clips'][:2]:
        clip.pop('prompt_h3')
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    (ctx.work_dir/'output/requests.json').unlink()
    info = _request_draft(ctx, plan)
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'request_draft': info})
    before = (ctx.episode_dir/'clip_plan.json').read_bytes()
    calls = []

    def draft(clip, *, tries, episode_dir, problems):
        calls.append((clip['clip_id'], tries, episode_dir))
        if clip['clip_id'] == 'clip_02':
            problems.append('reference outfit conflicts with the source')
            return False
        clip['prompt_h3'] = prompts[clip['clip_id']]
        return True

    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', draft)
    _fill_missing_requests(ctx)
    _fill_missing_requests(ctx)  # an unsuccessful first draft is handed to the Agent, not retried here
    assert sorted(calls) == [('clip_01', 1, ctx.episode_dir), ('clip_02', 1, ctx.episode_dir)]
    output = read_json(ctx.work_dir/'output/requests.json')['prompts']
    assert output['clip_01'] == prompts['clip_01'] and output['clip_02'] == ''
    assert output['clip_03'] == plan['clips'][2]['prompt_h3']
    assert (ctx.episode_dir/'clip_plan.json').read_bytes() == before
    pending = read_json(ctx.work_dir/'input/manifest.json')['request_draft']
    assert pending['needs_work'] == {'clip_02': 'reference outfit conflicts with the source'}
    assert 'clip_01' in pending['reusable']
    assert not (ctx.episode_dir/'repair_history').exists()


def test_existing_script_first_requests_preserve_all_prepared_material(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch, old_english=False)
    script_bytes = (ctx.episode_dir / 'chapter_script.json').read_bytes()
    source_bytes = {p.name: p.read_bytes() for p in ctx.source_episode_dir.iterdir()}
    tools = EpisodeTools(ctx)
    checked = tools.dispatch('check')
    assert checked['valid'] is True and checked['clip_count'] == 8
    assert checked['requested_seconds'] == 120
    assert read_json(ctx.episode_dir / 'clip_plan.json') == before

    rendered = tools.dispatch('render')
    assert rendered['ok'] is True and rendered['status'] == 'assembled'
    after = read_json(ctx.episode_dir / 'clip_plan.json')
    expected = copy.deepcopy(before)
    for clip in expected['clips']:
        clip['prompt_h3'] = prompts[clip['clip_id']]
        clip['prompt_h3_of'] = h3_stamp(clip)
    assert after == expected
    assert (ctx.episode_dir / 'chapter_script.json').read_bytes() == script_bytes
    assert {p.name: p.read_bytes() for p in ctx.source_episode_dir.iterdir()} == source_bytes
    assert read_json(ctx.episode_dir / 'review_feedback.json') == {}
    assert read_json(ctx.run_dir / 'prepared.json')['input_mode'] == 'script'
    assert history.generation_counts(ctx.episode_dir) == {}


def test_existing_requests_reject_missing_clips_and_changed_frozen_bindings(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    candidate = dict(prompts)
    mutations = [
        {cid: text for cid, text in candidate.items() if cid != 'clip_01'},
        {**candidate, 'clip_01': candidate['clip_01'].replace('你来啦。', '你回去。')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('<Subject 2> is the person shown in <Picture 3>.',
                                                            '<Subject 2> is the person shown in <Picture 1>.')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('Planned duration: 15 seconds.',
                                                            'Planned duration: 14 seconds.')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('from <Audio 1>.', 'from <Audio 2>.')},
    ]
    for changed in mutations:
        atomic_write_json(ctx.work_dir / 'output/requests.json', {'prompts': changed})
        answer = EpisodeTools(ctx).dispatch('render', {'file': 'output/requests.json'})
        assert answer['ok'] is False and answer['valid'] is False and answer['errors']
        assert read_json(ctx.episode_dir / 'clip_plan.json') == before
    assert not (ctx.run_dir / 'prepared.json').exists()
    assert not (ctx.episode_dir / 'repair_history').exists()


def test_render_runs_independent_acceptance_and_delivers_its_current_evidence(tmp_path, monkeypatch):
    from novel_manga.application.repair import agent
    from novel_manga.review.contracts import REVIEW_POLICY
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    reviews = []

    def independent_review(directory, *, fresh):
        assert fresh is False
        reviews.append(directory)
        return read_json(directory/'episode_review.json')

    monkeypatch.setattr('novel_manga.application.review.execution.review_saved', independent_review)

    class Runner:
        def __init__(self):
            from types import SimpleNamespace
            self.context = SimpleNamespace(episode_dir=ctx.episode_dir)

        def run(self, *, assemble=True):
            from novel_manga.review.storage import take_identity
            video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
            video.parent.mkdir(parents=True)
            video.write_bytes(b'current selected take')
            atomic_write_json(video.parent/'asr_raw.json', {'hypothesis': '原始识别'})
            atomic_write_json(ctx.episode_dir/'episode_review.json', {
                'policy': REVIEW_POLICY, 'review_mode':'classic', 'clips': {'clip_01': {'severity': 'fail',
                    'video': str(video), 'take': take_identity(video)}},
                'feedback': {'clip_01': 'inspect the final action'}})
            return {'status': 'assembled', 'clips': [{'clip_id': 'clip_01', 'selected': {
                'video': str(video), 'passed': False, 'hypothesis': '修正识别'}}],
                'quality_review': {'passed': False}, 'assembly': {'thin_passed': True}}

    monkeypatch.setattr(MediaOperations, 'runner', lambda self: Runner())

    def frames(episode_dir, cid, video, destination):
        assert video.read_bytes() == b'current selected take'
        destination.mkdir(parents=True)
        frame = destination/'frame_04.jpg'
        frame.write_bytes(b'late frame')
        atomic_write_json(destination/'frame-evidence.json', {'frames': [
            {'path': str(frame), 'timestamp_seconds': 13.5}]})
        return [frame], 'current take'

    monkeypatch.setattr(agent, 'provided_frames', frames)
    rendered = EpisodeTools(ctx).dispatch('render')
    assert reviews == [ctx.episode_dir]
    assert rendered['ok'] is True and rendered['quality_passed'] is False
    assert rendered['review']['review_required'] is False
    assert rendered['review']['feedback'] == {'clip_01': 'inspect the final action'}
    evidence = read_json(ctx.work_dir/rendered['review']['evidence'])
    target = evidence['targets'][0]
    assert target['verdict']['severity'] == 'fail'
    assert target['frames'][0].endswith('frame_04.jpg')
    assert target['frame_evidence']['frames'][0]['timestamp_seconds'] == 13.5
    assert read_json(ctx.work_dir/target['asr_files'][0])['hypothesis'] == '原始识别'
    assert read_json(ctx.work_dir/'input/latest-render.json')['quality_review']['passed'] is False


def test_existing_media_requires_protected_repair_and_preserves_budget(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    video = ctx.episode_dir / 'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'existing take')
    tools = EpisodeTools(ctx)
    refused = tools.dispatch('render', {'file': 'output/requests.json'})
    assert refused['ok'] is False and 'apply repair decisions' in refused['error']
    atomic_write_json(ctx.episode_dir / 'episode_review.json', {'feedback': {'clip_01': 'current failure'}})
    atomic_write_json(ctx.episode_dir / 'thin_media_report.json', {})
    monkeypatch.setattr('novel_manga.application.repair.publication.publish_candidate',
                        lambda *a, **k: pytest.fail('a changed protected line must not be published'))
    repair = {'decisions': [{'clip_id': 'clip_01', 'action': 'edit', 'reason': 'correct the take',
                            'prompt_h3': prompts['clip_01'].replace('你来啦。', '你回去。')}]}
    atomic_write_json(ctx.work_dir / 'output/repair.json', repair)
    failed = tools.dispatch('apply')
    assert failed['ok'] is False and failed['changed'] == [] and failed['blocked']['clip_01']
    monkeypatch.setattr(history, 'generation_counts', lambda directory: {'clip_01': 3})
    atomic_write_json(ctx.work_dir / 'output/repair.json', {
        'decisions': [{'clip_id': 'clip_01', 'action': 'retake', 'reason': 'another take'}]})
    exhausted = tools.dispatch('apply')
    assert exhausted['ok'] is False and exhausted['blocked'] == {'clip_01': 'effective clip retry budget used'}
    assert exhausted['generation_counts'] == {'clip_01': 3}
    assert read_json(ctx.episode_dir / 'clip_plan.json') == before
    assert read_json(ctx.episode_dir / 'chapter_script.json') == read_json(ctx.source_episode_dir / 'chapter_script.json')


def test_one_exhausted_clip_does_not_discard_other_eligible_repairs(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'episode_review.json', {
        'feedback': {'clip_01': 'old failure', 'clip_02': 'fix grip'}})
    monkeypatch.setattr(history, 'generation_counts', lambda directory: {'clip_01': 2, 'clip_02': 1})
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'limit': 2, 'reason': 'bounded production'}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [
        {'clip_id': 'clip_01', 'action': 'retake', 'reason': 'quota already used'},
        {'clip_id': 'clip_02', 'action': 'edit', 'reason': 'correct grip', 'prompt_h3': prompts['clip_02']} ]})
    result = EpisodeTools(ctx).dispatch('apply')
    assert result['changed'] == ['clip_02']
    assert result['blocked'] == {'clip_01': 'effective clip retry budget used'}
    after = read_json(ctx.episode_dir/'clip_plan.json')
    assert after['clips'][0] == before['clips'][0]
    assert after['clips'][1]['prompt_h3'] == prompts['clip_02']


def test_saved_protocol_failure_resumes_only_the_still_current_repair(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.flow import _pending_structured_repairs
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    row = {'clip_id': 'clip_01', 'action': 'edit', 'reason': 'fix the grip',
           'h3': {'shots': ['<Subject 2> holds the cup firmly. <DIALOGUE_1> The cup settles.']}}
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [row]})
    atomic_write_json(ctx.run_dir/'tool-repair-decisions.json', [{'blocked': {'clip_01': "'prompt_h3'"}}])
    atomic_write_json(ctx.work_dir/'input/latest-review.json', {'targets': [{'clip': plan['clips'][0]}]})
    assert _pending_structured_repairs(ctx)
    assert read_json(ctx.work_dir/'output/resume-structured-repairs.json')['decisions'] == [row]
    plan['clips'][0]['dialogue_bindings'][0]['text'] = '这已经是另一个版本。'
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    assert not _pending_structured_repairs(ctx)


def test_existing_requests_accept_compiled_punctuation_without_changing_words(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    prompts['clip_01'] = prompts['clip_01'].replace('你来啦。', '你来啦.')
    atomic_write_json(ctx.work_dir / 'output/requests.json', {'prompts': prompts})
    assert EpisodeTools(ctx).dispatch('check')['valid'] is True
    assert read_json(ctx.episode_dir / 'clip_plan.json') == before


def test_existing_requests_still_reject_changed_question_or_words(tmp_path, monkeypatch):
    ctx, _, prompts = prepared(tmp_path, monkeypatch)
    for changed in ('你来啦？', '你回去。'):
        atomic_write_json(ctx.work_dir / 'output/requests.json', {
            'prompts': {**prompts, 'clip_01': prompts['clip_01'].replace('你来啦。', changed)}})
        assert EpisodeTools(ctx).dispatch('check')['valid'] is False


def test_existing_request_accepts_the_compiler_added_terminal_period(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    before['clips'][0]['dialogue_bindings'][0]['text'] = '你来啦'
    atomic_write_json(ctx.episode_dir/'clip_plan.json', before)
    prompts['clip_01'] = prompts['clip_01'].replace('你来啦。', '你来啦.')
    atomic_write_json(ctx.work_dir/'output/requests.json', {'prompts': prompts})
    assert EpisodeTools(ctx).dispatch('check')['valid'] is True


def test_repair_of_legacy_request_uses_stored_timing_without_requiring_new_headers(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.contracts import validate_patch
    ctx, before, _ = prepared(tmp_path, monkeypatch)
    clip = before['clips'][0]
    clip['prompt_h3'] = clip['prompt_h3'].replace('Planned duration: 15 seconds. ', '')
    changed = clip['prompt_h3'].replace('relaxed grip', 'firm grip')
    validate_patch(clip, changed)
    with pytest.raises(ValueError):
        validate_patch(clip, changed.replace('你来啦。', '你回去。'))
    with pytest.raises(ValueError):
        validate_patch(clip, changed.replace('[Shot 1]', '[Shot 1] Planned duration: 14 seconds.'))


def test_finish_requires_current_review_of_existing_video_without_spending_a_take(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'existing video')
    atomic_write_json(ctx.episode_dir/'episode_review.json', {
        'policy': 'old-review-policy', 'clips': {'clip_01': {'video': str(video), 'severity': 'fail'}}})
    monkeypatch.setattr('novel_manga.application.review.execution.save_result',
                        lambda *a, **k: pytest.fail('stale review must not finish'))
    before = history.generation_counts(ctx.episode_dir)
    tools = EpisodeTools(ctx)
    assert tools.dispatch('status')['review_required'] is True
    answer = tools.dispatch('finish')
    assert answer['error'] == 'stale_review' and answer['required_action'] == 'review'
    assert history.generation_counts(ctx.episode_dir) == before
    assert not (ctx.work_dir/'input/latest-finish.json').exists()


def test_missing_first_review_is_exposed_before_an_episode_can_finish(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'generated take')
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'clips': [{'clip_id': 'clip_01', 'selected': {'video': str(video)}}],
        'assembly': {'thin_passed': True}})
    tools = EpisodeTools(ctx)
    status = tools.dispatch('status')
    assert status['review_required'] is True and status['unreviewed_clips'] == ['clip_01']
    result = tools.dispatch('finish')
    assert result['error'] == 'missing_review' and result['required_action'] == 'review'
    assert not (ctx.work_dir/'input/latest-finish.json').exists()


def test_common_recheck_cap_prevents_repeated_independent_review(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True); video.write_bytes(b'current take')
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {'clips': [
        {'clip_id': 'clip_01', 'selected': {'video': str(video), 'passed': False}}]})
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'*': {'limit': 2, 'recheck_limit': 1, 'reason': 'advance'}})
    atomic_write_json(ctx.episode_dir/'episode_review.json', {'feedback': {'clip_01': 'disputed'}})
    atomic_write_json(ctx.work_dir/'output/repair.json', {'decisions': [
        {'clip_id': 'clip_01', 'action': 'keep', 'reason': 'check current evidence'}]})
    calls = []
    monkeypatch.setattr('novel_manga.application.review.execution.recheck_current_clip',
                        lambda *a: calls.append(1) or {'accepted': []})
    tools = EpisodeTools(ctx)
    first = tools.dispatch('apply')
    assert first['keep_checks'] == ['clip_01']
    second = tools.dispatch('apply')
    assert 'allowance used' in second['blocked']['clip_01']
    assert len(calls) == 1
