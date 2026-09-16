"""Controller refactors retain explicit adoption and pause semantics."""
import time
import pytest
import repair_manager_flow_thin as flow
import repair_manager_state_thin as state
import repair_manager_dispatch_thin as dispatch
import repair_manager_workers_thin as workers


@pytest.mark.parametrize('adopt', [False, True])
def test_paused_manager_only_adopts_when_requested(tmp_path, monkeypatch, adopt):
    manager=flow.Manager(tmp_path/'book',tmp_path/'old')
    manager.directory.mkdir(parents=True)
    (manager.directory/'pause').write_text('paused')
    manager.state.update(scan_started=True,summary={'deliverable_precise':0})
    manager.last_delivery=manager.last_refresh=time.monotonic()
    calls=[]
    monkeypatch.setattr(workers,'adopt',lambda m:calls.append('adopt'))
    monkeypatch.setattr(workers,'reap',lambda m:False)
    monkeypatch.setattr(dispatch,'schedule',lambda m:pytest.fail('paused manager cannot dispatch'))
    monkeypatch.setattr(workers,'launch',lambda m:pytest.fail('paused manager cannot launch'))
    monkeypatch.setattr(flow.time,'sleep',lambda seconds:(_ for _ in ()).throw(InterruptedError('end simulated tick')))
    with pytest.raises(InterruptedError):manager.run(adopt=adopt)
    assert calls==(['adopt'] if adopt else [])
    assert manager.state['status']=='paused' and manager.state['jobs']==[]
    assert (manager.directory/'pause').is_file()
