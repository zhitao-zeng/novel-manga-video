import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from support.managed_episode import fixture_episode
from novel_manga.application.repair import agent, managed, history
from novel_manga.application.preparation import request_check
from novel_manga.application.profiles import h3_prompt_outdated, h3_stamp
from novel_manga.story.h3 import compose


def example():
    c={'clip_id':'a','kind':'video','prompt':'【阶段】甲站在门前。', 'request_seconds':5,
       'references':[{'role':'character','name':'甲','path':'a.jpg'},
                     {'role':'voice','name':'甲','path':'a.wav'}]}
    c['prompt_h3']=compose(c,['<Subject 1> stands by the door.'],[('', [('甲','你好。',False,'平静')])])
    return c


@pytest.mark.parametrize('before,after', [('你好.','再见.'), ('<Picture 1>','<Picture 2>'),
                                        ('<Audio 1>','<Audio 2>'), ('(S1)','(S2)'),
                                        ('[Shot 1]','[Shot 2]')])
def test_agent_cannot_change_locked_request_parts(before,after):
    c=example()
    with pytest.raises(ValueError):agent.validate_prompt(c,c['prompt_h3'].replace(before,after))


def test_repair_can_add_duration_matching_saved_shot_timing():
    c = example()
    c['shot_timing'] = [{'stage': 1, 'seconds': 5}]
    replacement = c['prompt_h3'].replace('[Shot 1]', '[Shot 1] Planned duration: 5 seconds.')
    agent.validate_prompt(c, replacement)


def test_repair_cannot_add_duration_different_from_saved_shot_timing():
    c = example()
    c['shot_timing'] = [{'stage': 1, 'seconds': 5}]
    replacement = c['prompt_h3'].replace('[Shot 1]', '[Shot 1] Planned duration: 6 seconds.')
    with pytest.raises(ValueError):
        agent.validate_prompt(c, replacement)


def configured(tmp_path):
    d,clips,review=fixture_episode(tmp_path)
    for c in clips:
        c['prompt_h3']=compose(c,['A person stands by the door.'],[('',[])])
        c['prompt_h3_of']=h3_stamp(c)
    (d/'clip_plan.json').write_text(json.dumps({'clips':clips}))
    (d.parent/'profile.json').write_text(json.dumps({'repair_backend':'sandbox_agent'}))
    return d,clips,review


def test_first_failure_uses_agent_instead_of_old_route_and_keeps_budget(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path);saved=copy.deepcopy(clips);calls=[]
    monkeypatch.setattr('novel_manga.application.packing.blocked.repair_episode',lambda *a,**k:pytest.fail('old path'))
    def decide(directory,clip,verdict):
        calls.append(clip['clip_id'])
        return {'action':'edit','reason':'remove ambiguity','evidence':['input/prompt.txt'],
                'prompt':clip['prompt_h3'].replace('stands by','stands calmly by'),'run':'fixture','attempt':'1'}
    monkeypatch.setattr(agent,'propose',decide)
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:{'consistent':True})
    result=managed.prepare(d,['a'])
    assert calls==['a'] and result['changed']==['a']
    assert not any(managed.generated_counts(history.load(d)).values())
    new=json.loads((d/'clip_plan.json').read_text())['clips']
    assert new[1]==saved[1] and new[0]['prompt']==saved[0]['prompt']
    assert 'stands calmly' in new[0]['prompt_h3']
    assert not h3_prompt_outdated(new[0],strict=True)
    trial=history.load(d)['trials'][-1]
    assert trial['method']=='agent_prompt' and trial['managed']


def test_budget_exhaustion_never_launches_agent(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path)
    rec=history.load(d);rec['prior_generation_counts']={'clips':{'a':3}};history.save(d,rec)
    monkeypatch.setattr(agent,'propose',lambda *a,**k:pytest.fail('no remaining video budget'))
    result=managed.prepare(d,['a'])
    assert not result['changed'] and 'budget' in result['blocked']['a']


def test_rejected_agent_candidate_does_not_write_or_charge_and_other_clip_proceeds(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path);before=copy.deepcopy(clips)
    monkeypatch.setattr(agent,'propose',lambda directory,clip,verdict:{
        'action':'edit','reason':'fix','evidence':['input/prompt.txt'],
        'prompt':clip['prompt_h3'].replace('stands by','stands calmly by')})
    monkeypatch.setattr(request_check,'request_consistency',lambda c,*a,**k:{
        'consistent':c['clip_id']=='b','problems':['still wrong']})
    result=managed.prepare(d)
    after=json.loads((d/'clip_plan.json').read_text())['clips']
    assert result['changed']==['b'] and 'a' in result['blocked']
    assert after[0]==before[0] and not any(managed.generated_counts(history.load(d)).values())


def test_agent_stop_does_not_fall_back_to_blind_retake(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path)
    monkeypatch.setattr(agent,'propose',lambda *a:{'action':'stop','reason':'no supported fix','evidence':['input/take-1']})
    result=managed.prepare(d,['a'])
    assert result['skip_render'] and 'agent stopped' in result['blocked']['a']
    assert json.loads((d/'clip_plan.json').read_text())['clips']==clips


def test_sandbox_exports_copies_and_requires_current_output(tmp_path,monkeypatch):
    d,clips,review=configured(tmp_path);config={'runs_root':str(tmp_path/'runs')}
    def run(name,**kw):
        work=Path(config['runs_root'])/name
        evidence=json.loads((work/'input/evidence.json').read_text())
        assert evidence['takes'] and (work/evidence['takes'][0]['path']/'clip.mp4').is_file()
        assert (work/'.claude/skills/video-repair/SKILL.md').is_file()
        (work/'output').mkdir()
        (work/'output/decision.json').write_text(json.dumps({'action':'retake','reason':'input correct','evidence':['input/take-1']}))
        return SimpleNamespace(ok=True,produced=['decision.json'],directory=work/'attempts/1')
    monkeypatch.setattr(agent,'run_agent',run)
    decision=agent.propose(d,clips[0],review['a'],config=config)
    assert decision['action']=='retake'


def test_unchanged_repack_preserves_agent_request_but_new_inputs_invalidate_it():
    from novel_manga.application.packing.flow import carry_agent_requests
    c=example();c.update(prompt_h3_of=h3_stamp(c),prompt_h3_agent={'run':'r'},repair_take=2,shot_indexes=[1])
    fresh={k:copy.deepcopy(v) for k,v in c.items() if k not in ['prompt_h3','prompt_h3_of','prompt_h3_agent','repair_take']}
    plan={'clips':[copy.deepcopy(fresh)]}
    carry_agent_requests({'clips':[c]},plan)
    assert plan['clips'][0]['prompt_h3']==c['prompt_h3'] and plan['clips'][0]['repair_take']==2
    changed=copy.deepcopy(fresh);changed['prompt']='【阶段】甲坐下。'
    plan={'clips':[changed]};carry_agent_requests({'clips':[c]},plan)
    assert 'prompt_h3' not in plan['clips'][0]
    changed=copy.deepcopy(fresh);changed['references'][0]['path']='new-card.jpg'
    plan={'clips':[changed]};carry_agent_requests({'clips':[c]},plan)
    assert 'prompt_h3' not in plan['clips'][0]


def test_request_rejection_also_enters_agent_without_old_input_repair(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path)
    monkeypatch.setattr(agent,'propose',lambda *a:{'action':'stop','reason':'source needs repair','evidence':['input/evidence.json']})
    monkeypatch.setattr(managed,'reframe_candidate',lambda *a,**k:pytest.fail('old input repair must not run first'))
    result=managed.prepare_request_conflicts(d,{'blocked_clips':{'a':['request: bad wearing']}})
    assert result['skip_render'] and 'agent stopped' in result['blocked']['a']


def test_source_changed_during_agent_is_not_overwritten(tmp_path,monkeypatch):
    d,clips,_=configured(tmp_path)
    def decide(directory,clip,verdict):
        (directory/'chapter_script.json').write_text(json.dumps({'shots':[],'updated_elsewhere':True}))
        return {'action':'edit','reason':'fix','evidence':['input/prompt.txt'],
                'prompt':clip['prompt_h3'].replace('stands by','stands calmly by')}
    monkeypatch.setattr(agent,'propose',decide)
    result=managed.prepare(d,['a'])
    assert result['skip_render'] and 'inputs changed' in result['blocked']['a']
    assert json.loads((d/'chapter_script.json').read_text())['updated_elsewhere']
    assert json.loads((d/'clip_plan.json').read_text())['clips']==clips


def test_retention_description_can_clarify_clothes_but_not_change_reference():
    c=example();old=agent.sections(c['prompt_h3'])['retention_analysis']
    new=old.replace('identity, face, hair and clothing','identity, face, hair and cream shirt with dark vest')
    agent.validate_prompt(c,c['prompt_h3'].replace(old,new))
    bad=new.replace('<Picture 1>','<Picture 2>')
    with pytest.raises(ValueError,match='retention_analysis'):
        agent.validate_prompt(c,c['prompt_h3'].replace(old,bad))
    with pytest.raises(ValueError,match='retention_analysis'):
        agent.validate_prompt(c,c['prompt_h3'].replace(old,new.replace('fully_preserved','partially_preserved')))


def test_definition_self_reference_preserves_binding_but_other_tags_remain_locked():
    c = example()
    old = agent.sections(c['prompt_h3'])['subject_definitions']
    # A description on the same definition line may refer to its own subject.
    first, separator, rest = old.partition('\n')
    clarified = first + ' <Subject 1> wears the outfit from <Picture 1>.' + separator + rest
    agent.validate_prompt(c, c['prompt_h3'].replace(old, clarified))
    with pytest.raises(ValueError, match='subject_definitions'):
        crossed = first + ' It follows <Subject 2>.' + separator + rest
        agent.validate_prompt(c, c['prompt_h3'].replace(old, crossed))


def test_repair_accepts_fullwidth_compiled_dialogue_punctuation():
    c = example()
    agent.validate_prompt(c, c['prompt_h3'].replace('你好.', '你好。'))
    with pytest.raises(ValueError):
        agent.validate_prompt(c, c['prompt_h3'].replace('你好.', '你好？'))
