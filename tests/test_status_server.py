"""The monitor reports current qualified finals, and keeps unfinished reviews visible."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import status_server as monitor  # noqa: E402
from thin_profile import h3_prompt_fingerprint, plan_fingerprint  # noqa: E402
from thin_runs import count_run  # noqa: E402

NOVEL = {"id": "nov", "title": "测试小说", "conductor": None}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "_lane_keys", lambda: {"nov": [{"base_url": "pool"}]})
    monkeypatch.setattr(monitor, "_EPISODE_CACHE", {})
    monkeypatch.setattr(monitor, "_FILE_CACHE", {})
    monkeypatch.setattr(monitor, "_MODE_CACHE", {})
    return tmp_path / "outputs" / "nov"


def write(path, data):
    previous = path.stat().st_mtime if path.exists() else 0
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    stamp = max(time.time(), previous + 1)
    os.utime(path, (stamp, stamp))


def episode(workspace, n=1, *, passed=True, gate_failed=False, failed=False, clips=1):
    directory = workspace / f"nov_{n}"
    directory.mkdir(parents=True)
    plan = {"policy": "thin-15s", "clips": [
        {"clip_id": f"clip_{i:02d}", "kind": "video", "prompt": f"镜头{i}", "prompt_h3": f"shot {i}",
         "references": [], "request_seconds": 10} for i in range(1, clips + 1)]}
    write(directory / "clip_plan.json", plan)
    write(directory / "thin_media_report.json", {
        "clip_plan_fingerprint": plan_fingerprint(plan), "prompt_h3_fingerprint": h3_prompt_fingerprint(plan),
        "review_feedback": {}, "failed_clips": ["clip_01"] if failed else [],
        "gate_failed_clips": ["clip_01"] if gate_failed else [], "assembly": None if failed else {"thin_passed": passed},
        "clips": [{"clip_id": c["clip_id"], "attempts": [{}], "error": "SubmissionUncertain: answer lost" if failed else None}
                  for c in plan["clips"]],
    })
    if not failed:
        (directory / f"nov_{n}.mp4").write_bytes(b"final")
    return directory, plan


def review(directory, severities, *, fresh=True):
    path = directory / "episode_review.json"
    write(path, {"clips": {f"clip_{i:02d}": {"severity": severity} for i, severity in enumerate(severities, 1)}})
    final = (directory / f"{directory.name}.mp4").stat().st_mtime
    stamp = final + 1 if fresh else final - 1
    os.utime(path, (stamp, stamp))


def test_live_and_board_count_only_current_qualified_finals(workspace):
    good, _ = episode(workspace, 1)
    review(good, ["pass"])
    episode(workspace, 2, passed=False)
    stale, plan = episode(workspace, 3)
    plan["clips"][0]["prompt"] = "重写后的镜头"
    write(stale / "clip_plan.json", plan)
    english, plan = episode(workspace, 4)
    plan["clips"][0]["prompt_h3"] = "new English prompt"
    write(english / "clip_plan.json", plan)
    episode(workspace, 5, failed=True)
    orphan, _ = episode(workspace, 6)
    (orphan / "thin_media_report.json").unlink()
    episode(workspace, 7, gate_failed=True)
    exhausted, _ = episode(workspace, 8, gate_failed=True)
    for _ in range(3):
        count_run(exhausted)

    live = monitor._novel_status(NOVEL)
    board = monitor._board_novel(NOVEL)
    assert live["files"] == 7 and live["planned"] == 8 and live["done"] == board["done"] == 1
    assert live["today"] == live["per_hour"] == 1 and live["left"] == 7
    assert sum(count for _, count in board["daily"]) == 1
    assert live["states"] == {"done": 1, "done_with_warnings": 3, "stale": 2, "clips_failed": 1, "pending": 1}
    assert live["blocked"] == 3 and live["uncertain"] == 1
    assert live["eta_hours"] is None and board["projected_ts"] is None and board["projected"] is None
    reasons = {r["chapter"]: r["reason"] for r in live["attention"]}
    assert "核账" in reasons[5] and "重试次数用尽" in reasons[8] and "重新验证" in reasons[6]


def test_state_cache_tracks_feedback_report_final_and_review_changes(workspace):
    directory, plan = episode(workspace)
    assert monitor._episode_state(directory, True)["status"] == "done"
    write(directory / "review_feedback.json", {"clip_01": "调整表情"})
    assert monitor._episode_state(directory, True)["status"] == "stale"
    write(directory / "review_feedback.json", {})
    assert monitor._episode_state(directory, True)["status"] == "done"
    review(directory, ["review_error"])
    assert monitor._episode_state(directory, True)["review"] == "error"
    review(directory, ["pass"])
    path = directory / "episode_review.json"
    os.utime(path, (time.time() + 2, time.time() + 2))
    assert monitor._episode_state(directory, True)["review"] == "reviewed"
    (directory / "nov_1.mp4").unlink()
    assert monitor._episode_state(directory, True)["status"] == "pending"
    (directory / "nov_1.mp4").write_bytes(b"final")
    (directory / "thin_media_report.json").unlink()
    assert monitor._episode_state(directory, True)["status"] == "pending"


def test_review_errors_and_old_reviews_do_not_change_current_quality_rate(workspace):
    current, _ = episode(workspace, 1, clips=2)
    review(current, ["pass", "review_error"])
    old, _ = episode(workspace, 2)
    review(old, ["fail"], fresh=False)
    stale, plan = episode(workspace, 3)
    review(stale, ["fail"])
    plan["clips"][0]["prompt"] = "changed"
    write(stale / "clip_plan.json", plan)
    (current / "render.log").write_text('{"video_model":"sd2.5"}\nold run\n{"video_model":"minimax-h3"}\n', encoding="utf-8")
    board = monitor._board_novel(NOVEL)
    assert board["quality"]["clips"] == 1
    assert board["quality"]["pass_rate"] == board["quality"]["recent_rate"] == 100
    assert board["quality"]["review_errors"] == 1
    assert board["review_pending"] == board["review_errors"] == 1
    lane = next(row for row in board["lanes"] if row["model"] == "minimax-h3")
    assert lane["pass_rate"] == 100 and lane["reviewed"] == lane["review_errors"] == 1


def test_lane_progress_and_worker_cache_use_current_plan(workspace, monkeypatch):
    good, _ = episode(workspace, 1)
    review(good, ["pass"])
    episode(workspace, 2, passed=False)
    root = str(workspace)
    listing = f"999999 python thin_batch.py --novel-dir {root} --chapters 1-2 --stage render"
    monkeypatch.setattr(monitor.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=listing))
    monkeypatch.setattr(monitor, "_proc_env", lambda pid: {"NOVEL_LOCAL_H3_URL": "pool"})
    row = monitor._lanes()[0]
    assert row["covered"] == 1 and row["total"] == 2
    work = good / "work" / "clips"
    for cid in ("clip_01", "clip_99"):
        path = work / cid / "attempt_01"
        path.mkdir(parents=True)
        (path / "clip.mp4").write_bytes(b"cached")
    listing = f"999999 12 python render_clips_thin.py --novel-dir {root} --episode nov_1"
    assert monitor._workers()[0]["detail"] == "缓存 1/1 段（未计质检）"


def test_live_and_board_javascript_render_the_status_payload(workspace):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    good, _ = episode(workspace, 1, clips=2)
    review(good, ["pass", "review_error"])
    episode(workspace, 2, passed=False)
    write(workspace / "delivery.json", {"deliverable": 1, "total": 2, "generated_at": "2026-09-12 23:00:00",
                                        "gates": {"tech": {"blocked": 1}, "review": {"blocked": 0, "must_fix_clips": 0},
                                                  "script": {"flagged": 0, "would_block": 0}}})
    live = {"now": time.strftime("%Y-%m-%d %H:%M:%S"), "novels": [monitor._novel_status(NOVEL)],
            "lanes": [], "workers": [], "inflight": [], "warnings": [], "processes": {}, "local": []}
    board = {"now": live["now"], "novels": [monitor._board_novel(NOVEL)]}
    board["novels"][0]["viewer_review"] = {
        "baseline_at": live["now"], "baseline_confirmed": 2, "baseline_one_vote": 0, "tracked": 2,
        "counts": {"clear": 1, "confirmed": 1}, "repair_checks": 2, "repair_counts": {"clear": 1, "confirmed": 1},
        "rows": [{"chapter": 1, "clip": "clip_01", "status": "clear", "kind": "", "observation": "正常"},
                 {"chapter": 2, "clip": "clip_02", "status": "confirmed", "kind": "画面出现文字", "observation": "出现<字幕>"}],
    }
    pages = [{"html": monitor.PAGE, "data": live, "body": "novels"},
             {"html": monitor.PAGE_BOARD, "data": board, "body": "board"}]
    script = r"""
const vm = require('vm'), fs = require('fs'), assert = require('assert').strict;
(async()=>{
  for(const p of JSON.parse(fs.readFileSync(0,'utf8'))){
    const elements = Object.fromEntries([...p.html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1], {style:{}, innerHTML:'', textContent:''}]));
    const timers = [];
    const ctx = vm.createContext({document:{getElementById:id=>elements[id], addEventListener:()=>{}},
      fetch:async()=>({json:async()=>p.data}), setTimeout:(fn,ms)=>timers.push(ms), setInterval:()=>{}});
    for(const m of p.html.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(m[1],ctx);
    await new Promise(resolve=>setImmediate(resolve));
    assert(!elements.stamp.textContent.includes('读取失败'), elements.stamp.textContent);
    const html = elements[p.body].innerHTML;
    assert(html.includes('质检合格') && html.includes('未过质检 1') && html.includes('审查失败 1'),html);
    assert(html.includes('可交付') && html.includes('</b> / 2 · 技术挡 1'),html);
    assert(html.includes('暂无总完成时间'),html);
    assert(!html.includes('全部跑完'),html);
    if(p.body==='board'){
      assert(html.includes('100.0%') && html.includes('未计入通过率'),html);
      assert(html.includes('需要重拍的片段') && html.includes('段无需重拍'),html);
      assert(html.indexOf('需要重拍的片段') < html.indexOf('设定一致性明细'),html);
      assert(html.includes('明显画面错误 · 复审与修复验收'),html);
      assert(html.includes('复审未发现明显错误 1 段') && html.includes('出现&lt;字幕&gt;'),html);
      assert(timers.includes(300000));
    }else{
      assert(elements.health.className.includes('bad'));
      assert(elements.attention.innerHTML.includes('待处理'));
      assert(!html.includes('（sd2.0）'));
    }
  }
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([node, "-e", script], input=json.dumps(pages, ensure_ascii=False), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
