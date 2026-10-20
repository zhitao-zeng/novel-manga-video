from types import SimpleNamespace
from pathlib import Path
import pytest
from novel_manga.application.repair import ambience
from novel_manga.application.review import final_cut
from novel_manga.util import atomic_write_json


def test_unreviewed_audio_rejected(tmp_path):
    with pytest.raises(ValueError,match='speech-free'):ambience.validate({'path':str(tmp_path/'none.wav'),'review':{'speech_free':False}})


def test_independent_ambience_does_not_reuse_source_clip_asr(tmp_path):
    # The old mixed source words remain in body; the card now uses another audio file.
    import wave
    audio=tmp_path/'room.wav'
    with wave.open(str(audio),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    receipt={'ambience_source':{'kind':'independent_asset','review':{'speech_free':True,'method':'local_asr'},'path':str(audio)}}
    assert final_cut.ambience_speech_issues({'clips':[]},receipt,[])==[]


def test_audio_only_repair_reexports_and_reviews_without_renderer(tmp_path,monkeypatch):
    from tests.test_final_cut_loop import fixture,verdict
    from novel_manga.application.rendering import reviewed
    runner,report,movie=fixture(tmp_path,monkeypatch);events=[]
    monkeypatch.setattr(reviewed.managed,'candidates',lambda d,r:(list(r['feedback']),{}))
    monkeypatch.setattr(reviewed.managed,'prepare',lambda *a:{'changed':[],'assembly_changed':True,'skip_render':True})
    runner.run=lambda:pytest.fail('audio repair must not generate video')
    def final(media):
        events.append('export_and_review');return verdict(movie,len(events)>1)
    result=reviewed.run(runner,repair_existing=True,final_review=final)
    assert events==['export_and_review','export_and_review'] and result['quality_review']['passed']


def test_audio_policy_and_actual_file_change_invalidates_movie_context(tmp_path):
    from novel_manga.application.review.movie_context import context
    audio=tmp_path/'room.wav';audio.write_bytes(b'one')
    atomic_write_json(tmp_path/'delivery_policy.json',{'interaction_ambience':{'path':str(audio)}})
    first=context(tmp_path,{},'missing.mp4');audio.write_bytes(b'two different')
    second=context(tmp_path,{},'missing.mp4')
    assert first!=second


def test_novel_audio_target_cannot_silence_bound_dialogue(tmp_path):
    import wave
    from novel_manga.util import read_json
    audio=tmp_path/'room.wav'
    with wave.open(str(audio),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    atomic_write_json(tmp_path/'repair_candidates.json',{'audio':[{'id':'room','path':str(audio),'review':{'speech_free':True,'method':'local_asr'}}]})
    clip={'clip_id':'a','lines':[{'text':'do not cut this speech','delivery_mode':'offscreen_dialogue'}]}
    with pytest.raises(ValueError,match='dialogue'):ambience.adopt(tmp_path,clip,{'candidate_id':'room','target':'clip'})
    assert not (tmp_path/'delivery_policy.json').exists()


def test_novel_silent_clip_audio_is_assembled_without_video_generation(tmp_path,monkeypatch):
    import wave
    from novel_manga.util import read_json
    from novel_manga.application.repair import upstream
    audio=tmp_path/'room.wav'
    with wave.open(str(audio),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    atomic_write_json(tmp_path/'repair_candidates.json',{'audio':[{'id':'room','path':str(audio),'review':{'speech_free':True,'method':'local_asr'}}]})
    clip={'clip_id':'a','lines':[],'shot_indexes':[1]}
    atomic_write_json(tmp_path/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(tmp_path/'chapter_script.json',{'shots':[{'index':1,'turns':[{'delivery_mode':'silent_action','text':'look'}]}]})
    result=ambience.adopt(tmp_path,clip,{'candidate_id':'room','target':'clip'})
    assert result['assembly_changed'] and 'a' in read_json(tmp_path/'delivery_policy.json')['clip_ambience']
    movie=tmp_path/'movie.mp4';movie.write_bytes(b'old movie');commands=[]
    monkeypatch.setattr(ambience,'run',lambda command:commands.append(command)or Path(command[-1]).write_bytes(b'assembled new audio'))
    report={'assembly':{'final_video':str(movie),'thin_passed':True,'media_qc_passed':True,'timeline':[{'unit_id':'a','start':0,'end':5}]}}
    updated=ambience.apply_assembly(tmp_path,report)
    assert updated['assembly']['final_video']!=str(movie) and movie.read_bytes()==b'old movie'
    assert updated['assembly']['thin_passed'] is False and updated['assembly']['media_qc_passed'] is False
    assert '-c:v' in commands[0] and commands[0][commands[0].index('-c:v')+1]=='copy'
    assert ambience.apply_assembly(tmp_path,updated)==updated and len(commands)==1


def test_real_novel_audio_remux_keeps_video_stream_and_replaces_silent_clip(tmp_path):
    import subprocess,wave
    movie=tmp_path/'body.mp4';room=tmp_path/'room.wav'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=128x96:r=25:d=3',
                    '-f','lavfi','-i','sine=frequency=200:sample_rate=48000:duration=3',
                    '-c:v','libx264','-c:a','aac','-shortest',str(movie)],check=True)
    with wave.open(str(room),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    clip={'clip_id':'a','lines':[],'shot_indexes':[1]}
    atomic_write_json(tmp_path/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(tmp_path/'chapter_script.json',{'shots':[{'index':1,'turns':[{'delivery_mode':'silent_action'}]}]})
    atomic_write_json(tmp_path/'delivery_policy.json',{'clip_ambience':{'a':{'path':str(room),'review':{'speech_free':True,'method':'fixture_silence'}}}})
    result=ambience.apply_assembly(tmp_path,{'assembly':{'final_video':str(movie),'timeline':[{'unit_id':'a','start':0,'end':3}]}})
    target=Path(result['assembly']['final_video'])
    def video(path):return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-c:v','copy','-f','h264','-'])
    assert video(movie)==video(target)
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(target),'-vn','-f','s16le','-'])
    assert pcm and set(pcm)=={0}
    from novel_manga.config import Settings
    ctx=SimpleNamespace(novel_dir=tmp_path.parent,episode_dir=tmp_path,clip_plan={'clips':[clip]},
                        script={'shots':[{'index':1,'turns':[{'delivery_mode':'silent_action'}]}]},
                        settings=Settings(width=128,height=96))
    checked=ambience.apply_assembly(tmp_path,result,context=ctx)
    assert checked['assembly']['final_video']==str(target)
    assert checked['assembly']['audio_repair']['qc_checked'] is True
    assert checked['assembly']['media_qc']['checks']['audio_level']['passed'] is False
    assert checked['assembly']['thin_passed'] is False


def test_persisted_ambience_allows_recovery_when_current_assembly_is_missing(tmp_path,monkeypatch):
    import wave
    room=tmp_path/'room.wav'
    with wave.open(str(room),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    atomic_write_json(tmp_path/'delivery_policy.json',{'clip_ambience':{'a':{'path':str(room),'review':{'speech_free':True,'method':'local_asr'}}}})
    monkeypatch.setattr(ambience,'run',lambda *a:pytest.fail('no assembled movie to remux'))
    report={'assembly':None,'status':'clips_failed','blocked_clips':{'a':['request conflict']}}
    assert ambience.apply_assembly(tmp_path,report)==report


from support.repair_history import episode, candidate_episode


def test_novel_audio_candidate_passes_existing_controlled_publication(episode,tmp_path,monkeypatch):
    import wave
    from novel_manga.application.repair import delivery
    from novel_manga.application.profiles import plan_fingerprint
    from novel_manga.util import read_json
    directory,plan,review,media,takes=candidate_episode(episode)
    plan['clips'][0].update(shot_indexes=[1],lines=[])
    atomic_write_json(directory/'clip_plan.json',plan)
    media['clip_plan_fingerprint']=plan_fingerprint(plan)
    from novel_manga.application.review.movie_context import assembly_inputs
    media['assembly']['source_inputs']=assembly_inputs(plan,media)
    media['assembly']['timeline']=[{'unit_id':'clip_01','start':0,'end':8}]
    room=tmp_path/'room.wav'
    with wave.open(str(room),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    atomic_write_json(directory/'chapter_script.json',{'shots':[{'index':1,'turns':[{'delivery_mode':'silent_action'}]}]})
    atomic_write_json(directory/'delivery_policy.json',{'clip_ambience':{'clip_01':{'path':str(room),'review':{'speech_free':True,'method':'local_asr'}}}})
    monkeypatch.setattr(ambience,'run',lambda command:Path(command[-1]).write_bytes(b'audio corrected movie'))
    result=ambience.apply_assembly(directory,media)
    assert Path(result['assembly']['final_video']).parent==directory/'repair_candidate'
    # Unchecked changed audio must first lose the old passing technical verdict.
    assert result['assembly']['thin_passed'] is False
    # This fixture represents the subsequent successful actual media check.
    result['assembly'].update(thin_passed=True,media_qc_passed=True)
    atomic_write_json(directory/'thin_media_report.json',result)
    review['feedback']={};review['clips']['clip_01'].update(story_ok=True,verify={'verdict':'fine'})
    assert delivery.publish_if_ready(directory,review,takes)
    assert (directory/'nov_1.mp4').read_bytes()==b'audio corrected movie'
    assert (directory/'repair_history/previous_final.mp4').read_bytes()==b'incumbent movie'
