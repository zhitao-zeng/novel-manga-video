"""Episode first-take cost probe: one measured clip a day, never a real call.

_submit_probe and the ffprobe read are replaced; conftest fails any test that
touches a real transport, so nothing here can reach PhanRouter or an H3 box.
"""
import json
import os
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode import probe
from novel_manga.application.agents.episode.tools import EpisodeTools


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("PHANROUTER_", "NOVEL_")):
            monkeypatch.delenv(name, raising=False)


def make_ctx(tmp_path):
    novel = tmp_path / "book"
    episode = novel / "book_1"
    run = tmp_path / "run"
    work = run / "sandbox"
    for directory in (episode, run, work):
        directory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(novel_dir=novel, episode_dir=episode, run_dir=run,
                           work_dir=work, input_mode='source')


def log_rows(ctx):
    path = ctx.novel_dir / "probe-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def stub_submit(monkeypatch, calls):
    def fake_submit(ctx, env, **kwargs):
        calls.append(kwargs)
        output = ctx.run_dir / "probe.mp4"
        output.write_bytes(b"mp4")
        return output
    monkeypatch.setattr(probe, "_submit_probe", fake_submit)


PAID_ENV = {"NOVEL_VIDEO_MODEL": "sd2.5", "PHANROUTER_API_KEY": "video-secret"}


def test_first_probe_match_writes_log_and_second_call_same_day_skips(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    (ctx.novel_dir / "profile.json").write_text(
        json.dumps({"frame": "16:9", "resolution": "720p"}), encoding="utf-8")
    calls = []
    stub_submit(monkeypatch, calls)
    monkeypatch.setattr(probe, "_actual_resolution", lambda path: "1280x720")

    result = probe.maybe_probe(ctx, environ=PAID_ENV)
    assert result == {"match": True, "requested": "720p", "actual": "1280x720"}
    assert calls == [{"lane": "paid", "model": "sd2.5", "ratio": "16:9",
                      "resolution": "720p", "seconds": probe.PROBE_SECONDS}]
    rows = log_rows(ctx)
    assert len(rows) == 1
    row = rows[0]
    assert row["match"] is True and row["requested"] == "720p" and row["actual"] == "1280x720"
    assert row["model"] == "sd2.5" and row["lane"] == "paid" and row["date"]

    assert probe.maybe_probe(ctx, environ=PAID_ENV) is None
    assert len(calls) == 1


def test_resolution_mismatch_stops_render_before_a_runner_exists(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    (ctx.novel_dir / "profile.json").write_text(json.dumps({"tier": "fast"}), encoding="utf-8")
    (ctx.episode_dir / "clip_plan.json").write_text(
        json.dumps({"clips": [{"clip_id": "c1", "kind": "video", "request_seconds": 8}]}),
        encoding="utf-8")
    calls = []
    stub_submit(monkeypatch, calls)
    monkeypatch.setattr(probe, "_actual_resolution", lambda path: "1920x1080")
    monkeypatch.setenv("NOVEL_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_VIDEO_MODEL", "sd2.5")
    monkeypatch.setenv("PHANROUTER_API_KEY", "video-secret")
    runners = []
    monkeypatch.setattr(EpisodeTools, "_runner",
                        lambda self: runners.append(self) or pytest.fail("runner created"))

    result = EpisodeTools(ctx).render({})
    assert result == {"ok": False, "error": "resolution_mismatch",
                      "requested": "480p", "actual": "1920x1080"}
    assert runners == []
    assert calls and calls[0]["resolution"] == "480p"
    rows = log_rows(ctx)
    assert len(rows) == 1 and rows[0]["match"] is False


def test_probe_disabled_by_novel_probe_0(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    calls = []
    stub_submit(monkeypatch, calls)

    result = probe.maybe_probe(ctx, environ={**PAID_ENV, "NOVEL_PROBE": "0"})
    assert result is None
    assert calls == []
    assert not (ctx.novel_dir / "probe-log.jsonl").exists()


def test_local_h3_lane_records_throughput_not_price(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    calls = []
    stub_submit(monkeypatch, calls)
    env = {"NOVEL_LOCAL_H3_URL": "http://127.0.0.1:19700",
           "NOVEL_VIDEO_MODEL": "minimax-h3-ref2va-turbo"}

    result = probe.maybe_probe(ctx, environ=env)
    assert result["match"] is True
    assert calls == [{"lane": "local_h3", "model": "minimax-h3-ref2va-turbo", "ratio": "9:16",
                      "resolution": "720p", "seconds": probe.H3_PROBE_SECONDS}]
    rows = log_rows(ctx)
    assert len(rows) == 1
    assert rows[0]["lane"] == "local_h3" and rows[0]["seconds"] == probe.H3_PROBE_SECONDS
    assert isinstance(rows[0]["elapsed_s"], float)
    assert "requested" not in rows[0] and "match" not in rows[0]
