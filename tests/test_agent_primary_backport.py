import json
from pathlib import Path
from types import SimpleNamespace
from tests.test_agent_frame_evidence import agent, setup_episode, mock_host_frames, write
from novel_manga.review.storage import take_identity


def test_compact_entry_contains_current_take_local_source_and_keeps_full_novel_documents(tmp_path,monkeypatch,agent):
    directory,current,_,clip,_=setup_episode(tmp_path)
    clip['segment_ids']=['s1']
    card=directory.parent/'card.jpg';card.write_bytes(b'reference')
    write(card.with_suffix('.jpg.origin.json'),{'reference_scope':'identity_portrait'})
    clip['references']=[{'role':'character','name':'甲','path':'card.jpg'}]
    write(directory/'chapter_script.json',{'shots':[{'index':1,'segment_id':'s1'},{'index':2,'segment_id':'s2'},{'index':3,'segment_id':'s3'}]})
    write(directory/'segments.json',[{'segment_id':'s1','text':'current source'},{'segment_id':'s2','text':'next source'},{'segment_id':'s3','text':'unrelated source'}])
    write(directory.parent/'entity_index.json',{'existing':'identity index'})
    mock_host_frames(agent,monkeypatch);config={'runs_root':str(tmp_path/'runs')}
    def execute(run,**kwargs):
        work=Path(config['runs_root'])/run
        primary=json.loads((work/'input/primary.json').read_text());evidence=json.loads((work/'input/evidence.json').read_text())
        assert primary['current_frames']==evidence['takes'][0]['frames']
        assert primary['provider']=='h3' and primary['request_field']=='prompt_h3'
        assert primary['references'][0]['reference_scope']=='identity_portrait'
        assert (work/primary['current_request']).read_bytes()==(current.parent/'request.json').read_bytes()
        assert [r['segment_id']for r in primary['source_passages']]==['s1','s2']
        assert len(evidence['segments'])==3 and len(evidence['takes'])==2
        assert 'input/entity_index.json'in evidence['source_documents']
        assert (work/'prompt.txt').read_text().startswith('Read input/primary.json first')
        write(work/'output/decision.json',{'action':'stop','reason':'test','evidence':['input/primary.json']})
        return SimpleNamespace(ok=True,produced=['decision.json'],attempt='test')
    monkeypatch.setattr(agent,'run_agent',execute)
    agent.propose(directory,clip,{},config=config)


def test_failed_unselected_take_still_supplies_actual_frames_request_and_asr(tmp_path,monkeypatch,agent):
    directory,current,_,clip,_=setup_episode(tmp_path)
    write(directory/'thin_media_report.json',{'clips':[{'clip_id':clip['clip_id'],'selected':None,'attempts':[{'video':str(current),'passed':False}]}]})
    mock_host_frames(agent,monkeypatch);config={'runs_root':str(tmp_path/'runs')}
    def execute(run,**kwargs):
        work=Path(config['runs_root'])/run;e=json.loads((work/'input/evidence.json').read_text())
        assert e['takes'][0]['current'] is True
        assert (work/e['takes'][0]['path']/'clip.mp4').read_bytes()==current.read_bytes()
        assert e['takes'][0]['frames']and(work/e['takes'][0]['asr']).exists()
        write(work/'output/decision.json',{'action':'stop','reason':'test','evidence':['input/evidence.json']})
        return SimpleNamespace(ok=True,produced=['decision.json'],attempt='test')
    monkeypatch.setattr(agent,'run_agent',execute)
    agent.propose(directory,clip,{'video':str(current),'take':take_identity(current)},config=config)
