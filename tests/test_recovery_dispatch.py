from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
import novel_manga.story.dialogue as story_dialogue
import novel_manga.application.packing.service as packing_service
import novel_manga.application.repair.manager_dispatch as repair_manager_dispatch
import novel_manga.application.repair.manager_workers as repair_manager_workers

from support.render_context import uninitialized_runner
import json
from pathlib import Path
import subprocess

import novel_manga.repair.scheduling as schedule_rules
import novel_manga.application.repair.manager_flow as repair_manager_flow
import novel_manga.application.repair.manager_workers as repair_manager_workers
import novel_manga.application.repair.recovery as recovery


def info(**kw):
    return dict(status='done', ready=True, bad=0, held=False, can_fill=False,
                unverified=0, flash_pending=0, deliverable=False, **kw)


def test_recovery_takes_ownership_and_shares_existing_worker_limits(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / 'book', tmp_path / 'old')
    m.state['phase'] = 2
    for n in range(1, 31):
        d = m.novel / f'book_{n}'
        d.mkdir(parents=True)
        (d / 'chapter_script.json').write_text('{}')
        j = m.add('repair', [n], step=2)
        j['status'] = 'waiting_plan'
        m.info[n] = {**info(), 'status': 'plan_blocked', 'ready': False, 'plan_blocked': True}
    m.info[40] = {**info(), 'status': 'done_with_warnings'}
    m.info[41] = {**info(), 'bad': 1}
    m.state['passes']['41'] = 2
    repair_manager_dispatch.schedule(m)
    jobs = [j for j in m.state['jobs'] if j['kind'] == 'recovery']
    assert len(jobs) == 24
    assert {j['recovery_kind'] for j in jobs} == {'plan', 'technical', 'residual'}
    assert len(schedule_rules.active_episodes(m.state)) == 32
    assert schedule_rules.stage_slots(jobs[0]) == ('repair_model', 1)
    jobs[0]['step'] = 1
    assert schedule_rules.stage_slots(jobs[0]) == ('repair_render', 1)
    repair_manager_dispatch.schedule(m)
    assert len([j for j in m.state['jobs'] if j['kind'] == 'recovery']) == 24


def test_completed_recovery_is_not_scheduled_again_for_same_failure(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / 'book', tmp_path / 'old')
    m.info[1] = {**info(), 'status': 'done_with_warnings'}
    repair_manager_dispatch.schedule(m)
    job = m.state['jobs'][0]
    assert job['kind'] == 'recovery'
    job['step'] = 3
    repair_manager_workers.finish(m, job)
    repair_manager_dispatch.schedule(m)
    assert len(m.state['jobs']) == 1
    assert not m.state['passes']


def test_missing_expression_recovery_precedes_new_targets_and_transfers_owner(tmp_path,monkeypatch):
    monkeypatch.setattr(schedule_rules,'REPAIR_EPISODES',2)
    m=repair_manager_flow.Manager(tmp_path/'book',tmp_path/'old')
    old=m.add('fill',[1]);old['status']='waiting_plan'
    m.info[1]={**info(),'status':'plan_blocked','plan_blocked':True,'ready':False}
    m.state['plan_queue']={'1':{'clip_01':['asset: missing required image characters/c/expressions.jpeg']}}
    m.state['targeted_recovery']=[{'episode':n,'method':'managed'} for n in [2,3]]
    repair_manager_dispatch.schedule_recovery(m)
    jobs=[j for j in m.state['jobs'] if j['status']=='pending']
    assert old['status']=='superseded' and len(jobs)==2
    assert jobs[0]['episodes']==[1] and jobs[0]['recovery_kind']=='references' and jobs[0]['replaces']==old['id']
    assert jobs[1]['episodes']==[2]
    repair_manager_dispatch.schedule_recovery(m)
    assert len(m.state['jobs'])==3


def test_recovery_no_change_stops_without_three_model_retries(tmp_path, monkeypatch):
    m = repair_manager_flow.Manager(tmp_path / 'book', tmp_path / 'old')
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({'returncode': 4}))
    j = m.add('recovery', [1], pid=123, result=str(result), recovery_kind='residual')
    monkeypatch.setattr(repair_manager_workers, 'alive', lambda pid: False)
    repair_manager_workers.reap(m)
    assert j['status'] == 'needs_attention' and j['failures'] == 0


def test_technical_failure_only_changes_failed_clip_and_retains_story_instruction(tmp_path, monkeypatch):
    d = tmp_path / 'book_1'
    d.mkdir()
    (d / 'thin_media_report.json').write_text(json.dumps({'gate_failed_clips': ['clip_02']}))
    (d / 'clip_plan.json').write_text(json.dumps({'clips': [{'clip_id': 'clip_02', 'lines': []}]}))
    (d / 'review_feedback.json').write_text(json.dumps({'clip_01': 'keep', 'clip_02': 'correct actor'}))
    import novel_manga.application.repair.history as repair_history
    monkeypatch.setattr(repair_history, 'begin_trial', lambda *a, **k: 1)
    result = recovery.prepare_technical(d)
    notes = json.loads((d / 'review_feedback.json').read_text())
    assert result['changed'] == ['clip_02']
    assert notes['clip_01'] == 'keep' and notes['clip_02'].startswith('correct actor\n')


def test_black_detector_locates_actual_black_media(tmp_path):
    video = tmp_path / 'black.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=25',
                    '-t', '1.5', '-c:v', 'libx264', '-threads', '1', str(video)], check=True)
    ranges = recovery.black_ranges(video)
    assert ranges and ranges[0][1] - ranges[0][0] > 1


def test_black_take_cannot_pass_on_cached_speech_result(tmp_path, monkeypatch):
    import novel_manga.application.rendering.flow as renderer
    runner = uninitialized_runner()
    runner.context.black_checks = {'clip_02'}
    runner.context.episode_dir = tmp_path
    monkeypatch.setattr(recovery, 'black_ranges', lambda *a: [(3.0, 4.2)])
    monkeypatch.setattr(recovery, 'brighten_dark_scene', lambda *a: False)
    result = runner.check_clip_black({'clip_id': 'clip_02'}, {'passed': True, 'issues': []}, tmp_path / 'clip.mp4')
    assert not result['passed'] and 'black_frames' in result['issues']
    assert runner.check_clip_black({'clip_id': 'clip_01'}, {'passed': True}, tmp_path / 'other.mp4')['passed']


def test_nonverbal_events_remain_audible_without_becoming_recited_dialogue():
    pass
    shot = {'turns': [{'speaker_name': '甲', 'delivery_mode': 'visible_dialogue', 'text': '阿嚏~'},
                      {'speaker_name': '甲', 'delivery_mode': 'visible_dialogue', 'text': '我感冒了。'}]}
    assert [t['text'] for t in story_dialogue.merged_turns(shot)] == ['我感冒了。']
    assert '甲打喷嚏' in ClipCompiler(compiler_options()).sound_clause(shot)
    assert shot['sfx'].count('甲打喷嚏') == 1
    assert not story_dialogue.nonverbal_sound({'delivery_mode': 'visible_dialogue', 'text': '阿嚏，我感冒了。'})
    assert not story_dialogue.nonverbal_sound({'delivery_mode': 'visible_dialogue', 'text': '艾蕾娅！'})


def test_black_interval_also_flagged_as_freeze_is_still_repairable(tmp_path, monkeypatch):
    video = tmp_path / 'clip.mp4'
    video.touch()
    media = {'clips': [{'clip_id': 'clip_02', 'selected': {'video': str(video)}}],
             'assembly': {'media_qc': {'checks': {'black_frames': {'passed': False}, 'long_freeze': {'passed': False}}}}}
    (tmp_path / 'thin_media_report.json').write_text(json.dumps(media))
    monkeypatch.setattr(recovery, 'black_ranges', lambda *a: [(2, 6)])
    assert set(recovery.technical_targets(tmp_path)) == {'clip_02'}


def test_exposure_repair_does_not_turn_blank_video_into_a_pass(tmp_path):
    video = tmp_path / 'black.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=25',
                    '-t', '1.5', '-c:v', 'libx264', '-threads', '1', str(video)], check=True)
    original = video.read_bytes()
    assert not recovery.brighten_dark_scene(video, [(0, 1.4)], tmp_path, 'clip_01')
    assert video.read_bytes() == original


def test_old_black_gate_can_receive_one_cache_only_exposure_repair(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / 'book', tmp_path / 'old')
    m.info[1] = {**info(), 'status': 'done_with_warnings', 'exposure_due': True}
    m.state['recovery_attempts']['1'] = {'technical': 1}
    repair_manager_dispatch.schedule(m)
    job = m.state['jobs'][0]
    assert job['step'] == 1 and job['cache_only'] and job['recovery_kind'] == 'technical'
    job['step'] = 3
    repair_manager_workers.finish(m, job)
    repair_manager_dispatch.schedule(m)
    assert len(m.state['jobs']) == 1
