"""Updated ASR evidence must not inherit a cached combined speech failure."""
import pytest
from novel_manga.util import atomic_write_json
from novel_manga.review import storage,contracts
from novel_manga.application.review import episode,cast_video


@pytest.mark.parametrize('old_pass,new_pass,expected_calls',[(False,True,1),(False,False,0),(True,True,0)])
def test_review_reconsiders_coupled_verdict_only_when_speech_result_changes(tmp_path,monkeypatch,old_pass,new_pass,expected_calls):
    folder=tmp_path/'book/book_1';folder.mkdir(parents=True)
    video=folder/'clip.mp4';video.write_bytes(b'unchanged video')
    atomic_write_json(folder.parent/'story_bible.json',{'novel_title':'test','genre':'fantasy',
        'visual_style':'3D','palette':'warm','style_fingerprint':'style','characters':[]})
    atomic_write_json(folder/'clip_plan.json',{'clips':[{'kind':'video','clip_id':'clip_01','segment_ids':[],'spoken_text':''}]})
    atomic_write_json(folder/'thin_media_report.json',{'clips':[{'clip_id':'clip_01',
        'selected':{'video':str(video),'passed':new_pass,'reference':'','hypothesis':'','issues':[]}}]})
    atomic_write_json(folder/'episode_review.json',{'policy':contracts.POLICY,'review_mode':'classic',
        'clips':{'clip_01':{'video':str(video),'take':storage.take_identity(video),
            'severity':'pass' if old_pass else 'fail','tier':'optional' if old_pass else 'must_fix',
            'identity_ok':True,'speech_gate':{'passed':old_pass},'feedback':'' if old_pass else '当前语音门未通过'}},
        'feedback':{} if old_pass else {'clip_01':'当前语音门未通过'}})
    monkeypatch.setattr(cast_video,'enabled',lambda:False)
    monkeypatch.setattr(episode.review_evidence,'load_review_rules',lambda _: {})
    monkeypatch.setattr(episode.review_evidence,'review_mode',lambda _: 'classic')
    calls=[]
    def judge(*args,**kwargs):
        calls.append(args)
        return {'severity':'pass','identity_ok':True,'feedback':''}
    monkeypatch.setattr(episode.review_judges,'judge_clip',judge)
    result=episode.review_episode(folder)
    assert len(calls)==expected_calls
    if expected_calls:
        assert result['clips']['clip_01']['severity']=='pass'
        assert result['feedback']=={}


def test_new_audio_pass_does_not_automatically_clear_a_real_picture_failure(tmp_path,monkeypatch):
    folder=tmp_path/'book/book_1';folder.mkdir(parents=True)
    video=folder/'clip.mp4';video.write_bytes(b'unchanged video')
    atomic_write_json(folder.parent/'story_bible.json',{'novel_title':'test','genre':'fantasy',
        'visual_style':'3D','palette':'warm','style_fingerprint':'style','characters':[]})
    atomic_write_json(folder/'clip_plan.json',{'clips':[{'kind':'video','clip_id':'clip_01','segment_ids':[],'spoken_text':''}]})
    atomic_write_json(folder/'thin_media_report.json',{'clips':[{'clip_id':'clip_01',
        'selected':{'video':str(video),'passed':True,'reference':'','hypothesis':'','issues':[]}}]})
    atomic_write_json(folder/'episode_review.json',{'policy':contracts.POLICY,'review_mode':'classic',
        'clips':{'clip_01':{'video':str(video),'take':storage.take_identity(video),'severity':'fail',
            'speech_gate':{'passed':False},'feedback':'old combined failure'}},'feedback':{'clip_01':'old combined failure'}})
    monkeypatch.setattr(cast_video,'enabled',lambda:False)
    monkeypatch.setattr(episode.review_evidence,'load_review_rules',lambda _: {})
    monkeypatch.setattr(episode.review_evidence,'review_mode',lambda _: 'classic')
    monkeypatch.setattr(episode.review_judges,'judge_clip',lambda *a,**k:
        {'severity':'fail','identity_ok':False,'identity_issue':'wrong actor','feedback':'wrong actor',
         'verify':{'verdict':'obvious','action_by_wrong_person':True,'instruction':'use the correct actor'}})
    result=episode.review_episode(folder)
    assert result['clips']['clip_01']['severity']=='fail'
    assert 'clip_01' in result['feedback']
