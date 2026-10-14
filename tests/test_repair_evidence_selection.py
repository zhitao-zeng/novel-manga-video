import json
from novel_manga.application.repair.agent import unique_takes


def take(tmp_path, name, marker=None, data=b'video'):
    video=tmp_path/name/'clip.mp4';video.parent.mkdir();video.write_bytes(data)
    (video.parent/'request.json').write_text(json.dumps({'managed_generation':marker}))
    return video


def test_current_and_archive_of_same_generation_are_exported_once(tmp_path):
    marker={'trial':1,'clip_id':'clip_08','task_id':'same-render','take':[1,5,10]}
    current=take(tmp_path,'current',marker);archive=take(tmp_path,'archive',marker)
    paths,skipped=unique_takes([current,archive])
    assert paths==[current] and skipped==1
    assert archive.read_bytes()==b'video'


def test_actual_previous_generations_and_unknown_provenance_are_retained(tmp_path):
    current=take(tmp_path,'current',{'trial':2,'task_id':'new'})
    old=take(tmp_path,'old',{'trial':1,'task_id':'old'})
    unknown=take(tmp_path,'unknown')
    paths,skipped=unique_takes([current,old,unknown,current])
    assert paths==[current,old,unknown] and skipped==0
