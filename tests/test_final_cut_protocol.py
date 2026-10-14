import json
from pathlib import Path
from types import SimpleNamespace
from novel_manga.application.review import final_cut
from novel_manga.util import atomic_write_json

def episode(tmp_path):
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    movie=directory/'movie.mp4';movie.write_bytes(b'movie')
    clip={'clip_id':'a','kind':'video','shot_indexes':[1],'references':[]}
    plan={'clips':[clip]}
    atomic_write_json(directory/'clip_plan.json',plan)
    atomic_write_json(directory/'chapter_script.json',{'shots':[]})
    atomic_write_json(directory/'episode_review.json',{'clips':{'a':{'severity':'pass'}},'feedback':{}})
    media={'clips':[{'clip_id':'a','selected':{'video':str(movie),'chunks':[]}}],
           'assembly':{'final_video':str(movie),'thin_passed':True,
                       'timeline':[{'unit_id':'a','start':0,'end':9}]}}
    ctx=SimpleNamespace(episode_dir=directory,clip_plan=plan,script={'shots':[]},bible=None,
                        settings=SimpleNamespace(width=1080,height=1920,font_path='/font'))
    return SimpleNamespace(context=ctx),media,movie

def test_invalid_review_response_returns_exact_protocol_feedback_once(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);calls=[]
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    issue={'clip_ids':['a'],'frame_numbers':[1],'problem':'wet ground on dry platform',
           'evidence':'visible water','source_basis':'source requires dry platform'}
    def judge(parts,*args,**kwargs):
        calls.append(parts[-1]['text'])
        return {'passed':len(calls)==1,'observations':['visible water'],'issues':[issue.copy()]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['complete'] is True and result['passed'] is False
    assert len(calls)==2 and 'conflicting passage' in calls[1]
    assert 'wet ground on dry platform' in calls[1]
    assert len(result['responses'])==2 and 'protocol_error' in result['responses'][0]
    assert result['issues'][0]['problem']==issue['problem']

def test_repeated_bad_protocol_stops_without_certifying_movie(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);calls=[]
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    def judge(*args,**kwargs):calls.append(1);return {'passed':False,'observations':['uncertain'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert len(calls)==2 and result['complete'] is False and result['passed'] is False
    assert result['errors'] and len(result['responses'])==2
