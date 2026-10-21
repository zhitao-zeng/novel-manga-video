# Pipeline Mainline Closeout Implementation Plan

**Goal:** 将当前已验证的主工作区 WIP、傲世共性修复、官方 DS41 选项及 H3 打包源码收敛成可干净检出的 main，验证后推送，并用一个复杂章跑正常交付。

**Architecture:** 保持原生规划、准备、生成、Agent 异常审修及既有交付入口。先冻结主目录和指定工作树差分；在本分支按依赖顺序接入，以小型公共修复和一份相互依赖的前端改动分别提交。原工作树、小说文件、媒体、原生成计数及现有部署保持原有归属。

**Tech Stack:** Python 3.11+、现有项目虚拟环境、pytest、FFmpeg、Git worktree、既有 H3/Agent/ModelScope 接口。

**Spec:** 本会话用户已批准的四步收口方案与 `docs/pipeline-mainline-closeout-20261021.md`。

## Global Constraints

- 基线 `310676f`，本地 origin/main 比 main 落后 159 提交；只向现有 child repository 的 origin 推送。
- 生产原计数、缓存、历史、额度和来源链均保留。真实未知和失败不改写成成功。
- 当前多会话 WIP 按冻结快照采用；后续并行修改仍留在原工作区，不代为提交。
- 不把旧分支的临时补额度、现场放行和冻结实验整包并入默认流程。
- 只保留已安装项目环境；不新增模型/媒体下载，不提交生成产物或凭据。
- 用户已授权本轮整合、提交、合 main、推送及一次复杂章真实全流程；保留当前生产路由。

## Review Focus

1. 空流、丢尾流、未知用量与恢复会话累计 token 不得误当成功或重复收费。
2. 新书重启不得重新读取已采用原文、重建设计、重复写初稿或恢复预算。
3. 首拍准备与 Agent 接续必须共享当前素材、源文失败及原活动时间。
4. 主目录的并行新改动不能被冻结快照覆盖，旧工作树不能被当成新的生产分支整包合入。
5. 缓存交付与真实生成分别记录；模型/额度故障不得伪装内容通过或已上传。

## Task 1: Freeze ownership and establish the integration record

- [x] 冻结 main 的 132 项未提交条目及二进制 diff，保存原状态。
- [x] 冻结傲世 12 个自有源码及相关测试、官方 DS41 三文件，按工作板和 source-change-index 记归属。
- [x] 分类旧工作树为已合入/重复快照/冻结实验/本轮采用，保留原目录。

Expected: 所有采用文件有来源；隔离树仍以 `310676f` 为基线，不写生产媒体。

## Task 2: Integrate common LLM transport and metering

Files: `src/novel_manga/llm/{client,config,transport,usage,native_usage}.py`、`scripts/llm_usage_report.py`、`scripts/standby/codex_luna/` 的已改文件、Agent session 计量接线、相关端点配置和测试。

- [x] 先以现有新增 `tests/test_incomplete_model_retry.py` 在基线源码上复现真实空流/不完整响应失败。
- [x] 接入冻结的已实现代码、计量测试与配置；不重新编写一套重试流程。
- [x] 运行 `python -m pytest tests/test_incomplete_model_retry.py tests/test_llm_usage.py tests/test_llm_cost_report.py tests/test_model_client.py tests/test_codex_luna_standby.py tests/test_endpoint_presets.py tests/test_episode_agent_session.py`。
- [x] 差分核对后提交 `fix: preserve incomplete-response retries and accurate model usage`。

Expected: 相关测试全部通过，未知用量和原时间/输出分配仍可观测。

## Task 3: Integrate the coupled new-book/identity/story front end

Files: Task 2 后 main 冻结清单剩余源码、测试、技能、资料及配置；`docs/active-sessions.md` 在最终收工时合并最新记录。

- [x] 依现有快照接回初读归并、人物身份、规划异常接续、姿态/在场、对白保留、镜头语言及关联黄金夹具。
- [x] 检查已删除的两个旧测试是否被实际保留内容的回归取代。
- [x] 运行 `python -m pytest tests/test_new_book_pipeline.py tests/test_reading_merge.py tests/test_planning_agent_repair.py tests/test_existing_draft_repair.py tests/test_identity_auto_closure.py tests/test_posture_pipeline.py tests/test_story_methods.py`，随后运行全量 `python -m pytest`。
- [x] 修复实际连接缺陷时先保留失败回归；保持既有验收口径。
- [x] 提交 `feat: resume source-grounded new-book planning through existing agents`。

Expected: 干净分支包含原工作区所有本轮采用的前端依赖，不能依赖未跟踪模块才能运行。

## Task 4: Merge the proven Aoshi production fixes

Files: 以 `.runtime/aoshi-film-pilot-20261020/source-change-index.json` 的 12 源码为准，连同真实相关回归；不复制历史授权或现场运营控制器。

- [x] 对比原 frozen execution baseline 与当前集成树，仅加入该轮实拍验证的增量。
- [x] 运行 `python -m pytest tests/test_preparation_identity_handoff.py tests/test_episode_agent_inputs.py tests/test_agent_frame_evidence.py tests/test_review_frame_tail.py tests/test_fill_reading_cast.py tests/test_input_source_verdict.py tests/test_incomplete_model_retry.py`，再做必要全量回归。
- [x] 提交 `fix: reuse prepared assets and resume source failures without new budgets`。

Expected: 缺图延后补齐能回填当前集、身份失败接回原 Agent、错误格式保持技术失败分类、尾帧和关键动作未知仍保留。

## Task 5: Archive the optional provider and H3 runtime recipe

- [x] 合入官方 DS41 三文件的独立增量，默认 Agent 与生成路由保持当前版本。
- [x] 运行相关 `test_endpoint_presets` 和模型客户端回归，独立提交。
- [x] 将已推送 H3 0.5.17 的 Dockerfile、快照导出器、入口、复验脚本和去机器临时信息后的说明归档到 `scripts/ops/h3-live-0517/`。
- [x] H3 归档只验证脚本语法、镜像清单和既有实际 GPU 回执；不重新构建/上传同一镜像，不声称四卡生成已测。
- [x] 独立提交归档源码；源权重和 rootfs 均不入 Git。

Expected: 可选模型显式选用；镜像地址、外置权重和已验证范围可复查。

## Task 6: Verify cached delivery, review, publish and run a real episode

- [x] 复用已有真实素材验证准备/审修/合成/交付接续，原计数逐项相同。
- [x] 对整个分支做一次 fresh-context 独立复核；重要问题用失败→通过回归修复，随后全套绿。
- [x] 重新核对 main HEAD/index/工作区变化，将已验证的集成提交快进到 main，保留快照之后的并行改动。
- [x] 向现有 origin/main 推送，回读远端 ref。
- [ ] 从干净版本选一章已有正常规划、未耗尽原预算的复杂章，通过标准入口跑到正式交付，记录时间、实际用量、生成次数和远端回执。
- [x] 写明真实成功、外部卡点及未验证项；收工更新工作板。

Expected: main 可干净检出复现；真实模型/交付结果与离线回归分别记录。服务额度不足时保留实际卡点，不新造余额或重置预算。

真实验收已按标准入口尝试一次：傲世22（18段/210秒）准备阶段chapter_binding_review返回402，未生成视频，真实交付条目保留未完成。用户指定本地成片和封面；运行目录、原活动、原计数及预算均保留，详见收口记录。
