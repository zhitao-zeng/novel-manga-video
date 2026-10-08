"""Episode first-take cost probe: one measured clip a day, never a real call.

_submit_probe and the ffprobe read are replaced; conftest fails any test that
touches a real transport, so nothing here can reach PhanRouter or an H3 box.
"""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import httpx
from types import SimpleNamespace

import pytest

from novel_manga.application.rendering import probe
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
    return [row for line in path.read_text(encoding="utf-8").splitlines()
            if (row:=json.loads(line)).get('status')!='started']


def stub_submit(monkeypatch, calls):
    def fake_submit(ctx, env, **kwargs):
        calls.append(kwargs)
        output = (getattr(ctx, 'run_dir', None) or ctx.episode_dir) / "probe.mp4"
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


def test_resolution_mismatch_still_blocks_a_second_call(tmp_path, monkeypatch):
    ctx=make_ctx(tmp_path)
    calls=[];stub_submit(monkeypatch,calls)
    monkeypatch.setattr(probe,'_actual_resolution',lambda path:'1920x1080')
    first=probe.maybe_probe(ctx,environ=PAID_ENV)
    second=probe.maybe_probe(ctx,environ=PAID_ENV)
    assert first['match'] is False and second['match'] is False
    assert len(calls)==1


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


def shared_contexts(tmp_path):
    from novel_manga.application.agents.episode.context import create_context
    book=tmp_path/'source-book';book.mkdir()
    (book/'novel.json').write_text(json.dumps({'novel_id':'book'}))
    rows=[]
    for chapter in (1,2):
        ctx=create_context(book,chapter,output_root=tmp_path/'runs',input_mode='source')
        ctx.novel_dir.mkdir(parents=True)
        (ctx.novel_dir/'profile.json').write_text(json.dumps({'resolution':'720p','frame':'16:9'}))
        rows.append(ctx)
    return rows


@pytest.mark.parametrize('parallel',[False,True])
def test_probe_is_once_for_the_source_book_across_real_workspaces(tmp_path,monkeypatch,parallel):
    contexts=shared_contexts(tmp_path);calls=[]
    def submit(ctx, env, **kwargs):
        calls.append(ctx.run_dir);time.sleep(.03)
        return ctx.run_dir/'probe.mp4'
    monkeypatch.setattr(probe,'_submit_probe',submit)
    monkeypatch.setattr(probe,'_actual_resolution',lambda _: '1280x720')
    if parallel:
        barrier=threading.Barrier(2)
        def check(ctx):barrier.wait();return probe.maybe_probe(ctx,environ=PAID_ENV)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(check,contexts))
    else:
        for ctx in contexts:probe.maybe_probe(ctx,environ=PAID_ENV)
    assert len(calls)==1
    assert (contexts[0].source_novel_dir/probe.LOG_NAME).is_file()


@pytest.mark.parametrize('changed',['model','endpoint','resolution','frame'])
def test_changed_submission_contract_is_checked_again(tmp_path,monkeypatch,changed):
    ctx=make_ctx(tmp_path);calls=[];stub_submit(monkeypatch,calls)
    monkeypatch.setattr(probe,'_actual_resolution',lambda _: '1280x720')
    probe.maybe_probe(ctx,environ=PAID_ENV)
    env=dict(PAID_ENV)
    if changed=='model':env['NOVEL_VIDEO_MODEL']='sd2.0'
    elif changed=='endpoint':env['PHANROUTER_BASE_URL']='https://different.invalid/phanrouter'
    else:(ctx.novel_dir/'profile.json').write_text(json.dumps({changed:'480p' if changed=='resolution' else '16:9'}))
    probe.maybe_probe(ctx,environ=env)
    assert len(calls)==2


def test_failed_probe_is_recorded_once_without_echoing_the_error_body(tmp_path,monkeypatch):
    ctx=make_ctx(tmp_path);calls=[]
    def fail(*a,**kw):calls.append(1);raise RuntimeError('credential-from-provider-response')
    monkeypatch.setattr(probe,'_submit_probe',fail)
    with pytest.raises(RuntimeError):probe.maybe_probe(ctx,environ=PAID_ENV)
    assert probe.maybe_probe(ctx,environ=PAID_ENV) is None
    assert len(calls)==1
    assert 'credential-from-provider-response' not in (ctx.novel_dir/probe.LOG_NAME).read_text()


def standard_runner(tmp_path,monkeypatch):
    from support.render_context import media_runner as runner
    from novel_manga.providers import phanrouter_video
    r=runner(tmp_path/'book',extra={'resolution':'480p'})
    requests=[]
    def answer(request):
        requests.append(request.method)
        if request.method=='POST':return httpx.Response(200,json={'task_id':'test-task'})
        return httpx.Response(200,json={'status':'succeeded','url':'https://media.invalid/video.mp4'})
    r.context.provider.client.close()
    r.context.provider.client=httpx.Client(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(phanrouter_video,'download_file',lambda client,url,path:path.write_bytes(b'video'))
    return r,requests


def test_standard_provider_blocks_mismatch_before_any_new_paid_task(tmp_path,monkeypatch):
    r,requests=standard_runner(tmp_path,monkeypatch)
    calls=[];stub_submit(monkeypatch,calls)
    monkeypatch.setattr(probe,'_actual_resolution',lambda _: '1920x1080')
    output=r.context.work/'clip.mp4'
    for _ in range(2):
        with pytest.raises(RuntimeError,match='resolution_mismatch'):
            r.context.provider.create_video('scene',None,output,4)
    assert requests==[] and len(calls)==1
    assert not output.with_suffix('.mp4.task.json').exists()


def test_standard_provider_resumes_an_existing_task_without_probe(tmp_path,monkeypatch):
    r,requests=standard_runner(tmp_path,monkeypatch)
    output=r.context.work/'clip.mp4'
    monkeypatch.setenv('NOVEL_PROBE','0')
    r.context.provider.create_video('scene',None,output,4)
    output.unlink();monkeypatch.delenv('NOVEL_PROBE')
    monkeypatch.setattr(probe,'_submit_probe',lambda *a,**kw:pytest.fail('resume bought a probe'))
    r.context.provider.create_video('scene',None,output,4)
    assert requests.count('POST')==1


def test_cache_only_renderer_does_not_probe(tmp_path,monkeypatch):
    from support.render_context import media_runner as runner, video_clip as clip
    from novel_manga.application.rendering import flow
    r=runner(tmp_path/'book');r.context.cache_only=True
    cached=tmp_path/'cached.mp4';cached.write_bytes(b'video')
    monkeypatch.setattr(flow.cache,'current_video',lambda *a,**kw:cached)
    monkeypatch.setattr(probe,'_submit_probe',lambda *a,**kw:pytest.fail('cache reuse bought a probe'))
    assert r.generate_clip(clip(),1)==cached


def test_interrupted_probe_is_not_bought_again_by_next_chapter(tmp_path,monkeypatch):
    contexts=shared_contexts(tmp_path);calls=[]
    def interrupted(*a,**kw):calls.append(1);raise SystemExit('interrupted after request')
    monkeypatch.setattr(probe,'_submit_probe',interrupted)
    with pytest.raises(SystemExit):probe.maybe_probe(contexts[0],environ=PAID_ENV)
    assert probe.maybe_probe(contexts[1],environ=PAID_ENV) is None
    assert len(calls)==1


def test_standard_workspace_carries_the_source_book_anchor(tmp_path,monkeypatch):
    from support.render_context import media_runner as runner
    source=tmp_path/'original';source.mkdir()
    monkeypatch.setenv('NOVEL_SOURCE_NOVEL_DIR',str(source))
    r=runner(tmp_path/'workspace')
    calls=[];stub_submit(monkeypatch,calls)
    monkeypatch.setattr(probe,'_actual_resolution',lambda _: '1280x720')
    probe.maybe_probe(r.context)
    assert (source/probe.LOG_NAME).exists()
    assert not (r.context.novel_dir/probe.LOG_NAME).exists()
