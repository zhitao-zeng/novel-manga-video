from copy import deepcopy
from contextlib import nullcontext
import importlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from novel_manga.application.identity import flow, semantic_review
from novel_manga.llm import client
from novel_manga.util import atomic_write_json, read_json
from tests.test_identity_source_resume import actor, episode

REAL_BINDINGS = semantic_review.bindings


@pytest.fixture(autouse=True)
def normal_binding_service(monkeypatch, isolated_identity_semantic_service):
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)


def rejected_reading(tmp_path, monkeypatch):
    path = episode(tmp_path)
    calls = []
    wrong = actor();wrong['presence'] = 'mentioned'
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'source_problem': '', 'actors': [wrong]}
        return {'approved': False, 'reason': '甲实际在场', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)
    with pytest.raises(ValueError, match='甲实际在场'):
        flow.resolve_chapter(path)
    proposal = {'inputs': read_json(path/'identity_binding_review.json')['inputs'],
                'correction': {'actors': [actor()]}}
    return path, proposal, calls


def test_agent_identity_proposal_requires_a_fresh_normal_review(tmp_path, monkeypatch):
    path, proposal, calls = rejected_reading(tmp_path, monkeypatch)
    assert not (path/'identity_context.json').exists()
    def approve(*args, **kwargs):
        calls.append(kwargs['name'])
        assert kwargs['name'] == 'chapter_binding_review'
        return {'approved': True, 'reason': '甲在原文现场', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', approve)
    result = flow.resolve_chapter(path, proposal=proposal)
    assert calls == ['chapter_identity_source', 'chapter_binding_review',
                     'chapter_binding_review', 'chapter_binding_review']
    assert result['semantic_review']['approved'] is True
    assert result['source_actors'][0]['presence'] == 'on_stage'


@pytest.mark.parametrize('invalid', ['source_changed', 'invented_actor', 'unchanged_candidate'])
def test_agent_proposal_cannot_bypass_current_source_or_replay_the_same_judgment(tmp_path, monkeypatch, invalid):
    path, proposal, calls = rejected_reading(tmp_path, monkeypatch)
    if invalid == 'source_changed':
        proposal['inputs']['context']['current_segments'][0]['text'] = '另一个版本的原文。'
    elif invalid == 'invented_actor':
        proposal['correction']['actors'] = [actor('虚构人物')]
    else:
        proposal['correction'] = {'actors': proposal['inputs']['candidate']['actors']}
    before = list(calls)
    with pytest.raises(ValueError):
        flow.resolve_chapter(path, proposal=proposal)
    assert calls == before
    assert not (path/'identity_context.json').exists()


def repair_batch(tmp_path):
    book = tmp_path/'book';directory = book/'book_2'
    atomic_write_json(directory/'segments.json', [{'text': '甲走进屋里。'}])
    atomic_write_json(directory/'request_attempt_01.json', {'source_segments': [{'text': '甲走进屋里。'}]})
    atomic_write_json(directory/'response_attempt_01.raw.json', {'clips': []})
    atomic_write_json(directory/'repair_budget_grants.json', {'original': {'limit': 2, 'used': 2}})
    config = {'runs_root': str(tmp_path/'agents'), 'model': 'deepseek-v4.1-flash',
              'base_url': 'http://127.0.0.1:8789', 'timeout_seconds': 5400}
    return SimpleNamespace(novel_dir=book, novel_id='book', episode_dir=lambda n: directory,
        rows={2: {'plan': 'failed', 'note': 'ValueError: important dialogue missing'}},
        planning_stop=Event(), args=SimpleNamespace()), config


def test_managed_failure_reaches_its_agent_before_next_chapter_without_second_pass(tmp_path, monkeypatch):
    from novel_manga.application.production import flow as production, reports
    repair = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    batch.args = SimpleNamespace(plan_parallel=1, repair_planning=True,
        agent_config=tmp_path/'agent.json', dry_run=False)
    atomic_write_json(batch.args.agent_config, config)
    batch.rows[3] = {}
    events = []
    batch.grow = lambda n: None
    batch.plan = lambda n: events.append(('plan', n)) or batch.rows[n].update(plan='failed' if n == 2 else 'planned')
    batch.second_pass = lambda *a: pytest.fail('Agent-owned planning cannot start a second native batch')
    monkeypatch.setattr(repair, 'resume_failure', lambda *a: False)
    monkeypatch.setattr(reports, 'volume_checkpoint', lambda *a: None)
    def fix(target, chapter, **kwargs):
        events.append(('repair', chapter))
        return {'status': 'held', 'reason': 'real failure retained'}
    monkeypatch.setattr(repair, 'repair_chapter', fix)
    production.Batch.plan_stage(batch, [2, 3])
    assert events == [('plan', 2), ('repair', 2), ('plan', 3)]
    assert batch.rows[2]['planning_repair']['status'] == 'held'


@pytest.mark.parametrize('managed, expected', [(True, '0'), (False, '5')])
def test_only_agent_managed_native_planning_uses_one_initial_draft(tmp_path, monkeypatch, managed, expected):
    from tests.test_planning_waste import batch as native_batch
    batch = native_batch(tmp_path, monkeypatch)
    batch.args.repair_planning = managed
    calls = []
    batch.run = lambda command, path: calls.append(command) or (1, 'real source failure')
    batch.plan(1)
    assert calls[0][calls[0].index('--max-redo')+1] == expected
    assert batch.args.max_redo == 5 and batch.rows[1]['plan'] == 'failed'


def test_failed_plan_uses_existing_agent_then_returns_draft_to_the_normal_planner(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    calls = []
    before = (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes()
    def agent(run, prompt, **kwargs):
        calls.append(run)
        folder = Path(config['runs_root'])/run
        atomic_write_json(folder/'output/repair.json', {'kind': 'draft', 'draft': {'clips': []}})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', record={'seconds': 1},
                               produced=['repair.json'])
    monkeypatch.setattr(module, 'run_session', agent)
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    def plan(chapter, **kwargs):
        assert kwargs['candidate'].is_file()
        assert not (batch.episode_dir(2)/'clip_plan.json').exists()
        atomic_write_json(batch.episode_dir(2)/'clip_plan.json', {'validated_by': 'normal planner test double'})
        batch.rows[2]['plan'] = 'planned'
    batch.plan = plan
    result = module.repair_chapter(batch, 2, config=config)
    assert result['status'] == 'planned' and len(calls) == 1
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'kept'
    assert len(calls) == 1 and (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes() == before


def test_same_failed_agent_attempt_is_not_restarted_and_credit_failures_are_not_content_repairs(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    calls = []
    def agent(run, prompt, **kwargs):
        calls.append(run)
        folder = Path(config['runs_root'])/run
        atomic_write_json(folder/'output/repair.json', {'kind': 'unknown'})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', record={'seconds': 1},
                               produced=['repair.json'])
    monkeypatch.setattr(module, 'run_session', agent)
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    batch.plan = lambda *a, **k: pytest.fail('invalid proposal cannot reach normal planning')
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'
    assert len(calls) == 2  # feedback is attempted once; an identical rejected proposal stops
    batch.rows[2]['note'] = "httpx.HTTPStatusError: 402 Payment Required"
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'service_blocked'
    assert len(calls) == 2


def test_native_agent_credit_failure_stops_the_shared_queue(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    def agent(run, prompt, **kwargs):
        folder = Path(config['runs_root'])/run/'attempts/one';folder.mkdir(parents=True)
        (folder/'stderr.log').write_text('APIError: 402 Insufficient credits')
        return SimpleNamespace(exit_code=1, directory=folder, produced=[], record={'seconds': 1})
    monkeypatch.setattr(module, 'run_session', agent)
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    result = module.repair_chapter(batch, 2, config=config)
    assert result['status'] == 'service_blocked' and batch.planning_stop.is_set()


def test_agent_queue_wait_does_not_consume_activity_and_changed_inputs_do_not_reset_it(tmp_path, monkeypatch):
    from contextlib import contextmanager
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path);config['timeout_seconds'] = 10
    clock = [0.0];timeouts = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    @contextmanager
    def slot(*args, **kwargs):
        clock[0] += 100
        yield
    monkeypatch.setattr(module, 'model_slot', slot)
    def agent(run, prompt, **kwargs):
        timeouts.append(kwargs['timeout']);clock[0] += 2
        folder = Path(config['runs_root'])/run
        atomic_write_json(folder/'output/repair.json', {'kind': 'unknown'})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', produced=['repair.json'], record={})
    monkeypatch.setattr(module, 'run_session', agent)
    module.repair_chapter(batch, 2, config=config)
    batch.rows[2]['note'] = 'ValueError: a different actual content failure'
    module.repair_chapter(batch, 2, config=config)
    assert timeouts == [10, 8]  # changed diagnostics do not renew the same logical action
    atomic_write_json(batch.episode_dir(2)/'segments.json', [{'text': '甲走出屋里。'}])
    module.repair_chapter(batch, 2, config=config)
    record = read_json(batch.episode_dir(2)/'planning-repair.json')
    assert timeouts == [10, 8, 6, 4] and record['used_seconds'] == 8


def test_an_old_agent_output_is_not_adopted_when_current_attempt_produced_nothing(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    def agent(run, prompt, **kwargs):
        folder = Path(config['runs_root'])/run
        atomic_write_json(folder/'output/repair.json', {'kind': 'draft', 'draft': {'clips': []}})
        return SimpleNamespace(exit_code=0, directory=folder/'attempts/one', produced=[], record={})
    monkeypatch.setattr(module, 'run_session', agent)
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    batch.plan = lambda *a, **k: pytest.fail('stale output cannot become the current plan')
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'


def test_planning_entry_does_not_report_success_when_automatic_repair_remains_held(tmp_path, monkeypatch):
    import sys
    from novel_manga.application.production import batch_cli, flow as production, reports
    batch = SimpleNamespace(novel_id='book', novel_dir=tmp_path, source=tmp_path/'source.txt',
        rows={}, planning_stop=Event(), plan_status=lambda n: 'missing', check_qwen=lambda: None)
    def stage(chapters):
        batch.rows.update({n: {'plan': 'failed', 'planning_repair': {'status': 'held'}} for n in chapters})
    batch.plan_stage = stage
    monkeypatch.setattr(production, 'Batch', lambda args: batch)
    monkeypatch.setattr(reports, 'report', lambda *a: 0)
    monkeypatch.setattr(sys, 'argv', ['thin_batch.py', '--novel-dir', str(tmp_path),
        '--stage', 'plan', '--repair-planning'])
    assert batch_cli.main() == 2


def test_held_repair_restart_does_not_renew_authoring_or_second_pass(tmp_path, monkeypatch):
    from novel_manga.application.production.flow import Batch
    from novel_manga.application.production import reports
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    monkeypatch.setattr(module, 'run_session', lambda *a, **k: SimpleNamespace(
        exit_code=0, directory=tmp_path/'attempt', produced=[], record={}))
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'
    before = (batch.episode_dir(2)/'planning-repair.json').read_bytes()
    batch.args = SimpleNamespace(plan_parallel=1, repair_planning=True, agent_config=tmp_path/'agent.json', dry_run=False)
    atomic_write_json(batch.args.agent_config, config)
    batch.grow = lambda *a: pytest.fail('held repair cannot restart growth')
    batch.plan = lambda *a, **k: pytest.fail('held repair cannot renew author attempts')
    batch.second_pass = lambda *a: pytest.fail('held repair cannot enter second pass')
    monkeypatch.setattr(reports, 'volume_checkpoint', lambda *a: None)
    Batch.plan_stage(batch, [2])
    assert batch.rows[2]['planning_repair']['status'] == 'held'
    assert (batch.episode_dir(2)/'planning-repair.json').read_bytes() == before


def test_failure_packet_uses_one_recorded_current_attempt_not_largest_stale_files(tmp_path):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, _ = repair_batch(tmp_path)
    path = batch.episode_dir(2)
    for n in [1, 5]:
        atomic_write_json(path/f'response_attempt_{n:02d}.raw.json', {'clips': [], 'attempt': n})
        atomic_write_json(path/f'request_attempt_{n:02d}.json', {'attempt': n})
        atomic_write_json(path/f'analysis_attempt_{n:02d}.txt', {'attempt': n})
    atomic_write_json(path/'planning_failed.json', {'attempts': [{'attempt': 1, 'stage': 'draft'}]})
    inputs = module.failure_inputs(batch, 2)
    assert inputs['candidate']['attempt'] == inputs['planning_input']['attempt'] == inputs['outline']['attempt'] == 1
    atomic_write_json(path/'planning_attempt.json', {'attempt': 1, 'planning_input': {'attempt': 'current'},
        'candidate': None, 'outline': None})
    inputs = module.failure_inputs(batch, 2)
    assert inputs['planning_input']['attempt'] == 'current'
    assert inputs['candidate'] is None and inputs['outline'] is None


def test_old_service_failure_returns_to_native_planning_instead_of_semantic_agent(tmp_path, monkeypatch):
    from novel_manga.application.production.flow import Batch
    from novel_manga.application.production import reports
    batch, config = repair_batch(tmp_path)
    path = batch.episode_dir(2)
    atomic_write_json(path/'planning_failed.json', {'errors': ['HTTPStatusError: 502 Bad Gateway']})
    batch.args = SimpleNamespace(plan_parallel=1, repair_planning=True, agent_config=tmp_path/'agent.json', dry_run=False)
    atomic_write_json(batch.args.agent_config, config)
    calls = []
    batch.grow = lambda n: None
    def plan(n):
        calls.append(n)
        atomic_write_json(path/'clip_plan.json', {'native': True})
        batch.rows[n]['plan'] = 'planned'
    batch.plan = plan
    batch.second_pass = lambda *a: None
    monkeypatch.setattr(reports, 'volume_checkpoint', lambda *a: None)
    Batch.plan_stage(batch, [2])
    assert calls == [2] and 'planning_repair' not in batch.rows[2]


def test_ungrounded_source_failure_enters_identity_repair_and_normal_review(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    path = episode(tmp_path)
    bad = actor('假名字')
    def fail(*args, **kwargs):
        assert kwargs['name'] in {'chapter_identity_source', 'chapter_identity_evidence'}
        return {'source_readable': True, 'source_problem': '', 'actors': [bad]}
    monkeypatch.setattr(client, 'ask_json', fail)
    with pytest.raises(ValueError, match='lack grounded names'):
        flow.resolve_chapter(path)
    batch = SimpleNamespace(episode_dir=lambda n: path, rows={1: {'note': 'identity actors lack grounded names'}})
    inputs = module.failure_inputs(batch, 1)
    assert inputs['kind'] == 'identity_source'
    assert inputs['source_reading']['answer']['actors'] == [bad]
    assert inputs['evidence_repair']['answer']['actors'] == [bad]
    calls = []
    def approve(*args, **kwargs):
        calls.append(kwargs['name'])
        assert kwargs['name'] == 'chapter_binding_review'
        return {'approved': True, 'reason': '原文支持甲', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', approve)
    result = flow.resolve_chapter(path, source_proposal={'inputs': inputs['source_reading'],
        'correction': {'actors': [actor()]}})
    assert result['source_actors'][0]['name'] == '甲'
    assert result['semantic_review']['approved'] and calls == ['chapter_binding_review']


@pytest.mark.parametrize('invalid', ['stale_source', 'invented_form', 'no_change'])
def test_source_proposal_still_rejects_stale_or_ungrounded_input(tmp_path, monkeypatch, invalid):
    from novel_manga.application.identity.store import chapter_inputs
    path = episode(tmp_path)
    saved = {'policy': flow.POLICY, 'inputs': chapter_inputs(path),
             'answer': {'source_readable': True, 'actors': [actor()]}}
    atomic_write_json(path/'identity_source_reading.json', saved)
    proposal = {'inputs': deepcopy(saved), 'correction': {'actors': [actor()]}}
    if invalid == 'stale_source':
        proposal['inputs']['inputs']['segments'][0]['text'] = '另一章'
    elif invalid == 'invented_form':
        proposal['correction']['actors'] = [actor('不存在的人')]
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: pytest.fail('invalid proposal must not request approval'))
    with pytest.raises(ValueError):
        flow.resolve_chapter(path, source_proposal=proposal)
    assert not (path/'identity_context.json').exists()


def test_failed_validation_returns_to_same_agent_with_feedback_and_remaining_budget(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path);config['timeout_seconds'] = 10
    clock = [0.0];calls = [];checks = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    original_budget = (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes()
    def agent(folder, prompt, **kwargs):
        folder = Path(folder)
        calls.append((folder, kwargs['resume'], kwargs['timeout']))
        if len(calls) == 2:
            feedback = read_json(folder/'input/planning-feedback.json')
            assert '对白归属仍错' in feedback['reason']
            assert feedback['proposal']['draft']['revision'] == 1
        atomic_write_json(folder/module.SESSION_FILE, {'session_id': 'same-conversation'})
        atomic_write_json(folder/'output/repair.json', {'kind': 'draft',
            'draft': {'revision': len(calls)}, 'outline': {}})
        clock[0] += 2
        return SimpleNamespace(exit_code=0, directory=folder/f'attempts/{len(calls)}', produced=['repair.json'])
    def plan(n, **kwargs):
        checks.append(read_json(kwargs['candidate'])['draft']['revision'])
        if len(checks) == 1:
            batch.rows[n].update(plan='failed', note='对白归属仍错')
        else:
            atomic_write_json(batch.episode_dir(n)/'clip_plan.json', {'checked': True})
            batch.rows[n]['plan'] = 'planned'
    batch.plan = plan;monkeypatch.setattr(module, 'run_session', agent)
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'planned'
    assert checks == [1, 2]
    assert calls[0][0] == calls[1][0] and [c[1:] for c in calls] == [(False, 10), (True, 8)]
    record = read_json(batch.episode_dir(2)/'planning-repair.json')
    assert record['initial_seconds'] == 10 and record['used_seconds'] == 4
    assert (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes() == original_budget


def test_explanation_only_identity_edit_stops_without_rechecking_or_renewing_budget(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path)
    clock = [0.0]
    calls, checks = [], []
    before = (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes()
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    packet = {'kind': 'identity', 'source_segments': [{'text': '甲走进屋里。'}],
        'identity_inputs': {'candidate': {}}, 'failure': '原文归属仍未成立'}
    monkeypatch.setattr(module, 'failure_inputs', lambda *a: deepcopy(packet))
    def agent(folder, prompt, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) > 2:
            pytest.fail('explanation-only revisions kept running')
        clock[0] += 2
        atomic_write_json(Path(folder)/'output/repair.json', {'kind': 'identity', 'correction': {
            'bindings': [{'source_id': 1, 'entity_id': 'UNKNOWN', 'decision': 'extra',
                          'paragraphs': [1], 'reason': f'不同措辞{len(calls)}'}], 'actors': [], 'removed_source_ids': []}})
        return SimpleNamespace(exit_code=0, directory=Path(folder)/f'attempts/{len(calls)}', produced=['repair.json'])
    def reject(*a, **k):
        checks.append(k)
        raise ValueError('同一归属仍未成立')
    monkeypatch.setattr(module, 'run_session', agent)
    monkeypatch.setattr(flow, 'resolve_chapter', reject)
    batch.plan = lambda *a, **k: pytest.fail('unapproved identity cannot be published')
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'
    assert module.repair_chapter(batch, 2, config=config)['status'] == 'held'
    record = read_json(batch.episode_dir(2)/'planning-repair.json')
    assert calls == [5400, 5398] and len(checks) == 1
    assert record['used_seconds'] == 4 and record['initial_seconds'] == 5400
    assert (batch.episode_dir(2)/'repair_budget_grants.json').read_bytes() == before


def test_repeated_malformed_identity_delta_still_counts_as_no_progress():
    module = importlib.import_module('novel_manga.application.planning.repair')
    invalid = {'kind': 'identity', 'correction': 'not an object'}
    assert module.same_proposal(invalid, deepcopy(invalid))
    left = {'kind': 'identity', 'correction': {'actors': [], 'bindings': [], 'removed_source_ids': [1]}}
    right = deepcopy(left)
    right['correction']['removed_source_ids'] = [2]
    assert not module.same_proposal(left, right)


def test_unrelated_catalogue_and_other_chapter_notes_do_not_reopen_a_held_repair(tmp_path):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, _ = repair_batch(tmp_path);book = batch.novel_dir
    atomic_write_json(book/'story_bible.json', {'characters': [{'name': '甲', 'role': '人物', 'appearance': '青衣'}], 'locations': []})
    batch.notes = {'2': '本章备注', '9': '其他章备注'}
    before = module.current_context(batch, 2)
    atomic_write_json(batch.episode_dir(2)/'planning-repair.json', {'context': before,
        'inputs': {'failure': '原失败'}, 'status': 'held', 'used_seconds': 7})
    atomic_write_json(book/'story_bible.json', {'characters': [
        {'name': '甲', 'role': '人物', 'appearance': '青衣'}, {'name': '乙', 'role': '人物', 'appearance': '红衣'}], 'locations': []})
    batch.notes['9'] = '其他章修改'
    assert module.current_context(batch, 2) == before
    assert module.resume_failure(batch, 2)
    bible = read_json(book/'story_bible.json');bible['characters'][0]['appearance'] = '白衣'
    atomic_write_json(book/'story_bible.json', bible)
    assert module.current_context(batch, 2) != before
    assert not module.resume_failure(batch, 2)


def test_feedback_loop_stops_at_original_budget_and_does_not_restart_held_work(tmp_path, monkeypatch):
    module = importlib.import_module('novel_manga.application.planning.repair')
    batch, config = repair_batch(tmp_path);config['timeout_seconds'] = 3
    clock = [0.0];calls = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'model_slot', lambda *a, **k: nullcontext())
    def agent(folder, prompt, **kwargs):
        calls.append(kwargs['timeout']);clock[0] += 2
        atomic_write_json(Path(folder)/'output/repair.json', {'kind':'draft','draft':{'revision':len(calls)},'outline':{}})
        return SimpleNamespace(exit_code=0,directory=Path(folder)/f'attempts/{len(calls)}',produced=['repair.json'])
    monkeypatch.setattr(module,'run_session',agent)
    batch.plan=lambda *a,**k: batch.rows[2].update(note='仍有真实缺陷')
    assert module.repair_chapter(batch,2,config=config)['status']=='held'
    assert calls==[3,1]
    assert module.repair_chapter(batch,2,config=config)['status']=='held' and calls==[3,1]


def test_source_correction_survives_unrelated_catalogue_growth_but_still_gets_review(tmp_path, monkeypatch):
    from novel_manga.application.identity.store import chapter_inputs
    path = episode(tmp_path)
    saved = {'policy':flow.POLICY,'inputs':chapter_inputs(path),
             'answer':{'source_readable':True,'actors':[actor('错名字')]}}
    atomic_write_json(path/'identity_source_reading.json',saved)
    bible = read_json(path.parent/'story_bible.json')
    bible['characters'].append({'name':'乙','role':'人物'})
    atomic_write_json(path.parent/'story_bible.json',bible)
    calls=[]
    def approve(*a,**k):
        calls.append(k['name'])
        assert k['name']=='chapter_binding_review'
        return {'approved':True,'reason':'原文支持','correction':None}
    monkeypatch.setattr(client,'ask_json',approve)
    result=flow.resolve_chapter(path,source_proposal={'inputs':saved,'correction':{'actors':[actor()]}})
    assert result['semantic_review']['approved'] and calls==['chapter_binding_review']


def test_repeated_rejected_candidate_does_not_buy_another_judgment(tmp_path, monkeypatch):
    module=importlib.import_module('novel_manga.application.planning.repair')
    batch,config=repair_batch(tmp_path);calls=[];checks=[]
    monkeypatch.setattr(module,'model_slot',lambda *a,**k:nullcontext())
    def agent(folder,prompt,**kwargs):
        calls.append(kwargs)
        atomic_write_json(Path(folder)/'output/repair.json',{'kind':'draft','draft':{'same':True},'outline':{}})
        return SimpleNamespace(exit_code=0,directory=Path(folder)/f'attempts/{len(calls)}',produced=['repair.json'])
    monkeypatch.setattr(module,'run_session',agent)
    batch.plan=lambda *a,**k:checks.append(1)
    assert module.repair_chapter(batch,2,config=config)['status']=='held'
    assert len(calls)==2 and checks==[1]
    assert module.repair_chapter(batch,2,config=config)['status']=='held' and len(calls)==2


def test_related_location_changes_invalidate_but_unrelated_locations_do_not(tmp_path):
    module=importlib.import_module('novel_manga.application.planning.repair')
    batch,_=repair_batch(tmp_path)
    bible={'characters':[],'locations':['屋里：青色墙壁']}
    path=batch.novel_dir/'story_bible.json';atomic_write_json(path,bible)
    before=module.current_context(batch,2)
    bible['locations'].append('宫殿：金色大殿');atomic_write_json(path,bible)
    assert module.current_context(batch,2)==before
    bible['locations'][0]='屋里：白色墙壁';atomic_write_json(path,bible)
    assert module.current_context(batch,2)!=before


def test_second_identity_revision_uses_the_saved_rejected_candidate(tmp_path, monkeypatch):
    path, first, _ = rejected_reading(tmp_path, monkeypatch)
    first['correction']['actors'][0]['appearance']='红衣'
    monkeypatch.setattr(client,'ask_json',lambda *a,**k:{'approved':False,'reason':'原文没有红衣','correction':None})
    with pytest.raises(ValueError,match='原文没有红衣'):
        flow.resolve_chapter(path,proposal=first)
    latest=read_json(path/'identity_binding_review.json')
    assert latest['inputs']['candidate']['actors'][0]['appearance']=='红衣'
    calls=[]
    def approve(*a,**k):
        calls.append(k['name'])
        return {'approved':True,'reason':'当前原文支持','correction':None}
    monkeypatch.setattr(client,'ask_json',approve)
    result=flow.resolve_chapter(path,proposal={'inputs':latest['inputs'],'correction':{'actors':[actor()]}})
    assert result['semantic_review']['approved'] and calls==['chapter_binding_review']


def test_effective_alias_change_invalidates_even_when_both_targets_are_already_selected(tmp_path):
    module=importlib.import_module('novel_manga.application.planning.repair')
    batch,_=repair_batch(tmp_path);path=batch.episode_dir(2)
    atomic_write_json(path/'segments.json',[{'text':'小甲看着甲和乙。'}])
    atomic_write_json(path/'identity_source_reading.json',{'answer':{'actors':[actor('小甲')]}})
    atomic_write_json(batch.novel_dir/'story_bible.json',{'characters':[
        {'name':'甲','role':'人物'},{'name':'乙','role':'人物'}],'locations':[]})
    reading=batch.novel_dir/'reading_cast.json'
    atomic_write_json(reading,{'aliases':{'小甲':'甲'}})
    before=module.current_context(batch,2)
    atomic_write_json(reading,{'aliases':{'小甲':'甲','其他人':'乙'}})
    assert module.current_context(batch,2)==before
    atomic_write_json(reading,{'aliases':{'小甲':'乙','其他人':'乙'}})
    assert module.current_context(batch,2)!=before


def test_failed_source_paragraphs_can_reach_repair_without_being_silently_fixed(tmp_path):
    module=importlib.import_module('novel_manga.application.planning.repair')
    batch,_=repair_batch(tmp_path);path=batch.episode_dir(2)
    bad=actor();bad.update(paragraphs=[2],appearance='青衣')
    atomic_write_json(path/'identity_source_reading.json',{'answer':{'actors':[bad]}})
    before=(path/'identity_source_reading.json').read_bytes()
    context=module.current_context(batch,2)
    packet=module.failure_inputs(batch,2)
    assert packet['kind']=='identity_source'
    assert packet['source_reading']['answer']['actors'][0]['paragraphs']==[2]
    assert context['source_actors'][0]['paragraphs']==[2]
    assert (path/'identity_source_reading.json').read_bytes()==before


def test_first_agent_delta_keeps_the_native_correction_from_the_last_review(tmp_path, monkeypatch):
    path=episode(tmp_path)
    initial=actor();initial['presence']='mentioned'
    def ask(parts,schema,**kwargs):
        if kwargs['name']=='chapter_identity_source':
            return {'source_readable':True,'source_problem':'','actors':[initial]}
        candidate=json.loads(parts[0]['text'].split('\n')[-1])['candidate']
        if candidate['actors'][0]['presence']=='mentioned':
            return {'approved':False,'reason':'甲在场','correction':{'actors':[actor()]}}
        return {'approved':False,'reason':'还需补明黑发外貌','correction':None}
    monkeypatch.setattr(client,'ask_json',ask)
    with pytest.raises(ValueError,match='还需补明'):
        flow.resolve_chapter(path)
    module=importlib.import_module('novel_manga.application.planning.repair')
    batch=SimpleNamespace(episode_dir=lambda n:path, rows={1:{'note':'identity unresolved'}})
    inputs=module.failure_inputs(batch,1)
    assert inputs['identity_inputs']['candidate']['actors'][0]['presence']=='on_stage'
    changed=deepcopy(inputs['identity_inputs']['candidate']['actors'][0]);changed['appearance']='黑发'
    seen=[]
    def approve(parts,*a,**k):
        value=json.loads(parts[0]['text'].split('\n')[-1])['candidate'];seen.append(value)
        assert value['actors'][0]['presence']=='on_stage' and value['actors'][0]['appearance']=='黑发'
        return {'approved':True,'reason':'两项原文均成立','correction':None}
    monkeypatch.setattr(client,'ask_json',approve)
    result=flow.resolve_chapter(path,proposal={'inputs':inputs['identity_inputs'],
                                              'correction':{'actors':[changed]}})
    assert result['semantic_review']['approved'] and len(seen)==1


def test_corrected_source_positions_do_not_send_old_cited_errors_back_to_the_judge(tmp_path, monkeypatch):
    initial={'actors':[dict(actor(),presence='mentioned')]}
    current={'actors':[actor()]};current['actors'][0]['forms'][0].update(paragraphs=[1],cited=[7])
    context={'chapter':1}
    pending={'approved':False,'inputs':{'context':context,'candidate':initial},'steps':[
        {'kind':'review','candidate':current,'answer':{'approved':False,'reason':'旧7段不存在','correction':None}}]}
    calls=[]
    def approve(parts,*a,**k):
        view=json.loads(parts[0]['text'].split('\n')[-1])['candidate'];calls.append(view)
        assert view['actors'][0]['presence']=='on_stage'
        assert view['actors'][0]['forms'][0]['paragraphs']==[1]
        assert 'cited'not in view['actors'][0]['forms'][0]
        return {'approved':True,'reason':'当前引用与身份成立','correction':None}
    monkeypatch.setattr(client,'ask_json',approve)
    result,_=semantic_review.close(initial,context,{},name='review',pending=pending,record=tmp_path/'review.json')
    assert len(calls)==1 and result==current
    record=read_json(tmp_path/'review.json');step=record['steps'][-1]
    assert step['candidate']['actors'][0]['forms'][0]['cited']==[7]
    assert 'cited'not in step['reviewed_view']['actors'][0]['forms'][0]
    step['answer']={'approved':False,'reason':'未出现新的身份修订','correction':None}
    record['approved']=False;record['steps']=[step]
    record['inputs']['candidate']=current
    with pytest.raises(ValueError,match='未出现新的身份修订'):
        semantic_review.close(current,context,{},name='review',pending=record)
    assert len(calls)==1
    clean=deepcopy(current);clean['actors'][0]['forms'][0].pop('cited')
    assert semantic_review.same_candidate(current,clean)


def test_corrected_literal_ranges_reach_actor_and_binding_without_changing_presence(tmp_path,monkeypatch):
    path=episode(tmp_path)
    atomic_write_json(path/'segments.json',[{'segment_id':'seg_1','text':'那人站着。'},
                                         {'segment_id':'seg_2','text':'甲走了过来。'}])
    wrong=actor();wrong['presence']='mentioned';wrong['forms'][0]['paragraphs']=[1]
    seen=[]
    def ask(parts,*a,**kw):
        if kw['name']=='chapter_identity_source':return {'source_readable':True,'source_problem':'','actors':[wrong]}
        candidate=json.loads(parts[0]['text'].split('\n')[-1])['candidate'];seen.append(candidate)
        assert candidate['actors'][0]['paragraphs']==[1,2]
        assert candidate['actors'][0]['presence']=='mentioned'
        assert candidate['bindings'][0]['paragraphs']==[1,2]
        assert candidate['actors'][0]['forms'][0]['paragraphs']==[2]
        assert 'cited'not in candidate['actors'][0]['forms'][0]
        return {'approved':True,'reason':'仅检查引用范围同步的离线夹具','correction':None}
    monkeypatch.setattr(client,'ask_json',ask)
    result=flow.resolve_chapter(path)
    assert result['semantic_review']['approved'] and len(seen)==1
    assert wrong['paragraphs']==[1] and wrong['forms'][0]['paragraphs']==[1]


def test_protocol_repair_schema_uses_the_same_actor_scope_as_the_host_guard(monkeypatch):
    from novel_manga.story.source_identity import source_schema
    one=actor();one['presence']='mentioned';two=deepcopy(actor());two.update(source_id=2,name='乙')
    original={'actors':[one,two],'bindings':[{'source_id':1},{'source_id':2}]}
    fixed=actor();bad=deepcopy(fixed);bad['name']='无据名字'
    schema=client.obj({'actors':source_schema(1)['properties']['actors'],
        'bindings':{'type':'array','items':client.obj({'source_id':{'type':'integer'}})},
        'removed_source_ids':{'type':'array','items':{'type':'integer'}}})
    calls=[]
    def ask(parts,contract,**kw):
        calls.append(kw['name'])
        if kw['name']=='review_correction':
            for field in ['actors','bindings']:
                assert contract['properties'][field]['maxItems']==1
                assert contract['properties'][field]['items']['properties']['source_id']['enum']==[1]
            assert contract['properties']['removed_source_ids']['items']['enum']==[1]
            return {'actors':[fixed],'bindings':[],'removed_source_ids':[]}
        return ({'approved':False,'reason':'修甲的在场','correction':{'actors':[bad]}}
                if len(calls)==1 else {'approved':True,'reason':'复核成立','correction':None})
    def validate(value):
        if value['actors'][0]['name']=='无据名字':raise ValueError('ungrounded name')
        return value
    monkeypatch.setattr(client,'ask_json',ask)
    result,reviewed=semantic_review.close(original,{},schema,name='review',validate=validate,
                                          merge_correction=semantic_review.merge_binding_changes)
    assert reviewed['approved'] and result['actors'][1]==two
    assert calls==['review','review_correction','review']
