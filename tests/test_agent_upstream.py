import copy
import wave
from pathlib import Path
import pytest
from novel_manga.util import atomic_write_json, read_json


def setup_case(tmp_path):
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    source='同一手机后置，只见右手空杆。灰色外套和灰工装裤。'
    shot={'index':1,'source_quote':source,'camera':'same phone','visual_prompt':'old',
          'motion_prompt':'old','duration_seconds':9,'turns':[{'speaker_name':'A','delivery_mode':'offscreen_dialogue','text':'试一下。'}]}
    clip={'clip_id':'a','kind':'video','shot_indexes':[1],'request_seconds':9,'shot_timing':[{'seconds':9}],
          'prompt':'old','prompt_h3':'old English','references':[],'lines':shot['turns']}
    script={'shots':[shot]};plan={'clips':[clip,{'clip_id':'b','kind':'video','prompt':'untouched'}]}
    for name,data in [('chapter_script.json',script),('clip_plan.json',plan),('segments.json',[{'text':source}]),('review_feedback.json',{})]:atomic_write_json(directory/name,data)
    return directory,script,plan,clip,source


def test_source_patch_compiles_earliest_field_and_preserves_other_clip(tmp_path,monkeypatch):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path);calls=[]
    monkeypatch.setattr(upstream,'check_source',lambda *args: {'consistent':True,'problems':[]})
    monkeypatch.setattr(upstream,'check_compilation',lambda *args: {'consistent':True,'problems':[]})
    def rebuild(directory,bible,script,plan,targets,**kwargs):
        result=copy.deepcopy(plan);result['clips'][0]['prompt']=script['shots'][0]['visual_prompt'];calls.append(kwargs);return result,['a']
    monkeypatch.setattr(upstream,'rebuild_clips',rebuild)
    monkeypatch.setattr(upstream,'convert',lambda clip,**kw:clip.update(prompt_h3='compiled '+clip['prompt']))
    monkeypatch.setattr(upstream,'check_request',lambda *a:{'consistent':True,'problems':[]})
    decision={'action':'input','source_quote':source,'reason':'camera is filming itself','shots':[{'index':1,'visual_prompt':'only right hand'}]}
    result=upstream.input_proposal(d,p,c,decision)
    assert result.script['shots'][0]['visual_prompt']=='only right hand'
    assert result.plan['clips'][0]['prompt_h3']=='compiled only right hand'
    assert result.plan['clips'][1]==p['clips'][1] and result.script['shots'][0]['turns']==s['shots'][0]['turns']
    assert read_json(d/'chapter_script.json')==s
    assert calls[0]['preserve_timing'] is True


@pytest.mark.parametrize('patch',[{'index':2,'camera':'new'},{'index':1,'turns':[]},{'index':1,'duration_seconds':1}])
def test_protected_or_outside_source_patch_rejected_before_model(tmp_path,monkeypatch,patch):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path)
    monkeypatch.setattr(upstream,'check_source',lambda *a:pytest.fail('invalid patch reached judge'))
    with pytest.raises(ValueError):upstream.input_proposal(d,p,c,{'action':'input','source_quote':source,'shots':[patch]})


def test_unreviewed_reference_cannot_be_selected(tmp_path,monkeypatch):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path);c['references']=[{'role':'character','name':'A','path':'old.jpeg'}]
    atomic_write_json(d/'repair_candidates.json',{'assets':[{'id':'body','role':'character','name':'A','path':'missing.jpeg','review':{'consistent':False}}]})
    with pytest.raises(ValueError,match='review|image|图片'):upstream.input_proposal(d,p,c,{'action':'input','source_quote':source,'assets':[{'candidate_id':'body'}]})


def test_audio_adoption_preserves_plan_history_and_video_budget(tmp_path):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path);audio=d/'ambience.wav'
    with wave.open(str(audio),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    atomic_write_json(d/'repair_candidates.json',{'audio':[{'id':'room','path':str(audio),'review':{'speech_free':True,'method':'synthesis'},'processing':'water noise, no speech input'}]})
    before=(d/'clip_plan.json').read_bytes()
    atomic_write_json(d/'interaction.json',{'seconds':3})
    result=upstream.adopt_audio(d,c,{'action':'audio','candidate_id':'room','reason':'mixed track has speech'})
    assert result['assembly_changed'] is True
    adopted=read_json(d/'delivery_policy.json')['interaction_ambience']
    assert adopted['source_path']==str(audio) and Path(adopted['path']).read_bytes()==audio.read_bytes()
    assert (d/'clip_plan.json').read_bytes()==before and not (d/'repair_budget_grants.json').exists()


def test_exhausted_video_still_prepares_source_without_render_permission(tmp_path,monkeypatch):
    from novel_manga.application.repair import agent,managed,history,upstream
    from tests.test_repair_agent import configured
    d,clips,_=configured(tmp_path)
    monkeypatch.setattr(managed,'generated_counts',lambda *a:{'a':3,'b':3})
    monkeypatch.setattr(agent,'propose',lambda *a,**k:{'action':'input','reason':'fix source','evidence':['input/primary.json'],'source_quote':'source','shots':[]})
    from novel_manga.repair.proposal import RepairProposal
    new=copy.deepcopy(read_json(d/'clip_plan.json'));new['clips'][0]['prompt']='source corrected'
    monkeypatch.setattr(upstream,'input_proposal',lambda *a:RepairProposal({'changed':['a']},script=read_json(d/'chapter_script.json'),plan=new,notes={},changes={'a':{'action':'input'}}))
    result=managed.prepare_agent_batch(d,['a'])
    assert result['input_changed']==['a'] and result['skip_render'] is True
    assert result['changed']==[] and 'a' in result['needs_video']
    assert len(history.load(d)['trials'])==1


def test_false_compiled_camera_is_rejected_before_publication(tmp_path,monkeypatch):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path)
    monkeypatch.setattr(upstream,'check_source',lambda *a:{'consistent':True,'problems':[]})
    def rebuild(*a,**kw):
        plan=copy.deepcopy(p);plan['clips'][0]['prompt']='changed';return plan,['a']
    monkeypatch.setattr(upstream,'rebuild_clips',rebuild)
    monkeypatch.setattr(upstream,'convert',lambda clip,**kw:clip.update(prompt_h3='reveals recording phone'))
    monkeypatch.setattr(upstream,'check_request',lambda *a:{'consistent':True,'problems':[]})
    monkeypatch.setattr(upstream,'check_compilation',lambda *a:{'consistent':False,'problems':['recording device should be offscreen']})
    with pytest.raises(ValueError,match='approved source'):
        upstream.input_proposal(d,p,c,{'source_quote':source,'shots':[{'index':1,'camera':'only right hand visible'}]})
    assert read_json(d/'chapter_script.json')==s


def test_new_reviewed_candidate_reopens_blocked_agent_input(tmp_path,monkeypatch):
    from novel_manga.application.repair import agent,managed
    from tests.test_repair_agent import configured
    d,clips,_=configured(tmp_path);monkeypatch.setattr(agent,'load_config',lambda:{})
    review=read_json(d/'episode_review.json');takes=managed.current_takes(d,{'clips':clips},review)
    state=managed.input_state(d,clips[0],review['clips']['a'],takes)
    atomic_write_json(d/'repair_routing.json',{'a':{'status':'blocked','reason':'missing approved asset','inputs':state}})
    assert 'a' not in managed.candidates(d,review)[0]
    atomic_write_json(d/'repair_candidates.json',{'assets':[{'id':'reviewed-body'}]})
    assert 'a' in managed.candidates(d,review)[0]


def test_partial_legacy_rebuild_cannot_publish_new_script_for_skipped_clip(tmp_path,monkeypatch):
    from novel_manga.application.repair import upstream
    d,s,p,c,source=setup_case(tmp_path)
    p['clips'][1]={**copy.deepcopy(c),'clip_id':'b','shot_indexes':[2]}
    s['shots'].append({**copy.deepcopy(s['shots'][0]),'index':2})
    atomic_write_json(d/'clip_plan.json',p);atomic_write_json(d/'chapter_script.json',s)
    monkeypatch.setattr(upstream,'check_source',lambda *a:{'consistent':True,'problems':[]})
    def rebuild(*args,**kwargs):
        kwargs['repack_report']['_compile_skipped']=['a: stage parts no longer match']
        candidate=copy.deepcopy(p);candidate['clips'][1]['prompt']='other changed'
        return candidate,['b']
    monkeypatch.setattr(upstream,'rebuild_clips',rebuild)
    monkeypatch.setattr(upstream,'convert',lambda clip,**kw:clip.update(prompt_h3='compiled'))
    monkeypatch.setattr(upstream,'check_request',lambda *a:{'consistent':True,'problems':[]})
    monkeypatch.setattr(upstream,'check_compilation',lambda *a:{'consistent':True,'problems':[]})
    # Both clips are in scope; the real rebuild may succeed one and retain the other.
    with pytest.raises(ValueError,match='skipped'):
        upstream.input_proposal(d,p,c,{'source_quote':source,'clip_ids':['a','b'],'shots':[{'index':1,'camera':'fixed'}]})
    assert read_json(d/'chapter_script.json')==s and read_json(d/'clip_plan.json')==p


def test_body_candidate_keeps_hd_identity_as_a_second_view():
    from novel_manga.application.packing.assets import select_reference_views
    from novel_manga.story.h3 import subject_lines
    refs=[{'tag':'@图片1','role':'character','name':'A','path':'portrait.jpg'},
          {'tag':'@图片2','role':'location','name':'room','path':'room.jpg'}]
    bindings=['<A>只对应@图片1，只采用五官、发型、体型和服装；A的辨识特征：短发']
    refs,bindings=select_reference_views(refs,bindings,{'A':{'body':'fullbody.jpg','face':'portrait.jpg','asset_id':'body'}})
    assert len([r for r in refs if r['role']=='character'])==2
    assert refs[0]['path']=='fullbody.jpg' and refs[-1]['path']=='portrait.jpg' and refs[-1]['view']=='expressions'
    assert '@图片3定五官' in bindings[0] and '@图片1定身体' in bindings[0]
    definitions,_=subject_lines({'references':refs})
    assert 'face, hair, age and skin tone from <Picture 3>' in definitions[0]


def test_ordinary_implicit_expression_view_keeps_existing_hd_face_without_third_picture():
    from novel_manga.application.packing.assets import select_reference_views
    refs=[{'tag':'@图片1','role':'character','name':'A','path':'turnaround.jpeg'},
          {'tag':'@图片2','role':'character','name':'A','path':'expressions.jpeg'}]
    refs,bindings=select_reference_views(refs,['<A>对应@图片1和@图片2'],{'A':{'body':'new-body.jpeg','face':'expressions.jpeg'}})
    assert len(refs)==2 and refs[0]['path']=='new-body.jpeg' and refs[1]['path']=='expressions.jpeg'
    assert refs[1]['view']=='expressions' and '@图片2定五官' in bindings[0]


def test_zero_video_budget_returns_bad_retake_to_agent_for_source_action(tmp_path,monkeypatch):
    from novel_manga.application.repair import agent,managed,upstream
    from novel_manga.repair.proposal import RepairProposal
    from tests.test_repair_agent import configured
    d,clips,_=configured(tmp_path);calls=[]
    monkeypatch.setattr(managed,'generated_counts',lambda *a:{'a':3})
    new=copy.deepcopy(read_json(d/'clip_plan.json'));new['clips'][0]['prompt']='source fixed'
    monkeypatch.setattr(upstream,'input_proposal',lambda *a:RepairProposal({'changed':['a']},script=read_json(d/'chapter_script.json'),plan=new,notes={},changes={'a':{'action':'input'}}))
    def propose(*args,rejections=None):
        calls.append(rejections or [])
        return {'action':'retake' if len(calls)==1 else 'input','reason':'fix earliest layer','evidence':['input/upstream.json']}
    monkeypatch.setattr(agent,'propose',propose)
    result=managed.prepare_agent_batch(d,['a'])
    assert len(calls)==2 and calls[1][0]['stage']=='video_budget'
    assert result['input_changed']==['a'] and result['skip_render'] and result['changed']==[]
