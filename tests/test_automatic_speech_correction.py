import json
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.rendering import flow
from novel_manga.application.rendering import h3
from novel_manga.application.profiles import h3_stamp, media_qc_ignores, speech_gate_result
from novel_manga.media.retries import RetryState, after_analysis
from support.render_context import uninitialized_runner


def test_extra_speech_requests_a_correction_within_the_take_limit():
    failed = {'passed': False, 'issues': ['excess_unplanned_speech']}
    step = after_analysis(RetryState(1, 2), failed, generated=True, free_retries=True, local_h3=True)
    assert (step.action, step.attempt) == ('correct_request', 2)
    assert after_analysis(RetryState(2, 2), failed, generated=True, free_retries=True, local_h3=True).action == 'stop'


def test_correction_runs_before_second_take_and_does_not_add_a_third(tmp_path, monkeypatch):
    runner = uninitialized_runner(); ctx = runner.context
    ctx.episode_dir = tmp_path / 'book' / 'book_1'; ctx.episode_dir.mkdir(parents=True)
    ctx.novel_dir = ctx.episode_dir.parent; ctx.work = ctx.episode_dir / 'work'
    ctx.settings = SimpleNamespace(local_h3_base_url='pool'); ctx.max_attempts = 2
    ctx.free_retries = True
    order = []
    def generate(clip, attempt):
        order.append(('generate', attempt)); clip['_generated'] = True
        return tmp_path / f'take{attempt}.mp4'
    monkeypatch.setattr(runner, 'generate_clip', generate)
    monkeypatch.setattr(runner, 'analyse_clip', lambda clip, video: {
        'video': str(video), 'passed': False, 'cer': 1.2, 'max_volume_db': -2,
        'issues': ['excess_unplanned_speech']})
    monkeypatch.setattr(runner, 'correct_speech_request', lambda *args: order.append(('correct', 1)) or True)
    monkeypatch.setattr(flow, 'record_privacy_ok', lambda *args: None)
    result = runner.process_clip({'clip_id': 'clip_13', 'kind': 'video', 'references': []})
    assert order == [('generate', 1), ('correct', 1), ('generate', 2)]
    assert len(result['attempts']) == 2 and not result['selected']['passed']


def test_new_english_correction_preserves_visual_note_and_is_written(tmp_path, monkeypatch):
    runner = uninitialized_runner(); ctx = runner.context
    ctx.episode_dir = tmp_path; ctx.feedback = {'c': '只出现一个人。'}
    clip = {'clip_id': 'c', 'kind': 'video', 'prompt': '本镜', 'prompt_h3': 'old English',
            'request_seconds': 5, 'references': [], 'spoken_text': '不然我就让佩珀炒了你。'}
    ctx.clip_plan = {'clips': [clip]}
    def translate(candidate, *, note):
        assert '只出现一个人' in note and '说话前及说完后' in note
        candidate['prompt_h3'] = 'The subject starts the bound line immediately and then stays silent.'
        candidate['prompt_h3_of'] = h3_stamp(candidate, note)
        return True
    monkeypatch.setattr(h3, 'convert', translate)
    assert runner.correct_speech_request(clip, {'passed': False, 'issues': ['excess_unplanned_speech']})
    assert json.loads((tmp_path / 'review_feedback.json').read_text()) == ctx.feedback
    assert json.loads((tmp_path / 'clip_plan.json').read_text())['clips'][0]['prompt_h3'] == clip['prompt_h3']
    assert not runner.correct_speech_request(clip, {'passed': False, 'issues': ['excess_unplanned_speech']})


def test_silence_report_only_does_not_disable_extra_or_missing_speech(tmp_path):
    (tmp_path / 'profile.json').write_text(json.dumps({'speech_gate': 'enforce',
        'qc_ignore': ['silence_ratio', 'long_silence']}))
    assert set(media_qc_ignores(tmp_path)) == {'silence_ratio', 'long_silence'}
    for issue in ['excess_unplanned_speech', 'missing_0.9_over_0.5']:
        row = speech_gate_result(tmp_path, {'passed': False, 'issues': [issue]})
        assert not row['passed'] and row['issues'] == [issue]
