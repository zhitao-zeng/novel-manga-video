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

import novel_manga.application.dashboard.config as dashboard_config
import novel_manga.application.dashboard.history as dashboard_history
import novel_manga.application.dashboard.inventory as dashboard_inventory
import novel_manga.application.dashboard.resources as dashboard_resources
from support.dashboard_pages import inline_page
import novel_manga.application.production.runs as thin_runs
from novel_manga.application.profiles import h3_prompt_fingerprint, plan_fingerprint
from novel_manga.application.production.runs import count_run

NOVEL = {"id": "nov", "title": "测试小说", "conductor": None}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_config, 'ROOT', tmp_path)
    monkeypatch.setattr(dashboard_config, '_lane_keys', lambda: {"nov": [{"base_url": "pool"}]})
    monkeypatch.setattr(dashboard_inventory, '_EPISODE_CACHE', {})
    import novel_manga.application.dashboard.store as dashboard_store_thin
    from novel_manga.dashboard.files import DashboardFiles
    monkeypatch.setattr(dashboard_store_thin, 'files', DashboardFiles())
    monkeypatch.setattr(dashboard_inventory, '_MODE_CACHE', {})
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
    write(path, {"policy": thin_runs.REVIEW_POLICY, "clips": {f"clip_{i:02d}": {"severity": severity} for i, severity in enumerate(severities, 1)}})
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

    live = dashboard_inventory._novel_status(NOVEL)
    board = dashboard_history._board_novel(NOVEL)
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
    assert dashboard_inventory._episode_state(directory, True)["status"] == "done"
    write(directory / "review_feedback.json", {"clip_01": "调整表情"})
    assert dashboard_inventory._episode_state(directory, True)["status"] == "stale"
    write(directory / "review_feedback.json", {})
    assert dashboard_inventory._episode_state(directory, True)["status"] == "done"
    review(directory, ["review_error"])
    assert dashboard_inventory._episode_state(directory, True)["review"] == "error"
    review(directory, ["pass"])
    path = directory / "episode_review.json"
    os.utime(path, (time.time() + 2, time.time() + 2))
    assert dashboard_inventory._episode_state(directory, True)["review"] == "reviewed"
    (directory / "nov_1.mp4").unlink()
    assert dashboard_inventory._episode_state(directory, True)["status"] == "pending"
    (directory / "nov_1.mp4").write_bytes(b"final")
    (directory / "thin_media_report.json").unlink()
    assert dashboard_inventory._episode_state(directory, True)["status"] == "pending"


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
    board = dashboard_history._board_novel(NOVEL)
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
    monkeypatch.setattr(dashboard_resources.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=listing))
    monkeypatch.setattr(dashboard_resources, '_proc_env', lambda pid: {"NOVEL_LOCAL_H3_URL": "pool"})
    row = dashboard_resources._lanes()[0]
    assert row["covered"] == 1 and row["total"] == 2
    work = good / "work" / "clips"
    for cid in ("clip_01", "clip_99"):
        path = work / cid / "attempt_01"
        path.mkdir(parents=True)
        (path / "clip.mp4").write_bytes(b"cached")
    listing = f"999999 12 python render_clips_thin.py --novel-dir {root} --episode nov_1"
    assert dashboard_resources._workers()[0]["detail"] == "缓存 1/1 段（未计质检）"


def test_processes_include_current_pipeline_without_counting_wrappers(monkeypatch):
    commands = [
        'python scripts/manage_repair_thin.py run --novel-dir outputs/zhutian-card',
        'python scripts/manage_repair_thin.py status --novel-dir outputs/zhutian-card',
        'python scripts/prepare_h3_book.py --novel-dir outputs/zhutian-card --workers 6',
        'python scripts/prepare_h3_book.py --novel-dir outputs/zhutian-card --episode 2001',
        'python scripts/prepare_recovery_thin.py --episode-dir outputs/zhutian-card/zhutian-card_2058 --kind entities',
        'python scripts/repair_review_thin.py --novel-dir outputs/zhutian-card --episodes 2058',
        'python scripts/verify_clips_thin.py --novel-dir outputs/xinghai',
        'python scripts/render_clips_thin.py --novel-dir outputs/zhutian-card --episode zhutian-card_2058',
        'python scripts/run_repair_step.py --result receipt.json -- python scripts/repair_review_thin.py --episodes 2058',
        "bash -c 'python scripts/prepare_h3_book.py --episode 2001'",
    ]
    monkeypatch.setattr(dashboard_resources, '_ps_output', lambda: '\n'.join(commands))
    result = dashboard_resources._processes()
    assert result['conductors'] == 2
    assert result['preparations'] == result['repairs'] == result['runners'] == 1
    assert result['reviews'] == 2 and result['planners'] == 0


def test_shared_locks_are_attributed_to_hyphenated_book_names(tmp_path, monkeypatch):
    pool = tmp_path/'pool';pool.mkdir()
    one=pool/'slot_00.lock';two=pool/'slot_01.lock';one.touch();two.touch()
    (tmp_path/'locks').write_text(f'1: FLOCK ADVISORY WRITE 100 00:11:{one.stat().st_ino} 0 EOF\n'
                                f'2: FLOCK ADVISORY WRITE 101 00:11:{two.stat().st_ino} 0 EOF\n')
    (tmp_path/'cmd100').write_bytes(b'python\0scripts/render_clips_thin.py\0--novel-dir\0/outputs/zhutian-card\0--episode\0zhutian-card_2058\0')
    (tmp_path/'cmd101').write_bytes(b'python\0scripts/render_clips_thin.py\0--episode\0other_book_4\0')
    original = Path
    mapped = {'/proc/locks':tmp_path/'locks','/proc/100/cmdline':tmp_path/'cmd100','/proc/101/cmdline':tmp_path/'cmd101'}
    monkeypatch.setattr(dashboard_resources, 'Path', lambda value: mapped.get(str(value), original(value)))
    assert dashboard_resources._held_by_novel(pool) == {'zhutian-card':1,'other_book':1}


def test_runtime_panel_updates_while_book_history_request_is_still_loading():
    script = r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert');
(async()=>{
 const html=fs.readFileSync(0,'utf8');
 const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{style:{},innerHTML:'',textContent:'',querySelectorAll:()=>[]}]));
 const runtime={now:'2026-09-16 11:30:00',processes:{conductors:2,preparations:6,reviews:3},
   inflight:[{novel:'诸天万象录',pool:'本地H3 · h3pool',slots:24,limit:24}]};
 const ctx=vm.createContext({document:{getElementById:id=>elements[id],addEventListener:()=>{}},
   fetch:url=>url==='runtime.json'?Promise.resolve({json:async()=>runtime}):new Promise(()=>{}),
   setInterval:()=>{},setTimeout:()=>{}});
 for(const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(m[1],ctx);
 await new Promise(resolve=>setImmediate(resolve));
 assert(elements.procs.innerHTML.includes('调度器')&&elements.procs.innerHTML.includes('开拍准备'));
 assert(elements.inflight.innerHTML.includes('24 / 24'));
 assert.strictEqual(elements['runtime-stamp'].textContent,runtime.now);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node','-e',script],input=inline_page(),text=True,check=True)


def test_live_and_board_javascript_render_the_status_payload(workspace):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    good, _ = episode(workspace, 1, clips=2)
    review(good, ["pass", "review_error"])
    episode(workspace, 2, passed=False)
    write(workspace / "delivery.json", {"review_policy": thin_runs.REVIEW_POLICY, "deliverable": 1, "total": 2, "generated_at": "2026-09-12 23:00:00",
                                        "gates": {"tech": {"blocked": 1}, "review": {"blocked": 0, "must_fix_clips": 0},
                                                  "script": {"flagged": 0, "would_block": 0}}})
    live = {"now": time.strftime("%Y-%m-%d %H:%M:%S"), "novels": [dashboard_inventory._novel_status(NOVEL)],
            "lanes": [], "workers": [], "inflight": [], "warnings": [], "processes": {}, "local": []}
    board = {"now": live["now"], "novels": [dashboard_history._board_novel(NOVEL)]}
    board["novels"][0]["viewer_review"] = {
        "baseline_at": live["now"], "baseline_confirmed": 2, "baseline_one_vote": 0, "tracked": 2,
        "counts": {"clear": 1, "confirmed": 1}, "repair_checks": 2, "repair_counts": {"clear": 1, "confirmed": 1},
        "rows": [{"chapter": 1, "clip": "clip_01", "status": "clear", "kind": "", "observation": "正常"},
                 {"chapter": 2, "clip": "clip_02", "status": "confirmed", "kind": "画面出现文字", "observation": "出现<字幕>"}],
    }
    pages = [{"html": inline_page(), "data": live, "body": "novels"},
             {"html": inline_page('board'), "data": board, "body": "board"}]
    import copy
    pipeline={"updated_at":live['now'],'age_seconds':10,'status':'running','total':2,'deliverable':1,'remaining':1,
              'inspection':{'episode_buckets':{'passed':1,'checked_with_errors':1},'clips':{'total':2,'passed':1,'failed':1,'unchecked':0}},
              'technical':{'done':1,'done_with_warnings':1},'ready_clips':1,'blocked_clips':0,'plan_blocked_clips':0,
              'held_episodes':[],'capacity':{'repair_episodes':24},'jobs':[],
              'repair':{'tracked':2,'passed':1,'generated':3,'retained':0,'avg_generations_passed':2,'total_cost_per_passed':3,'blocks':[],'updated_at':live['now']},
              'shared_audit':{'total':5,'counts':{'done':2,'pending':3}},'resources':{'available_instances':3,'available_slots':6,'night_instances':0,'instances':[]},
              'net_delivery':{'since':live['now'],'rates':{'15':None,'60':None}}}
    for original in list(pages):
        page=copy.deepcopy(original);page['data']['novels'][0]['pipeline']=pipeline;pages.append(page)
    cold=copy.deepcopy(pages[2])
    cold['data']['novels']=[{'id':'nov','title':'测试小说','history_loading':True,'attention':[], 'pipeline':pipeline}]
    pages.append(cold)
    for original in list(pages[:2]):
        page=copy.deepcopy(original)
        page['data']['novels'][0]['pipeline']={'mode':'audit','audit':{
            'scope':'仅 H3 片段','total':100,'episodes':20,'checked':4,'passed':3,'flagged':1,'flagged_episodes':1,
            'flagged_rate':25,'status':'running','workers':8,'alive':True,'counts':{'done':4,'pending':88,'running':8},
            'sampled_at':live['now'],'updated_at':live['now'],'excluded_models':{'sd2_5':90},'issues':{}}}
        pages.append(page)
    script = r"""
const vm = require('vm'), fs = require('fs'), assert = require('assert').strict;
(async()=>{
  for(const p of JSON.parse(fs.readFileSync(0,'utf8'))){
    const elements = Object.fromEntries([...p.html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1], {style:{}, innerHTML:'', textContent:'',querySelectorAll:()=>[]}]));
    const timers = [];
    const ctx = vm.createContext({document:{getElementById:id=>elements[id], addEventListener:()=>{}},
      fetch:async()=>({json:async()=>p.data}), setTimeout:(fn,ms)=>timers.push(ms), setInterval:()=>{}});
    for(const m of p.html.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(m[1],ctx);
    await new Promise(resolve=>setImmediate(resolve));
    assert(!elements.stamp.textContent.includes('读取失败'), elements.stamp.textContent);
    const html = elements[p.body].innerHTML;
    if(p.data.novels[0].pipeline&&p.data.novels[0].pipeline.mode==='audit'){
      for(const label of ['仅 H3 片段','本轮已审','审查通过','标记明显问题','8 路 Qwen']) assert(html.includes(label),label);
      assert(!html.includes('待交付')&&!html.includes('需重拍比例')&&!html.includes('NaN')&&!html.includes('undefined'),html);
    }else if(p.data.novels[0].pipeline){
      for(const label of ['统一产线','可交付','技术状态','当前片段审查','修复方式与实际开销','交付净增长与技术产出','Qwen / Flash 共同补查','运行阶段与实际算力','诊断参考']) assert(html.includes(label),label);
      assert(html.includes('观察中') && html.includes('至少累计 5 分钟采样后显示'));
      assert(!html.includes('NaN') && !html.includes('undefined'),html);
    }else{
      assert(html.includes('质检合格') && html.includes('未过质检 1') && html.includes('审查执行异常 1'),html);
      assert(html.includes('可交付') && html.includes('</b> / 2 · 技术挡 1'),html);
      assert(html.includes('暂无总完成时间'),html);
    }
    assert(!html.includes('全部跑完'),html);
    if(p.body==='board'&&!p.data.novels[0].pipeline){
      assert(html.includes('100.0%') && html.includes('未计入通过率'),html);
      assert(html.includes('需要重拍的片段') && html.includes('段无需重拍'),html);
      assert(html.indexOf('需要重拍的片段') < html.indexOf('设定一致性明细'),html);
      assert(html.includes('明显画面错误 · 复审与修复验收'),html);
      assert(html.includes('复审未发现明显错误 1 段') && html.includes('出现&lt;字幕&gt;'),html);
      assert(timers.includes(15000));
    }else if(p.body==='board'){
      assert(timers.includes(15000));
      assert(!html.includes('需要重拍的片段')&&!html.includes('设定一致性明细'));
    }else if(!p.data.novels[0].pipeline){
      assert(elements.health.className.includes('bad'));
      assert(elements.attention.innerHTML.includes('待处理'));
      assert(!html.includes('（sd2.0）'));
    }
  }
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([node, "-e", script], input=json.dumps(pages, ensure_ascii=False), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
