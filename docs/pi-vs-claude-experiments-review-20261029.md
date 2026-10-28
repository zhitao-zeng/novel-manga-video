# Pi vs Claude Code：实验记录与可质疑的结论

日期：2026-10-29。目的：给第三方审阅者一份完整、可复现、包含反证的记录，而不是只给结论。

## 0. 一句话结论（可被推翻）

- 已做：输入修复 A/B、10 集输入修复 pilot、4 段创作/渲染/审看、official 端点模型速度、宿主门禁修复。
- 目前的建议：**Pi 作为输入修复的 Agent loop 有条件可替换；整条创作/审看/交付 harness 还不能替换。**
- 这个建议**可能是错的**，第 6 节专门列了反证和替代解释。请优先看那一节。

## 1. 对照基线：当前生产

- 生产仓库：`/mnt/disk1/zengzhitao/novel/novel-manga-video`，main。
- Agent loop：**Claude Code CLI**，Docker `agent-sandbox:0918`，每次 600 秒切片（`agent_slice_seconds=600`），切片间 resume/轮换。
- 并发：5 个文本宿主；云端 H3 4 路 + 本地池；生产、未知续排、交付收集三个服务 active。
- 宿主侧：`episode.py` 工具、预算/回执/未知提交、独立 review/finish、交付收集。
- 快照（2026-10-29 01:11）：正式交付 **103/300**、首拍完成 **202**、缺首拍 **674**、待派 159、blocked 34。

## 2. 隔壁框架：pi-agent-kit-v2

位置：`/mnt/disk1/zengzhitao/interactive-video-worktrees/pi-experiments-20261028/.runtime/pi-agent-kit-v2-20261028/`

来源与设计（来自它自己的 README/V2-NOTES/VALIDATION）：

- 基于 `pi-editable-starter-20261028/examples/pi-video-agent`；
- 原生 read/write/edit/bash、原生会话与 compaction；
- 配置 1,048,576 上下文 / 128,000 输出（README 明确“服务实际能力仍以端点为准”）；
- 没有固定镜头数、生产阶段、候选次数或 finish 路由；
- 工具可编辑，`tools/`、`SYSTEM.md`、`TASK.md`、`skills/` 都是任务副本；
- 自带 `tools/media.py`（参考图/上传/任务回执）和 `assemble.py`；
- 官方 smoke：一条 10 秒单段（EP04C），**17.9 分钟、54 次工具执行（32 read + 22 bash）、3 条 take**；
- 它自己声明：会话结束不代表成片通过，未做新整集真实验证。

**它不是本项目的生产 harness**：没有预算/回执门禁、独立 review/finish、300 集调度和交付收集；smoke 是单段，不是整集。

## 3. 实验

### 3.1 输入修复 A/B（第 20 集，隔离副本）

设置：

- 复制第 20 集运行目录两份，互不共享状态；
- 同一 `current-task.json`、同一 current-status、同一套 `episode.py` 宿主桥；
- 双方禁用 render/review/finish，只允许 check/apply/status；
- 同一 official DS41 模型；Pi 1M/128k，Claude Code 自身 200k/32k；
- 单次上限 1800 秒。

结果：

| | Pi | Claude Code |
|---|---|---|
| Agent 时间 | 894.6s（首轮 6/7，clip_03 被范围复核挡下） + 166.7s 同 session 续跑 = **1061.3s** | 1800s 超时无 repair + 369.5s 原会话续跑 = **2169.5s** |
| 工具调用 | 38 + 11 | 57 |
| 最终 apply | 7/7 target applied | 7/7 target applied |

事实：Pi 快约 2 倍，但第一次漏了 clip_03，靠门禁反馈 + 同 session 续跑补齐。Claude 一次全过，但第一次 30 分钟超时。

### 3.2 10 集输入修复 pilot（隔离副本）

设置：

- 10 集：12、20、21、24、26、32、45、46、103、242；
- 每集复制生产 run dir；Pi 1M/128k；`episode.py` 宿主桥；禁 render/review/finish；
- 每轮开头按宿主 status 重新生成 current-task（production 也这样做）；
- apply blocked 时同一 Pi session 自动续跑，最多 3 轮。

结果（45/46 加 current-task 刷新后重跑）：

| 分类 | 集 | 说明 |
|---|---|---|
| 实修 | 103、242、32、20、21 | crop / edit / input；changed 或 media_changed / input_repaired |
| 正确 stop | 12、24、26 | 真实源区间额度 / SubmissionUncertain |
| 无合法 target | 45、46 | 45 宿主 status 无未解决项；46 需要 review 产出 target（pilot 禁 review） |

合计 **4199 秒（70 分钟）**，平均 **7.0 分钟/集**，**0 人工介入**。

关键纠正：第 12 集 Pi 报告“授权丢了”，实际检查：

- `repair_budget_grants.json` clip_08 条件额度 limit 5、授权 2 次、prepared/activated；
- `source_budget` limit 5、used 4（clip_08 3 + clip_10 1）、pending 1（clip_08 SubmissionUncertain）、remaining 0；
- 授权没有丢，是 source-family 5 次上限用满；`conditional_extra_takes` 显示 limit 3 只是视图公式。
- 这说明 Pi 会做深度探测，但也会过度推断；宿主账目和门禁必须保留。

### 3.3 4 段创作/渲染/审看（第 20 集子集）

- 初始 3 段（03/04/05）是无效形状：`clip_04` 是 stage 10 part [1,2]，part [2,2] 在 `clip_11`；裁掉 clip_11 后 `ranges: stage 10 has missing...` 是必然的，不是 Agent 能修的。
- 补回 `clip_11` 成 4 段后：
  - `clip_03`：pass / retained；
  - `clip_04`：真实 H3 290 秒，review pass；
  - `clip_05`：`reviewed extra-take authorization unavailable after input preflight`；直接调用 `activate_extra_take` 返回 True，grant prepared/remaining 都正确，属于激活时序问题；
  - `clip_11`：`location: clip location differs from its source stages`；clip 级 location 仍是空屋，shot 10 已改走廊，属于 clip 级派生字段未同步；
  - 未 assembly / finish。

结论：Pi 能真实出片并通过审看（03/04），但 4 段全链路还没完成；剩余两个 blocker 是宿主门禁/字段派生，不是 Pi 的上下文或工具能力。

### 3.4 official 端点模型速度与上下文（原始 API，不是 Agent loop）

同一 official 端点、同一提示、非流式、temperature 0.2：

| 模型 | 2000 token ×3 | 4000 token 完成一次 |
|---|---|---|
| deepseek-v4.1-flash-1017 | 18.8–19.0s，104–107 tok/s，1/3 有正文 | 17.3s，1722 tok，正文 1327 字符 |
| kimi-k3 | 47–56s，36–39 tok/s，2/3 有正文 | 76.0s，3296 tok，正文 1246 字符 |
| glm-5.3 | 30–34s，58–66 tok/s，0/3 有正文（全 reasoning） | 38.5s，4000 tok 全 reasoning，0 字符（同端点；本地网关 4000 一次 36.7s/1328 字符，另有一次 504） |

上下文/输出上限：

- **990,036 prompt token** 的请求被接受，耗时 25.9 秒；
- `max_tokens=128000` 被接受；
- 因此 `200k/32k` 是 Claude Code 客户端元数据上限，不是 official API 上限。

限制：这是原始模型调用，不含 Agent 的多轮工具开销；不能直接推 Agent 的端到端速度。DS41 在同端点最快，K3/GLM 更慢/更重 reasoning。

### 3.5 宿主门禁修复（与 harness 无关，但影响结果）

- 交接：82 集输入修复 `issues` 为空、76 集被要求读不存在的 `latest-preparation.json`；已修（`325d9c9` 等），79 集恢复。
- 静音：20 集已组装但只失败 `silence_ratio`；改为观察项（`cc0a139`），11 集直接收尾。
- 缺图：26 张地点卡 + 12 张角色卡经本书原有云图片通道补齐；`location_052` 旧“succeeded 无 URL”回执修复。
- accepted-risk：input 编译不认 `render_risks_accepted.json`（`20c4c58`）。
- status：`conditional_extra_takes` 只显示视图 limit，未显示 source_limit/used/pending（`91d2d53`）。
- 待修：clip_05 extra-take 激活时序、clip_11 clip 级 location 同步、exit 124 在预算边界被误标为 `invocation failed`（19 个 blocked 集）。

## 4. 结果汇总

| 实验 | Pi | Claude | 结论强度 |
|---|---|---|---|
| 输入修复单任务 A/B | 1061s / 7 targets | 2170s / 7 targets | 中：单任务、Claude 含 30 分钟超时 |
| 10 集输入修复 pilot | 5 实修 / 3 正确 stop / 2 无 target；0 人工；7 分钟/集 | 无同 10 集对照 | 中偏弱：只有单任务 A/B 对照 |
| 4 段创作/render/review | 2/4 段 H3 + review 通过；2 个宿主 blocker；无 finish | 未跑同子集 | 弱：不是完整集，未 finish |
| official 模型速度 | DS41 最快；K3/GLM 更慢 | 同端点数据 | 强（原始 API），但不等于 Agent |

## 5. 当前建议（条件式）

1. **输入修复**：可以接 Pi 快路径，但必须保留宿主门禁、自动 blocked 续跑、每轮 current-task 刷新；容器隔离后再切队列。
2. **创作/审看**：继续 Claude，直到 4 段/完整单集在 Pi 上跑通 render + review + finish。
3. **生产优化**：600 秒切片提到 900–1200；压缩 current-task/evidence；修剩余宿主门禁；H3 4 路/本地池保持。
4. **资源**：12 的 SubmissionUncertain、24/26 的 source 额度需要用户核销或逐片授权。

## 6. 为什么这个建议可能是错的

请审阅者重点挑战这些点：

1. **不是同条件比较**：Pi 用 1M/128k，Claude Code 用 200k/32k，且 Claude 有 600 秒切片。2 倍可能来自上下文/输出上限和切片，而不是 harness；如果 Claude Code 换自定义客户端或调大限制，差距可能消失。
2. **样本太小**：输入修复只有 1 个任务 A/B；pilot 没有同 10 集的 Claude 对照；pilot 的“5 实修/3 stop/2 无 target”是事后分类，分母有争议；不能外推 300 集。
3. **创作/审看没有完整证据**：4 段只过 2 段，没 finish；宿主门禁（extra-take 激活、clip 级 location）是当前主 blocker，换 harness 不解决。
4. **H3/宿主时间可能主导**：14 秒片段实际渲染约 290 秒；全链路提速可能主要取决于 H3 和门禁，而不是文本 harness。2 倍输入修复速度不等于 2 倍交付速度。
5. **Pi 有过度推断**：第 12 集把 source-family 用满误报成“授权丢了”；虽然 status 已补 source 账目，但说明 Pi 的结论需要宿主门禁兜底。
6. **容器/密钥隔离没做**：pilot 的 Pi 在宿主进程跑，key 在 env；这不是生产可接受的形态，容器化后速度可能下降，结论可能变化。
7. **生产已在改善**：交接、静音、accepted-risk、并发等修复已经让生产到 103/300；也许继续修 Claude 的 600 秒/提示/门禁，比切 harness 更稳。

## 7. 希望审阅者回答的问题

1. 在相同上下文/输出限制、相同门禁下，Pi 的 2 倍是否可复现？
2. “5/5 可执行集实修”是否足以支持切输入修复队列？正确的分母应该是什么？
3. Pi 的创作/审看质量能否在完整单集上通过同一套独立 review/finish？
4. 容器化后 Pi 的速度/稳定性是否仍成立？密钥暴露是否可接受？
5. 是否应先修门禁 + 把 Claude 切片提到 1200，再重新测 Claude，而不是现在切 Pi？
6. 这份实验是否过拟合到“第 20 集/输入修复”这个单一场景？

## 8. 证据与复现

- 输入修复 A/B：`docs/pi-harness-ab-20261028.md`；`.runtime/pi-ab-20261028/pi-result.json`、`claude-result.json`、两臂 session。
- 10 集 pilot：`docs/pi-input-repair-pilot-20261028.md`；`/mnt/disk3/zengzhitao/novel/shiri-pi-pilot-20261028/pilot-report.json`、`ep*/pass*.json`；adapter `.runtime/pi-ab-20261028/pi_pilot.py`、`host_bridge.py`。
- 4 段创作：`.runtime/pi-ab-20261028/pi/episodes/shiri/shiri_20-script/`；日志 `pi-create-*.log`。
- 模型速度：本轮的 `/tmp/bench*.py` 输出（official 端点、同提示、2000/4000 token）。
- 隔壁框架：`interactive-video-worktrees/pi-experiments-20261028/.runtime/pi-agent-kit-v2-20261028/{README.md,V2-NOTES.md,VALIDATION.md}`；smoke 日志 `smoke-v2.log`。
- 生产：`/mnt/disk3/zengzhitao/novel/shiri-h3-production-20261026/progress.json`；交付目录 `delivery/`。

> 本文档刻意保留反证和不确定性；如果审阅者认为某条证据不足，请直接指出对应实验/文件，不必接受第 5 节的建议。
