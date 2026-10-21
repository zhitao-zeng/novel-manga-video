import pytest
from novel_manga.application.review import evidence
from novel_manga.util import read_json


def test_frame_evidence_covers_final_action_without_extra_frames(tmp_path,monkeypatch):
    video=tmp_path/'clip.mp4';video.write_bytes(b'current take')
    monkeypatch.setattr(evidence,'media_duration',lambda path:10)
    calls=[]
    monkeypatch.setattr(evidence.subprocess,'run',lambda args,**kwargs:calls.append(args))
    frames=evidence.clip_frames(video,tmp_path/'frames',6)
    times=[float(args[args.index('-ss')+1])for args in calls]
    assert len(frames)==len(calls)==6
    assert times==sorted(times)
    assert times[0]<0.5 and 9.5<times[-1]<10
    receipt=read_json(tmp_path/'frames/frame-evidence.json')
    assert [row['seconds']for row in receipt['frames']]==pytest.approx(times,abs=0.0005)
    assert receipt['video']==str(video) and receipt['take']
