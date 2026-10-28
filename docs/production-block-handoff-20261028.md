# 生产阻塞到审修 Agent 的交接

2026-10-28。十日 1–300 集续跑期间，首拍阻塞的逐片原因停在生产者记录里，没有进入输入修复 Agent 的任务视图。

## 现象与证据

- 最新状态中有 85 集处于 `input_repair`，其中 82 集 `agent-run.status=blocked`、`no_progress=2`，原因均为 `episode agent ended twice without operation or output progress`；85 集的 `agent-run.remaining_seconds` 都大于 0，合计约 89.3 小时。
- 这 82 集的 `recovery-diagnosis.json` 的 `issues` 全为空；同时 82 集的 `batch-production.json` 都有当前逐片 `blocked` 记录（例如第 32 集 26 段、第 104 集 5 段、第 135 集 28 段）。
- 85 集都不存在 `sandbox/input/latest-preparation.json`，而首拍输入修复提示仍要求读取该文件。
- 归类后的阻塞原因主要有：`ValueError: asset: missing required image ...`（缺参考图）、`H3 conversion did not produce a valid current request` 与 `... build_h3_prompts.py ...`（转换/准备失败）、`ValueError: H3 request/reference mismatch: ...`（请求与参考图内容冲突）、`ValueError: risk: ... 穿着 ...`（穿戴风险）、`RequestCheckIncomplete`（复核未完成）。

旧诊断只遍历 `latest-review.json` 的目标；首拍失败通常还没有任何审看记录，因此逐片阻塞从未被读取。另有 29 个地点图缺失，规模最大的 `location_076` 涉及 22 集、308 段；工厂日志为本地 Qwen 图片服务 `127.0.0.1:8770` 连接被拒。

## 本次修改

- `recovery.current_blocks`：从当前 `batch-production.json`（`status=blocked`）读取逐片原因；没有阻塞回执时退回 `thin_media_report.json.blocked_clips`。两种来源都只保留当前分镜中仍未拍摄的视频片段。
- `recovery.block_kind`：把原因分为 `missing_asset`、`conversion`、`request_conflict`、`wear_risk`，其余仍走 `input`/`execution`。每类给出对应处理边界，避免用改请求绕过缺图、或用重复 render 修准备失败。
- `diagnose`：把上述逐片原因写入当前任务 `issues`，与既有审看/语音/发布阻塞并列；已拍摄片段和已完成生产者回执不下发，相同文本去重。
- `semantic_prompt(ctx)`：仅当 `latest-preparation.json` 实际存在时才要求读取；否则指向 `current-task.json` 的问题与证据文件。
- `resume_handoff`：仅对状态 `blocked`、阶段 `input_repair`、原因为上述无进展停止、仍有剩余时间、无在途任务、且生产者回执有当前未拍摄阻塞的 episode 恢复一次。恢复沿用原活动、已用次数、`remaining_seconds` 和会话记录，写入 `handoff-recovery.json` 备份，不清空阻塞、不新增拍摄次数；同一 run 只会自动恢复一次。
- `resume_review`：审看阶段 `finished_with_issues` 且最后一次 finish 仍列出未通过片段时，只有这些片段实际允许 `edit`/`retake`/`input` 且还有剩余拍摄次数才继续一次；已用尽次数或只剩 `crop`/`keep` 的不重开。同样保留原活动、计数和余时，并写入 `review-recovery.json`。第 25 集属于该情形：修复提示词已进入当前计划，但新一轮 take 尚未渲染。
- 调度器 `_observe` 与单集入口 `start_run` 都接入这两种恢复；标记随结果保存，供后续审计。

## 验证

- 新增 `tests/test_production_block_handoff.py`：覆盖无审看记录时的逐片交接、三类阻塞分类、已拍片段/已完成回执/无阻塞不恢复、恢复一次并保留原时间、提示只引用存在的准备文件，以及当前任务保留对应 clip 的 issue。
- 全量测试通过；用第 32、104、135 集真实记录只读回放：分别得到 26 条（15 请求冲突/8 复核未完成/3 转换）、5 条（1 请求冲突/3 穿戴风险/1 转换）、28 条（全部缺图）当前阻塞，`actionable=true`。
- 本次修改只改变阻塞证据的交接与一次有界恢复，不改变预审、独立审看、视频质量门、H3 路由、拍摄次数或阶段预算。

## 恢复执行

- 第 105 集先做单集验证：恢复后当前任务带入 3 条 `conversion` 阻塞，Agent 一次提交了 3 段英文请求修正并通过 `check`/`apply`，随后正常渲染 28 段并进入独立审看。
- 随后对 79 集符合条件者一次性写入 `handoff-recovery.json` 并按原活动、原计数、原剩余时间转回 `deferred`；77 集排队，恢复标记持久保存。第 20、105 集再次因两轮模型切片内没有工具操作而停止；其余恢复集继续排队。
- 5 集未恢复：第 19、21 集为已有的共享源次数用尽（需新的逐片授权），第 26 集 clip_09 只剩 `repair_assets` 且源区间次数为 0；第 135、172 集在原回执失效后已由标准生产重新接管。

## 缺图补齐

`NOVEL_LOCAL_QWEN_IMAGE_URL` 指向未监听的 8770，GPU 又均被其他用户或共享服务占用，未启动本地图片服务。改用本书既有卡的原始通道 `phanrouter/gpt-image-2`，只补缺失卡、不重画已有卡：

- 地点卡 26 张（含 `location_076`）与角色卡 12 张已生成并过卡审；`location_052` 首次因旧任务回执“succeeded 但无 URL”失败，修复为仅对缓存任务重提交一次后成功。
- 已用云卡与既有卡同模型同画风方言；抽查 `location_076` 为无人的地下赌场，符合设定。`character_117`（赤裸男人）被内容审核改写为着衣版本，卡可作参考但与原文“全身赤裸”不符，留给后续画面审查处理。

## 未解决

- 第 19、21、26 集受源区间拍摄次数限制，需用户逐片授权或接受未完成；不能自动补次数。
- 第 105、20 集两轮切片各 600 秒内模型没有产生工具操作，已按原规则停止，余时分别为约 515 秒和 1534 秒；这不是阻塞证据缺失。
- 第 25 集已完成一次 `retake` 决策并交回生产，clip_03 仍待实际重拍与复核。
- 其余 77 个恢复集仍在文本宿主队列中，需通过正常输入修复、首拍和独立验收；代码回归与恢复入队不等同整集交付。
