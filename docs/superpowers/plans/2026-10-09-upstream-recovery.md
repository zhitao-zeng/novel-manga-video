# 上游素材与有界恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Recommended execution: Native，由当前会话实施，最后一次独立审查；不为每项任务重复启动实现代理。

**Goal:** 让已确认的素材、旧长片段和耗尽任务通过现有pipeline修正、有限恢复、回到原任务验收。

**Architecture:** 复用源文核对、共享候选图缓存、RepairProposal、局部repack和现有授权/输入回执。Agent使用现有apply通道提出资产或打包操作，宿主验证并执行；额度来自同一份已有记录。书级候选准备、episode输入采用、实际视频提交分别记账，失败不通过重启获得新次数。

**Tech Stack:** Python 3.13、现有项目venv、pytest、既有图像/LLM/H3客户端、JSON回执与fcntl锁；无新依赖。

**Spec:** docs/superpowers/specs/2026-10-09-upstream-recovery-design.md（用户已于本轮批准）。

## Global Constraints

- 普通读书、建卡、剧本、分镜、翻译、生成沿用pipeline；Agent判断异常、提出源文支持的修正并调用已有工具。
- 使用现有RepairProposal、input-repair回执、repair_budget_grants和打包groups记录；不用新队列、数据库或新生产格式。
- 历史输入、视频、声线次数不清零，不通过新镜号绕过额度。
- 资源/素材/模型排队不占活动时间；实际模型与程序操作仍计时。
- 正常首拍的默认次数暂不全面上调。
- 已经用掉追加拍摄的任务不自动再追加。
- 局部审修600秒，源稿/整稿修正1200秒；最多两次提案。
- 已失败的同一恢复不自动再次入批。
- 正式成功仍须当前输入、选中take、语音/画面审核和交付门通过。
- 不修改另一会话的共享监控和身份统一草稿；不打印凭据，不push，不停止模型/GPU服务。

## Review Focus

1. 同书两运行树同时请求同一卡修正：共享候选只画一次；不同角色/状态不会合并缓存。Task 1必须有跨运行树并发测试。
2. 原文未明确年龄/衣料、泛称身份无法确认：不编造事实，不画另一角色替代。Task 1必须有不确定与美术推断测试。
3. 已拍父片段拆成两子段，同时发起首次生成：共用剩余授权，原记录和真实次数保持；已提交未完成不能被第二子段绕过。Task 2必须有并发提交、未知提交和恢复旧task测试。
4. 恢复已发、进程在源审/提交/资源等待中退出：重启不重发时间、输入或视频；真正等待不耗活动时间。Task 3必须有重启、等待和两次提案上限测试。
5. 输入/候选图已过，但画面仍坏或追加已用尽：不可标通过，不再自动追加；同一错误不能反复入池。Task 4与Task 5必须有闭环失败和正式验收测试。

## 工作树与基线

目录：/mnt/disk1/zengzhitao/novel/worktrees/audit-host-recovery-20261009。
分支：codex/audit-host-recovery-20261009；HEAD 51f36b2。
已有未提交的本会话修正已通过源码3038项、旧宿主281项，并上线独立运行快照。该WIP不是本计划新增实现。执行前将其diff另存本计划工作记录，防止把旧修正当本轮成果；不得stage其他会话草稿。

测试命令统一使用现有项目venv。每次新shell先执行下列定义，后文PY命令可直接运行。ROOT指主项目，W指本工作树；临时运行及凭据只在主项目.runtime下。以下相对路径均从W解释。

~~~bash
PY() { /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python "$@"; }
~~~

本计划的提交、合入main和部署只包含当前会话拥有的上述宿主修正及本计划文件。执行批准同时覆盖这些步骤；提交前单独检查暂存范围，主目录docs/active-sessions.md的其他会话行不代为提交。

## 公共操作与证据契约

保留八个公共RPC；通过apply决策增加两个单独提交的操作：repair_assets、repack。不得与其他clip补丁混在一个批次。

~~~json
{"decisions":[{"clip_id":"clip_03","action":"repair_assets","reason":"原文明确男角色，当前卡面与其不符","source_quote":"三个男人走入大厅","asset_id":"character_021","fields":{"gender":"男"}}]}
~~~

~~~json
{"decisions":[{"clip_id":"clip_03","action":"repack","reason":"未拍源镜范围超过H3上限","source_quote":"三人走入大厅","clip_ids":["clip_03"]}]}
~~~

字段由源核对与实际注册卡校验；Agent提供的“verified/consistent”不能作为授权。模型诊断返回binding/design/image/false_positive/uncertain及证据，宿主保留真实初核结果。binding仅产生源级绑定修正；design/image才进入建卡。诊断建议不是已采用输入。

资产结果保存在既有输入回执和共享draft-assets候选记录，返回资产ID/路径、源核对、图审、受影响范围和后续input决策；不直接改已有视频或标正式通过。

### Task 1: 共用资产诊断、候选与定向采用

**Files:**
- Create: src/novel_manga/application/agents/episode/upstream_assets.py
- Modify: src/novel_manga/application/agents/episode/draft_assets.py（_asset的共享复用键与锁）
- Modify: src/novel_manga/application/agents/episode/input_assets.py（已审核候选的phase/catalog接线）
- Modify: src/novel_manga/application/planning/lean_bible.py（抽出既有定向事实纠正入口）
- Test: tests/test_upstream_asset_recovery.py
- Existing regressions: tests/test_episode_draft_assets.py、test_phase_card_pipeline.py、test_asset_card_scope.py、test_publish_cards.py

**Interfaces:**
- Consumes: input_repair.source_check、draft_assets._asset/check_image、input_assets.local_phases、现有角色/地点卡路由。
- Produces: upstream_assets.validate(ctx, row: dict) -> dict；diagnose(ctx, row: dict) -> dict；prepare(ctx, row: dict) -> dict；apply_to_snapshot(ctx, result: dict) -> dict。
- prepare结果包含ok、kind、source、image_review、asset、affected_clips、input_decision、evidence_path。ok表示候选可供采用，不表示成片通过。
- draft_assets._asset(ctx, role, name, design, *, base_asset=None)保持旧调用有效；带base_asset时缓存键增加该已注册ID，现有旧键行为不变。

- [ ] **Step 1: 写具体失败测试。** 测试中只替换外部source/image模型及图像生成，保留真实索引、锁、记录和素材采用逻辑。用已有support.episode_existing.prepared构建两个ctx，设置相同source_novel_dir和不同run_dir；实际候选库必须位于同一书目录。

~~~python
def test_two_run_trees_share_one_source_checked_card(ctx_pair, approved_source, fake_image):
    from concurrent.futures import ThreadPoolExecutor
    from novel_manga.application.agents.episode import upstream_assets
    row = {"clip_id": "clip_01", "action": "repair_assets", "asset_id": "character_001",
           "reason": "卡面与原文冲突", "source_quote": "甲坐在宿舍", "fields": {"gender": "男"}}
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda ctx: upstream_assets.prepare(ctx, row), ctx_pair))
    assert results[0]["asset"]["asset_id"] == results[1]["asset"]["asset_id"]
    assert fake_image.calls == 1
    assert all(result["source"]["consistent"] for result in results)
    assert all(result["image_review"]["consistent"] for result in results)
~~~

同时实现并运行test_binding_fix_does_not_draw、test_uncertain_identity_draws_nothing、test_unspecified_age_is_not_source_contradiction、test_rejected_card_keeps_original_and_voice、test_different_base_assets_do_not_share_candidate、test_snapshot_adoption_keeps_other_actor_fields。ctx_pair/approved_source/fake_image为本测试文件内完整fixtures：source写入run_dir/source.txt，卡图用测试JPEG，外部模型按来源名返回，fake_image只记录并写候选图；不替换prepare和索引/发布函数。

- [ ] **Step 2: 运行RED。** Run: PY -m pytest -q tests/test_upstream_asset_recovery.py。Expected: import/新入口缺失失败；旧卡/声线不变的断言必须对真实磁盘快照执行。
- [ ] **Step 3: 实现验证与诊断。** validate从当前plan.references/catalog找asset_id，不接受未注册图或无效clip范围；source_quote逐字存在于run_dir/source.txt。使用实际卡图、原文、读书事实和当前字段调用独立判官；kind=uncertain时保留问题，kind=false_positive不画图，kind=binding给出已有源级修正目标。fields只允许现有LOOK_FIELDS/地点说明，保留年龄阶段与永久外貌的区别，不调用身份merge。

~~~python
def prepare(ctx, row):
    checked = validate(ctx, row)
    diagnosis = diagnose(ctx, checked)
    if diagnosis["kind"] in {"uncertain", "false_positive", "binding"}:
        return {**diagnosis, "ok": diagnosis["kind"] != "uncertain", "asset": None}
    from copy import copy
    from .draft_assets import _asset
    shared = copy(ctx)
    shared.asset_snapshot_dir = ctx.source_novel_dir
    asset = _asset(shared, diagnosis["role"], diagnosis["name"], diagnosis["design"],
                   base_asset=checked["asset_id"])
    return {**diagnosis, "ok": True, "asset": asset, "image_review": asset["image_review"]}
~~~

diagnose必须定义上述kind/role/name/design/source/affected_clips/input_decision/evidence_path字段，在已有source回执中保存源核对；_asset不得只凭Agent的断言直接建图。扩展现有锁/索引键，不另设图片数据库。图审失败沿用rejected/built/accepted记录；技术失败保留已画图，同候选继续审核，不用另一设计逃避失败。

- [ ] **Step 4: 定向采用。** apply_to_snapshot只使用prepare已记录的审核结果。保留人物名字、编号、声线、其他字段；当前章外貌按phase采用，明确永久事实才调用抽出的定向事实更新。source_novel_dir的共享候选可复用，但episode的phase/catalog只改变受影响范围。既有角色用phase/明确reuse_asset_id，新增原文角色使用现有characters路径；地点使用locations。实际请求改动仍由普通input编译和preflight完成。
- [ ] **Step 5: GREEN与相关回归。** Run: PY -m pytest -q tests/test_upstream_asset_recovery.py tests/test_episode_draft_assets.py tests/test_phase_card_pipeline.py tests/test_asset_card_scope.py tests/test_publish_cards.py。Expected: 全部通过，生成调用次数、原资产与声线断言成立。只提交本任务拥有的文件及必要的本会话前置修正。

### Task 2: 局部重打包与来源范围额度

**Files:**
- Create: src/novel_manga/application/agents/episode/scoped_repack.py
- Modify: src/novel_manga/application/packing/blocked.py（现有groups与source_addresses记录）
- Modify: src/novel_manga/application/repair/history.py（来源范围、授权与提交预留）
- Modify: src/novel_manga/application/rendering/submission.py（新提交原子占用/旧task恢复）
- Modify: src/novel_manga/application/production/clip_preparation.py（同一额度读法）
- Modify: src/novel_manga/application/agents/episode/inputs.py（初始化不再因部分已有take阻止合法局部打包）
- Test: tests/test_scoped_repack_budget.py
- Existing regressions: tests/test_blocked_recovery.py、test_repair_history.py、test_generation_recovery.py、test_episode_agent_inputs.py、test_conditional_repair_budget.py

**Interfaces:**
- Consumes: packing.blocked.repack(directory, plan, script, targets, context) -> (plan, report)；RepairProposal；history.publish_candidate路径。
- Produces: scoped_repack.validate(ctx, row: dict) -> dict；prepare(ctx, row: dict) -> RepairProposal；apply(ctx, row: dict) -> dict。
- Produces in history: generation_scope(directory, cid, *, record=None) -> set[str]；source_budget(directory, cid, *, preparation=False, grants=None) -> dict；remaining_takes(directory, cid, *, preparation=False) -> int；reserve_scope_submission(directory, output, request) -> dict；finish_scope_submission(directory, output, *, outcome: str) -> None。
- 真实generation_counts保持逐clip事实不变，generation_limit/preparation_limit的原有逐clip含义保持。remaining_takes返回逐clip余额与原来源限制允许余额的较小值，生成/预检/status/apply采用它；source_budget返回roots、constraints（每个root的limit/used/pending/remaining）与最小remaining。不能用新子段的默认次数决定原来源上限。

- [ ] **Step 1: 写RED测试。** 建立父片段已用1次、正常上限3次、拆成两子段的真实history/grants/plan记录。复用现有begin_trial和repack的groups，不将父次数伪造到每个子段。

~~~python
def test_two_children_share_parent_balance(repacked_episode):
    from novel_manga.application.repair import history
    directory = repacked_episode
    assert history.generation_counts(directory)["clip_01"] == 1
    assert history.generation_scope(directory, "clip_06") == {"clip_01", "clip_06"}
    budget = history.source_budget(directory, "clip_06")
    assert budget["constraints"]["clip_01"]["used"] == 1
    assert budget["constraints"]["clip_01"]["limit"] == 3
    assert history.remaining_takes(directory, "clip_06") == 2
~~~

增加test_unaffected_clip_and_request_bytes_are_identical、test_parent_28s_video_15s_is_not_auto_reused、test_7s_existing_clip_survives_other_ranges_repack、test_dialogue_stream_preserves_owners_words_modes、test_concurrent_children_cannot_overbook_parent、test_pending_unknown_submission_stays_reserved、test_same_task_resume_spends_no_second_slot、test_mixed_parent_limits_keep_each_authorization、test_repack_does_not_allow_second_extra。fixtures使用现有source-connected范围与真实request/task回执，外部H3提交以可控假客户端替换。

- [ ] **Step 2: 运行RED。** Run: PY -m pytest -q tests/test_scoped_repack_budget.py。Expected: 新函数缺失或当前新ID获得默认次数导致失败。
- [ ] **Step 3: 实现局部候选。** validate校验源文引用、当前clip范围、超长或已证实覆盖失败原因；无需整体无视频。prepare用现有repack，保留其old/new/source_addresses，保证turn_stream前后一致；notes只去掉实际替换范围。RepairProposal.result.changed列出旧范围和新分段；structural_repair使用原report。apply通过publish_candidate写入，原目录与take文件不清理。没有新plan或者验证失败时原plan不写回。

~~~python
def prepare(ctx, row):
    import copy
    from novel_manga.util import read_json
    from novel_manga.application.packing.blocked import repack
    from novel_manga.repair.proposal import RepairProposal
    checked = validate(ctx, row)
    script = read_json(ctx.episode_dir / "chapter_script.json")
    plan = read_json(ctx.episode_dir / "clip_plan.json")
    after, report = repack(ctx.episode_dir, plan, script, targets=set(checked["clip_ids"]),
                           context=checked["packing_context"])
    affected = set(report["changed"]) | {cid for group in report["groups"] for cid in group["new"]}
    notes = {cid: value for cid, value in read_json(ctx.episode_dir / "review_feedback.json", {}).items()
             if cid not in affected}
    return RepairProposal(result={"changed": sorted(affected)}, script=copy.deepcopy(script),
        plan=after, notes=notes, changes={"groups": report["groups"]}, structural_repair=report)
~~~

- [ ] **Step 4: 额度归属与实际提交。** repack会沿用部分旧clip_id：单父拆两段实际为clip_01/clip_06，而不是删除clip_01改为两个全新ID。采用前在已有groups记录中保存每个原来源的limit/used、每个子段的源父集合和该子ID当时raw计数。范围使用量为原来源used，加采用后的子段真实计数增量；不能把旧同ID的历史消耗再算作新子段消耗。begin_trial沿用原take归档流程，旧媒体仍可追溯。

source_budget从已采用trial.changes.groups计算原来源约束。跨父子段同时满足各来源限制，真实generation_counts只记录一次；约束不是将同一take伪造成多个实际take。根据实际源镜与拆分关联确定父关系，不能取整个group的max限制。无法建立明确归属时不采用候选并说明缺少哪段来源；同一来源范围本次只允许一次重打包，不能再拆一次领取新默认额度。

~~~python
# 此函数在history内定义；source_budget的计算使用上文保存的真实baseline与增量。
def remaining_takes(directory, cid, *, preparation=False):
    counts = generation_counts(directory)
    limit = (preparation_limit(directory, cid) if preparation else generation_limit(directory, cid))
    own = max(0, limit - counts.get(cid, 0))
    budget = source_budget(directory, cid, preparation=preparation)
    return min(own, budget["remaining"]) if budget["roots"] else own
~~~

将所有“counts与limit比较”的生成、输入依赖预算、status和preflight调用接到remaining_takes。分别搜索application/agents/episode、application/production/clip_preparation.py、application/rendering及media/generation，保留缓存/同task续取在配额检查前的复用行为。grant_conditional_extra按原来源累计次数记录唯一授权，所有子段映射同一授权。已有未消费授权复用，已用过一次追加不重发；conditional_extra对当前子段提供明确scope与等价逐clip余额视图，底层原来源授权/真实次数不改写。

新实际提交前在既有repair_budget_grants.lock下复核范围余额，将预留写入该attempt/request.json的回执元数据；本地H3与既有供应商的新提交均从rendering.submission进入。请求比较排除此回执元数据，不改变实际H3参数。已明确未受理可释放；已受理/提交不确定保留，按已有task恢复；完成后真实take替代预留，不能预留+完成双算。缓存命中与同task续取不预留。先执行现有before_video_submit探针，不为额度检查额外生成探针视频。

- [ ] **Step 5: GREEN与相关回归。** Run: PY -m pytest -q tests/test_scoped_repack_budget.py tests/test_blocked_recovery.py tests/test_repair_history.py tests/test_generation_recovery.py tests/test_episode_agent_inputs.py tests/test_conditional_repair_budget.py。Expected: 全绿；并发两个子段只有有余额者获新提交，旧task可完成且计数不增加。提交本任务文件。

### Task 3: 输入恢复、活动时间与视频授权分别记账

**Files:**
- Create: src/novel_manga/application/agents/episode/bounded_recovery.py
- Modify: src/novel_manga/application/agents/episode/input_records.py
- Modify: src/novel_manga/application/agents/episode/input_repair.py
- Modify: src/novel_manga/application/agents/episode/lifecycle.py、turns.py、recovery.py
- Modify: src/novel_manga/application/repair/history.py
- Test: tests/test_bounded_upstream_recovery.py
- Existing regressions: tests/test_episode_agent_input_repair.py、test_episode_agent_scheduling.py、test_resource_wait_budget.py、test_conditional_repair_budget.py

**Interfaces:**
- Consumes: Task 1 persisted source/image result与Task 2已采用groups；已有host确认的错误修复回执；resource_waits.wait_seconds。
- Produces: bounded_recovery.evidence(ctx, cid: str) -> dict；grant(ctx, cid: str, evidence: dict, *, kind: str) -> dict；input_operation(ctx, cid: str, row: dict, *, consume=False) -> dict；resume_window(ctx, saved: dict) -> dict | None；record_proposal(ctx) -> None。
- kind枚举input/time，视频继续使用history.grant_conditional_extra；不得因grant(input/time)改limit或activated。
- 授权及消费存现有repair_budget_grants[cid].upstream_recovery；episode时间窗口放现有agent-run.json.upstream_recovery。同一问题候选的证据路径/具体字段对比复用，不增加哈希。

- [ ] **Step 1: 写RED测试。** 使用实际receipt/grants文件构造旧revision_count=1、resume_count=1、旧时间0的episode；独立候选图已通过但旧视频额度也为0。验证恢复输入不激活视频，失败和重启不会再给一次。

~~~python
def test_input_recovery_does_not_grant_video(ctx, approved_asset_evidence):
    from novel_manga.application.agents.episode import bounded_recovery
    from novel_manga.application.repair import history
    old_limit = history.generation_limit(ctx.episode_dir, "clip_01")
    first = bounded_recovery.grant(ctx, "clip_01", approved_asset_evidence, kind="input")
    again = bounded_recovery.grant(ctx, "clip_01", approved_asset_evidence, kind="input")
    assert first == again
    assert history.generation_limit(ctx.episode_dir, "clip_01") == old_limit
    assert not history.conditional_extra(ctx.episode_dir, "clip_01")
~~~

增加test_self_reported_verified_cannot_grant、test_stale_candidate_cannot_consume_newer_evidence、test_spent_recovery_remains_spent_after_restart、test_local_window_is_600_source_window_is_1200、test_resource_wait_does_not_use_window、test_two_proposals_stop_without_losing_host_receipt、test_existing_unused_extra_is_reused、test_used_extra_is_not_reissued、test_old_input_revision_resume_and_voice_counts_are_preserved。fixtures保留原receipt文件字节快照，外部模型模拟，实际quota函数不可mock。

- [ ] **Step 2: 运行RED。** Run: PY -m pytest -q tests/test_bounded_upstream_recovery.py。Expected: 新恢复入口缺失及现有time=0拒绝失败。
- [ ] **Step 3: 证据与输入恢复。** evidence读取宿主的原文核对/候选图审核/真实编译变化/已修程序故障回执；校验目标当前片段与源引用，旧评审文本不是新事实。grant使用既有锁，保存具体依据与候选，重复调用返回旧grant；在真正源审/资产/编译执行前consume，语法check不consume。输入route支持一次upstream_recovery分支，新旧候选/修订/续跑的合法性与status共用；保留全部旧计数与previous_decisions。

~~~python
# grant内部在已有repair_budget_grants.lock下写回。
entry = grants.setdefault(cid, {}).setdefault("upstream_recovery", {})
if entry.get(kind):
    return entry[kind]
entry[kind] = {"evidence": verified_evidence, "consumed": False,
               "candidate": candidate, "reason": verified_evidence["reason"]}
atomic_write_json(ctx.episode_dir / "repair_budget_grants.json", grants)
return entry[kind]
~~~

上述verified_evidence由evidence返回，candidate由证据中的明确修正决定产生；不得将Agent自行写的consistent当它们。源核对继续严格，恢复并不豁免source/cast/编译错误。

- [ ] **Step 4: 一次时间窗口。** resume_window只在原预算耗尽且有合格依据/原任务无在跑job时发一次；先持久化旧remaining/deadline/agent_seconds与窗口，再唤醒原会话。局部600秒、source整稿1200秒为该窗口总活动时间，最多两次提案，重启只继续剩余。等待记现有resource_waits，旧agent_seconds保持累计。两次提案后已受理宿主操作仍要完成并保存结果，不能中断已提交H3或finish。

~~~python
window = {"kind": kind, "seconds": 1200 if kind == "source" else 600,
          "proposals": 0, "used": False, "evidence": verified_evidence,
          "prior_remaining_seconds": remaining_at_stop(saved)}
resumed = {**saved, "upstream_recovery": window, "status": "deferred", "phase": "ready_agent",
           "remaining_seconds": window["seconds"], "deadline": time.time() + window["seconds"],
           "no_progress": 0, "fallback_invocations": 0}
atomic_write_json(ctx.run_dir / "agent-run.json", resumed)
~~~

record_proposal在新Agent提案交接前增加proposals，resume旧job不增加；seen-window不再次进入创建代码。复用lifecycle.account_resources和原deadline续接，避免旧retry_issues通道重置本窗口。已发窗口失败后同问题不再发第二个。

- [ ] **Step 5: GREEN与相关回归。** Run: PY -m pytest -q tests/test_bounded_upstream_recovery.py tests/test_episode_agent_input_repair.py tests/test_episode_agent_scheduling.py tests/test_resource_wait_budget.py tests/test_conditional_repair_budget.py。Expected: 全绿；活动时间/次数历史保留，视频授权独立。提交本任务文件。

### Task 4: Agent操作、自动交接和具体卡点

**Files:**
- Modify: src/novel_manga/application/agents/episode/draft_operations.py、repair_operations.py、inspection.py、current_task.py、startup.py、flow.py
- Modify: src/novel_manga/application/agents/episode/pipeline_preparation.py、recovery.py
- Modify: skills/episode-production/references/repair-playbook.md、docs/production-lessons.md
- Test: tests/test_upstream_recovery_handoff.py
- Existing regressions: tests/test_episode_current_task.py、test_episode_agent_scheduling.py、test_episode_agent_bridge.py、test_episode_agent_assembly.py、test_episode_agent_risks.py

**Interfaces:**
- Consumes: Task 1 upstream_assets.validate/prepare/apply_to_snapshot；Task 2 scoped_repack.validate/apply；Task 3 bounded_recovery.evidence/grant/input_operation/resume_window/record_proposal。
- Produces: recovery.upstream_route(ctx, status: dict) -> dict；status的upstream_actions与同一合法性判断；current-task的具体操作/证据/待处理范围。

- [ ] **Step 1: 写RED端到端测试。** 使用真实EpisodeBridge/EpisodeTools，模型和媒体客户端模拟；返回已采用候选后运行真实输入校验/编译与正式finish。测试new action提交副本与回执同job，旧成功镜头不需重复keep。

~~~python
def test_assets_recovery_hands_back_to_pipeline_then_formal_review(running_episode):
    bridge, ctx = running_episode.bridge, running_episode.ctx
    accepted = bridge.handle("apply", {"file": "output/repair-assets.json"})
    assert accepted["job_id"]
    final = running_episode.finish_pipeline()
    assert final["passed"] is True
    assert running_episode.original_good_take_bytes == running_episode.current_good_take_bytes
    assert running_episode.actual_new_takes == 1
~~~

running_episode为本文件fixture，使用真实桥与当前状态方法，finish_pipeline按当前回执依次apply输入、render、review、finish，不替换质量判定。增加test_candidate_only_is_not_episode_accepted、test_no_video_permission_still_allows_asset_diagnosis、test_same_failed_recovery_is_not_queued_again、test_uncertain_source_is_reported_without_redraw、test_source_generation_stays_native_not_agent_authoring、test_finished_host_job_is_saved_after_proposal_limit。

- [ ] **Step 2: 运行RED。** Run: PY -m pytest -q tests/test_upstream_recovery_handoff.py。Expected: 新action不支持或交接缺失失败。
- [ ] **Step 3: 接check/apply/status。** repair_assets/repack是单独的apply决策，check调用相同validate且不画图/写额度。普通input继续旧规则。status必须读取Task 3的合法入口及Task 2范围余额；不再依据裸remaining判断可恢复。

~~~python
if row["action"] == "repair_assets":
    from . import upstream_assets
    candidate = upstream_assets.prepare(self.ctx, row)
    return upstream_assets.apply_to_snapshot(self.ctx, candidate) if candidate.get("asset") else candidate
if row["action"] == "repack":
    from . import scoped_repack
    return scoped_repack.apply(self.ctx, row)
~~~

check对应调用upstream_assets.validate/scoped_repack.validate；action与quota变化在同job记录submitted_decisions及结果。资产/事实初核不能被旧episode时间挡住：由既有书级候选准备入口对保存的具体源支持提案执行一次，成功后产生Task 3的合格证据才唤醒原episode；无提案或无证据时明确needs_source_evidence，不凭空给时间。

- [ ] **Step 4: 自动路由。** upstream_route根据当前宿主具体证据判定asset/source_binding/repack/recovery/needs_evidence，普通前置工作走pipeline_preparation，语义判断交Agent。采用图、源输入或重打包后回prepare→H3翻译/输入预检→生成→独立审核→finish。每次维护当前targets，失败分类附blocked clip、失败字段、原文/图片/回执入口和下一项合法动作；旧全局pool文本不能压过新状态。经验证的修法写已有playbook/memory，未通过不得写成成功案例。
- [ ] **Step 5: GREEN与相关回归。** Run: PY -m pytest -q tests/test_upstream_recovery_handoff.py tests/test_episode_current_task.py tests/test_episode_agent_scheduling.py tests/test_episode_agent_bridge.py tests/test_episode_agent_assembly.py tests/test_episode_agent_risks.py。Expected: 全绿，假客户端新take次数/已拍字节/正式质量门断言成立。提交本任务文件。

### Task 5: 完整验证、独立审查、运行版本接线与有限恢复

**Files:**
- Create: scripts/upstream_recovery_batch.py（现有运行索引的有限恢复入口，不新建调度系统）
- Test: tests/test_upstream_recovery_batch.py
- Modify: docs/audit-host-recovery-20261009.md、docs/active-sessions.md（只自己的登记行）
- Runtime owner: ROOT/.runtime/guangming-upstream-recovery-20261009/owner.md及原run_dir；保留旧审计/旧池/当前运行快照。

**Interfaces:**
- Consumes: 现有workspace-index与当前正式验收状态；Task 4 upstream_route；Task 3恢复消费标记；Task 2source范围计数。
- Produces: 一次有限恢复清单及每集具体前后回执，不自动二次入池；共享监控只读，自动补入继续关闭。

- [ ] **Step 1: RED测试与批入口。** 写test_accepted_episode_is_skipped、test_live_owner_is_not_restarted、test_same_failed_recovery_not_requeued、test_no_eligibility_records_reason_without_grant、test_partial_batch_success_keeps_other_failures，使用真实state文件与可控子进程；不调用LLM/H3。Run: PY -m pytest -q tests/test_upstream_recovery_batch.py。Expected: 脚本模块/入口缺失失败。

~~~python
def eligible_rows(rows, inspect):
    selected, held = [], []
    for row in rows:
        current = inspect(row)
        if current["accepted"] or current["live_owner"]:
            continue
        if current["recovery_failed"] or not current["upstream_action"]:
            held.append({"episode": row["episode"], "reason": current["reason"]})
        else:
            selected.append({**row, "upstream_action": current["upstream_action"]})
    return selected, held
~~~

inspect从当前manifest+quality+输入/take一致性及原锁读取，不使用历史pool登记当验收；采用现有有限controller/CLI启动和已有worker结果保存逻辑。计划索引路径由明确参数传入，不能硬编码章节名或人物名。

- [ ] **Step 2: GREEN、全量与独立审查。** Run: PY -m pytest -q tests/test_upstream_recovery_batch.py，然后PY -m pytest -q --junitxml=/mnt/disk1/zengzhitao/novel-manga-video/.runtime/guangming-upstream-recovery-20261009/source-suite.xml（先创建带owner的该运行目录）。Expected: 全绿。执行技能要求的一次全分支独立审查，审查重点为上方五项；重要问题用失败测试→修正→全量通过一次处理，不反复重新送审。
- [ ] **Step 3: 真实宿主版本。** 现有在产快照为旧monolithic tools/flow，源码为拆分模块，不能直接以源码测试宣称已部署。制作明确版本的新独立运行快照，保留旧快照；优先从已验证源码制作完整代码包，避免把新模块生搬到旧目录。冻结原run_dir的数据与私有路由，复用现有环境。运行实际快照的同等quota/source/bridge回归，加载检查只打印模块路径/版本，不输出配置或密钥。仅在原任务已释放锁时切到新快照；运行中的H3 task继续已有task_id，不重交。
- [ ] **Step 4: 受控生产验证。** 先离线生成剩余任务的当前入口与具体依据，再从有充分原文/图证据的资产任务及456/614两类旧长计划中由程序选择第一批。使用原run_dir、原生成记录、当前DS和H3池，单次有限恢复；没有资格记录held。无需重拍的已有媒体先复核/收尾，确需生成且有合法次数者才生成。等待正式finish并比对真实前后计数、未变片段/素材和主目录发布条件；图候选/输入通过不可报告成片通过。
- [ ] **Step 5: 合入与交接。** 检查并仅提交本会话拥有的源码/测试/设计/计划；main其他草稿不stage，运行产物不入Git。合入main后跑相关契约测试；新运行继续使用已验证快照，不能只合代码不切实际宿主。同步自己的在途行、具体未完成项与新全局计数；报告commit/merge/no-push、真实图片/视频结果和仍未验证的部分。真实仍需第二次追加的已耗尽片段保留修好输入与回执，不自动增加额度。

## 计划自审与执行边界

Task 1资产结果产生Task 3所需新依据，Task 2提供Task 3/4的来源额度，Task 4统一接线，Task 5验证到实际宿主与生产。初核/资产准备在已有书级路径，原episode时间耗尽不会阻止获得新证据；无证据不唤醒episode，避免恢复资格与修复前置形成死锁。

多父来源的次数不能把限制取max或把原次数复制到每个子段；精确归属无法建立即不采用候选。提交预留覆盖实际并发的兄弟片段，不引入通用租约框架。旧task续取、未知提交与真实新提交区别留在已有request/task回执。

时间窗口的600/1200秒是总活动时间，上限两提案，不是每轮重新给600/1200。模型/图片排队不计入，实际生成仍计入。候选图生成和输入恢复不自动授予视频，第二次条件补拍继续关闭。

执行方法建议Native：当前会话实施全部五项，最后一次独立代码审查。计划尚待用户书面核定；当前旧恢复批继续运行，没有实施本计划的新公共操作或恢复授权。
