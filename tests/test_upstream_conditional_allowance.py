import copy
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.repair import agent, history, managed, upstream
from novel_manga.repair.proposal import RepairProposal
from novel_manga.util import atomic_write_json, read_json
from tests.test_repair_agent import configured


def exhausted_case(tmp_path, monkeypatch):
    directory, clips, verdicts = configured(tmp_path)
    record = history.load(directory)
    record['prior_generation_counts'] = {'clips': {'a': 3, 'b': 3}}
    history.save(directory, record)
    monkeypatch.setattr(agent, 'propose', lambda *a, **kw: {
        'action': 'input', 'reason': 'reviewed body and HD face', 'evidence': ['candidate']})
    def proposal(*args):
        plan = copy.deepcopy(read_json(directory/'clip_plan.json'))
        plan['clips'][0]['prompt'] = 'reviewed body and retained HD face'
        return RepairProposal({'changed': ['a']}, script=read_json(directory/'chapter_script.json'),
                              plan=plan, notes={}, changes={'a': {'action': 'input'}})
    monkeypatch.setattr(upstream, 'input_proposal', proposal)
    for _ in range(2):
        result = managed.prepare_agent_batch(directory, ['a'])
        assert result['needs_video'] == ['a'] and result['skip_render']
    return directory, clips, verdicts


def test_explicit_conditional_grant_reopens_one_staged_input_without_resetting_history(tmp_path, monkeypatch):
    directory, clips, _ = exhausted_case(tmp_path, monkeypatch)
    assert not upstream.available(directory, 'a')
    assert len(list((directory/'work/pending_inputs').glob('*.json'))) == 2
    review = read_json(directory/'episode_review.json')
    state = managed.input_state(directory, clips[0], review['clips']['a'],
                                managed.current_takes(directory, {'clips': clips}, review))
    atomic_write_json(directory/'repair_routing.json', {'a': {
        'status': 'blocked', 'reason': 'upstream exhausted', 'inputs': state}})
    assert 'a' not in managed.candidates(directory)[0]
    counts = history.generation_counts(directory)
    granted = history.grant_conditional_extra(directory, 'a', authorization='one explicit recovery', reason='reviewed body asset')
    assert granted['input_limit'] == 3 and not granted['prepared'] and not granted['activated']
    assert upstream.limit(directory, 'a') == 3 and upstream.available(directory, 'a')
    assert 'a' in managed.candidates(directory)[0]
    updated = managed.input_state(directory, clips[0], review['clips']['a'],
                                  managed.current_takes(directory, {'clips': clips}, review))
    assert updated['repair_allowances']['upstream_remaining'] == 1
    assert updated['repair_allowances']['video_remaining'] == 0
    assert not history.activate_extra_take(directory, 'a')  # grant alone cannot bypass preparation
    assert history.generation_limit(directory, 'a') == 3
    result = managed.prepare_agent_batch(directory, ['a'])
    assert result['changed'] == ['a'] and result['input_changed'] == ['a']
    assert len(upstream.records(directory, 'a')) == 3 and not upstream.available(directory, 'a')
    extra = history.conditional_extra(directory, 'a')
    assert extra['prepared'] and extra['activated'] and history.generation_limit(directory, 'a') == 4
    assert history.generation_counts(directory) == counts
    before = (directory/'repair_budget_grants.json').read_bytes()
    history.grant_conditional_extra(directory, 'a', authorization='one explicit recovery', reason='restart')
    assert (directory/'repair_budget_grants.json').read_bytes() == before
    assert not upstream.available(directory, 'a') and history.generation_counts(directory) == counts


def test_agent_sees_conditional_input_balance_without_video_permission(tmp_path, monkeypatch):
    real_propose = agent.propose
    directory, clips, verdicts = exhausted_case(tmp_path, monkeypatch)
    history.grant_conditional_extra(directory, 'a', authorization='one explicit recovery', reason='reviewed input')
    config = {'runs_root': str(tmp_path/'runs')}
    def frames(d, cid, video, target):
        target.mkdir(parents=True); frame = target/'frame.jpg'; frame.write_bytes(b'current frame')
        return [frame], 'fixture'
    monkeypatch.setattr(agent, 'provided_frames', frames)
    def execute(run, **kwargs):
        work = Path(config['runs_root'])/run
        choices = read_json(work/'input/upstream.json')
        assert choices['upstream_remaining'] == 1 and choices['video_remaining'] == 0
        atomic_write_json(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture', 'evidence': ['current JPEG']})
        return SimpleNamespace(ok=True, produced=['decision.json'], directory=work/'attempts/one')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert real_propose(directory, clips[0], verdicts['a'], config=config)['action'] == 'stop'
    assert not history.conditional_extra(directory, 'a')['prepared']
