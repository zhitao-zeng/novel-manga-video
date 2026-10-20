import json
import copy
import pytest
from pathlib import Path
from types import SimpleNamespace
from novel_manga.application.review import final_cut
from novel_manga.application.review.movie_context import assembly_inputs
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
    media['assembly']['source_inputs']=assembly_inputs(plan,media)
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


def test_final_reviewer_uses_approved_caption_stage_without_raw_request_prohibitions(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path)
    runner.context.clip_plan['clips'][0]['prompt_h3']='native-only-no-text-sentinel'
    atomic_write_json(movie.parent/'clip_plan.json', runner.context.clip_plan)
    media['assembly']['source_inputs']=assembly_inputs(runner.context.clip_plan,media)
    ass=tmp_path/'dialogue.ass';ass.write_text('Dialogue: 0,0:00:00.00,0:00:09.00,Default,,0,0,0,,approved dialogue\n')
    media['assembly']['ass']=str(ass)
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    def judge(parts,*args,**kwargs):
        text=json.dumps(parts,ensure_ascii=False)
        assert 'native-only-no-text-sentinel' not in text
        assert 'approved dialogue' in text and 'expected approved captions' in text
        assert '单层吻合expected_captions的字幕完全合法' in text
        return {'passed':True,'observations':['approved single caption layer'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['passed'] and result['frames'][0]['expected_captions']==['approved dialogue']


def test_movie_reviewer_receives_actual_asr_without_substituting_planned_reference(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path)
    media['clips'][0]['selected'].update(hypothesis='actual recognized dialogue',
        reference='expected words are not actual audio',passed=False,issues=['missing core dialogue'])
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'image'})
    def judge(parts,*args,**kwargs):
        text=json.dumps(parts,ensure_ascii=False)
        assert 'actual recognized dialogue' in text and 'missing core dialogue' in text
        assert 'expected words are not actual audio' not in text
        assert 'speech_gate_passed' in text
        return {'passed':True,'observations':['picture reviewed; audio gate remains independent'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['complete'] and media['clips'][0]['selected']['passed'] is False


@pytest.mark.parametrize('changed',[None,'movie','caption'])
def test_stream_continuation_reuses_completed_groups_and_keeps_their_failures(tmp_path,monkeypatch,changed):
    runner,media,movie=episode(tmp_path)
    for i,cid in enumerate(['b','c'],1):
        runner.context.clip_plan['clips'].append({'clip_id':cid,'kind':'video','shot_indexes':[i+1],'references':[]})
        media['clips'].append({'clip_id':cid,'selected':{'video':str(movie),'chunks':[]}})
        media['assembly']['timeline'].append({'unit_id':cid,'start':i*9,'end':(i+1)*9})
    atomic_write_json(movie.parent/'clip_plan.json',runner.context.clip_plan)
    media['assembly']['source_inputs']=assembly_inputs(runner.context.clip_plan,media)
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':27}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'image'})
    calls=[]
    issue={'clip_ids':['a'],'frame_numbers':[1],'problem':'wrong person receives the object',
           'evidence':'object goes to a different actor','source_basis':'source names the recipient'}
    def first(parts,*a,**k):
        calls.append(parts)
        if len(calls)==2:raise RuntimeError('upstream stream interrupted')
        return {'passed':False,'observations':['wrong recipient visible'],'issues':[copy.deepcopy(issue)]}
    monkeypatch.setattr(final_cut.model,'ask_json',first)
    interrupted=final_cut.review_assembly(runner,media)
    assert not interrupted['complete'] and len(interrupted['groups'])==1
    if changed=='movie':
        movie.write_bytes(b'changed movie')
        media['assembly']['source_inputs']=assembly_inputs(runner.context.clip_plan,media)
    elif changed=='caption':
        ass=tmp_path/'subtitles.ass';ass.write_text('Dialogue: 0,0:00:00.00,0:00:27.00,Default,,0,0,0,,different approved caption\n')
        media['assembly']['ass']=str(ass)
    def remaining(parts,*a,**k):
        calls.append(parts)
        return {'passed':True,'observations':['remaining pictures reviewed'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',remaining)
    resumed=final_cut.review_assembly(runner,media)
    assert resumed['complete'] and not resumed['errors']
    if changed is None:
        assert len(calls)==3 and not resumed['passed']
        assert resumed['issues'][0]['problem']==issue['problem']
        assert resumed['responses'][0]['reused_from']
        assert Path(resumed['issues'][0]['frame_paths'][0]).is_file()
    else:
        assert len(calls)==4 and resumed['passed']
