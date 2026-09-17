"""Controller refactors retain pause semantics after the completed handoff retires."""
import time
import pytest
import repair_manager_flow_thin as flow
import repair_manager_state_thin as state
import repair_manager_dispatch_thin as dispatch
import repair_manager_workers_thin as workers


def test_paused_manager_never_dispatches_or_changes_history(tmp_path, monkeypatch):
    manager=flow.Manager(tmp_path/'book',tmp_path/'old')
    manager.directory.mkdir(parents=True)
    (manager.directory/'pause').write_text('paused')
    manager.state.update(scan_started=True,summary={'deliverable_precise':0})
    manager.last_delivery=manager.last_refresh=time.monotonic()
    monkeypatch.setattr(workers,'reap',lambda m:False)
    monkeypatch.setattr(dispatch,'schedule',lambda m:pytest.fail('paused manager cannot dispatch'))
    monkeypatch.setattr(workers,'launch',lambda m:pytest.fail('paused manager cannot launch'))
    monkeypatch.setattr(flow.time,'sleep',lambda seconds:(_ for _ in ()).throw(InterruptedError('end simulated tick')))
    with pytest.raises(InterruptedError):manager.run()
    assert manager.state['status']=='paused' and manager.state['jobs']==[]
    assert (manager.directory/'pause').is_file()


@pytest.mark.parametrize('arguments', [['preview'], ['run', '--adopt-legacy']])
def test_completed_handoff_is_no_longer_a_production_entry(tmp_path, monkeypatch, arguments):
    import sys
    import manage_repair_thin
    monkeypatch.setattr(sys, 'argv', ['manage_repair_thin.py', *arguments, '--novel-dir', str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        manage_repair_thin.main()
    assert error.value.code == 2
    assert not (tmp_path / 'repair_manager').exists()
