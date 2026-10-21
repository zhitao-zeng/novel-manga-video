"""Preparation cannot consume or renew the episode's generation/review allowance."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import pytest

from novel_manga.application.agents.episode import lifecycle
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json
from support.episode_runtime import episode_context as ctx, sandbox_config as config, install_fake_runtime


@pytest.fixture
def clock(monkeypatch):
    current = SimpleNamespace(now=100.0)
    monkeypatch.setattr(lifecycle, 'time', SimpleNamespace(
        monotonic=lambda: current.now, time=lambda: 10000+current.now,
        time_ns=lambda: int((10000+current.now)*1e9), strftime=time.strftime))
    return current


def bootstrap(ctx, config, monkeypatch, **kwargs):
    install_fake_runtime(monkeypatch, ctx)
    return lifecycle.start_run(ctx, config={**config, 'timeout_seconds': 100,
        'preparation_timeout_seconds': 200}, tools_factory=EpisodeTools, **kwargs).state


def test_full_asset_preparation_leaves_the_whole_media_window(ctx, config, monkeypatch, clock):
    state = bootstrap(ctx, config, monkeypatch)
    clock.now += 170  # Includes the late expression-card building inside render.
    persisted = []
    state.begin_generation(lambda **fields: persisted.append(fields))
    assert state.deadline-clock.now == 100
    assert state.result['phase_budget']['preparation']['used_seconds'] == 170
    assert state.result['phase_budget']['production']['used_seconds'] == 0
    assert persisted[-1]['phase_budget']['phase'] == 'production'


def test_first_two_workers_cannot_renew_the_media_window(ctx, config, monkeypatch, clock):
    state = bootstrap(ctx, config, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: state.begin_generation(lambda **fields: None), range(2)))
    deadline = state.deadline
    clock.now += 30
    state.begin_generation(lambda **fields: None)
    state.account_resources()
    assert state.deadline == deadline
    assert state.result['phase_budget']['production']['used_seconds'] == 30


def test_exhausted_preparation_never_starts_a_fresh_media_allowance(ctx, config, monkeypatch, clock):
    state = bootstrap(ctx, config, monkeypatch)
    clock.now += 201
    with pytest.raises(TimeoutError, match='preparation'):
        state.begin_generation(lambda **fields: None)
    assert state.result['phase_budget']['phase'] == 'preparation'


def test_shared_wait_is_credited_to_only_the_phase_where_it_happened(ctx, config, monkeypatch, clock):
    from novel_manga import resource_waits
    total = [40]
    monkeypatch.setattr(resource_waits, 'wait_seconds', lambda *a, **kw: total[0])
    state = bootstrap(ctx, config, monkeypatch)
    clock.now += 90
    state.begin_generation(lambda **fields: None)
    assert state.result['phase_budget']['preparation']['used_seconds'] == 50
    clock.now += 20
    total[0] += 10
    state.account_resources()
    assert state.result['phase_budget']['production']['used_seconds'] == 10
    assert state.deadline-clock.now == 90


def test_resume_and_changed_config_do_not_refresh_the_started_phase(ctx, config, monkeypatch, clock):
    state = bootstrap(ctx, config, monkeypatch)
    state.begin_generation(lambda **fields: None)
    clock.now += 30
    state.account_resources()
    saved = {**state.result, 'status': 'deferred', 'remaining_seconds': 70}
    atomic_write_json(ctx.run_dir/'agent-run.json', saved)
    clock.now += 1000  # Deferred queue/idle time is not activity.
    resumed = lifecycle.start_run(ctx, config={**config, 'timeout_seconds': 999},
        tools_factory=EpisodeTools).state
    resumed.begin_generation(lambda **fields: None)
    assert resumed.deadline-clock.now == 70
    assert resumed.result['phase_budget']['production']['limit_seconds'] == 100
    assert resumed.result['phase_budget']['production']['used_seconds'] == 30


def test_retry_does_not_renew_a_phase_budget(ctx, config, monkeypatch, clock):
    state = bootstrap(ctx, config, monkeypatch)
    state.begin_generation(lambda **fields: None)
    clock.now += 101
    state.account_resources()
    atomic_write_json(ctx.run_dir/'agent-run.json', {**state.result,
        'status': 'blocked', 'remaining_seconds': 0})
    resumed = bootstrap(ctx, config, monkeypatch, retry_issues=True)
    assert resumed.deadline <= clock.now
    assert resumed.result['phase_budget']['production']['used_seconds'] >= 100


def test_existing_exhausted_single_clock_is_not_implicitly_reclassified(ctx, config, monkeypatch, clock):
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'blocked', 'agent_activity': 'original', 'deadline': 10099,
        'remaining_seconds': 0, 'attempts': ['prior']})
    state = bootstrap(ctx, config, monkeypatch)
    assert state.result.get('phase_budget') is None
    state.begin_generation(lambda **fields: pytest.fail('legacy time was renewed'))
    assert state.deadline <= clock.now
    assert state.result['agent_activity'] == 'original'


def test_new_review_host_with_actual_media_starts_in_production(ctx, config, monkeypatch, clock):
    video = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'actual retained media')
    state = bootstrap(ctx, config, monkeypatch)
    assert state.result['phase_budget']['phase'] == 'production'
    assert state.deadline-clock.now == 100


def test_existing_evidenced_recovery_preserves_inflight_overtime(ctx, config, monkeypatch, clock):
    from novel_manga.application.agents.episode import bounded_recovery
    state = bootstrap(ctx, config, monkeypatch)
    state.begin_generation(lambda **fields: None)
    clock.now += 105
    state.account_resources()
    atomic_write_json(ctx.run_dir/'agent-run.json', {**state.result,
        'status': 'blocked', 'remaining_seconds': 0})
    def recovered(context, saved):
        return {**saved, 'status': 'deferred', 'remaining_seconds': 60,
                'upstream_recovery': {'seconds': 60}}
    monkeypatch.setattr(bounded_recovery, 'resume_window', recovered)
    resumed = bootstrap(ctx, config, monkeypatch)
    resumed.account_resources()
    assert resumed.deadline-clock.now == 60
    assert resumed.result['phase_budget']['production'] == {
        'limit_seconds': 165, 'used_seconds': 105, 'started_at': 10100}
