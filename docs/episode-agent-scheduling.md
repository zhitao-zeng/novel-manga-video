# Agent 集级制作与共享调度

本实现位于 `codex/episode-agent-scheduling`，基于独立 `feat/episode-agent`。
保留现有标准产线；没有修改运行目录、小说资源配额、历史产物或暂停标记。

## 职责与调用

```text
pipeline / Conductor：章节范围、集级并发、共享容量、暂停、恢复、状态
  └─ 每集独立宿主进程：工作目录 + 保存的 Agent UUID + 一个工具桥接器
       ├─ Agent 回合：申请模型槽 → --session-id / --resume → 结束并释放槽
       └─ 已接收工具：生成 / 独立审片 / 修复写回 / 合成验收
              → 保存实际结果 → 恢复原 Agent
```

同一个 Agent 持续决定该集的创作及修复。宿主执行既有媒体工具和独立判官；
Conductor 管理执行时机，不编写镜头或修改最终请求。每集仍只接受一个耗时工具操作。
工具收到 `accepted/yield` 回执后，Agent 正常结束本轮，宿主随后执行；
不能先启动同端点的审查、同时让父 Agent 占着唯一模型槽等待它。

`episode/flow.py` 保留宿主桥接器，跨多次 CLI 回合恢复同一会话。
`llm/resources.py` 在已有文件槽机制上限制实际模型服务；普通规划、审查、建档
请求和外部 Agent CLI 共享额度。模型请求参数、H3 请求、缓存选择、修复动作、
字幕和质量门沿用原实现。

## 资源

- 公共模型配置来自 `resources.planning_models`。逗号分隔的服务组共用其总槽数，
  不为每个地址重复增加额度；同服务别名也共池。未知服务保留原行为。
- H3 使用调度器选定的 key/pool、相同 `NOVEL_INFLIGHT_DIR` 与正数上限，
  继续使用实例槽、夜班 leases、共享 limit、AIMD 和停车机制。
- 每集片段 workers 由配置传入。集宿主数量和实际模型/视频请求数量分别统计。
- ASR 与 FFmpeg 仍使用原线程和执行模块，本次没有新建全局 CPU 调度器。
- 模型等待可被暂停打断；已持有资源并执行的操作继续收尾。

资源配置只包含地址、额度和凭据变量名。凭据不进入 Agent 输入或状态文件。

## 选择执行方式

在更新到本分支的运行目录中，可显式启动：

```bash
python scripts/pipeline.py start --novel BOOK_ID --executor episode_agent
python scripts/pipeline.py status --novel BOOK_ID --flow production --json
python scripts/pipeline.py stop --novel BOOK_ID --flow production
```

也可在已有小说配置中增加：

```json
{
  "executor": "episode_agent",
  "agent": {
    "input_mode": "auto",
    "parallel": 3,
    "workers": 2,
    "output_root": "outputs/episode-agent"
  }
}
```

这里是配置示例，没有自动启用任何小说。未配置时仍为 `standard`；
`parallel` 是集宿主上限，公共模型与视频额度仍独立约束真实请求。
Agent 执行器当前只支持 H3，SD 配置会在启动前报错。
`source` 从完整原文创作；`script` 保留已有剧本、对白、镜头、时长及引用，
只成稿最终请求；`auto` 有剧本时选择 `script`。

Agent 产物继续写到隔离输出目录。源小说、人物库和原视频不被回写；
最终候选路径由 `finish` 与运行状态给出。这不是历史视频迁移或批量替换操作。

## 暂停、恢复和停止条件

复用 Conductor 的进程表及 `state.json`，在已有 `agent-run.json` 中保存阶段、
UUID、当前工具回执、结果、截止时间和累计会话用时，不新增队列或数据库。

- 重复启动及控制器重启接管已有集宿主；源集的 `.render.lock` 防止重复执行。
- 暂停停止新派单，已接收但未启动的工具保留；已启动工具结束后保存结果并暂停。
- 重启视频操作使用供应商原任务记录和缓存；未启动的新稿提交保留其文件参数。
- 中断的 `apply` 不自动再次写回，原 Agent 先读取当前计划与历史再决定。
- 已完成的最终验收按当前会话及完成回执恢复，不再额外调用模型。
- 完成状态要求宿主 `finish` 回执与当前结果一致，写一个“通过”文件不能完成任务。
- `blocked`、`finished_with_issues` 不由 Conductor 无条件重跑；没有通过的片段保持未通过。

正常工具交接不计入三次异常续跑上限；无进展停止及视频真实生成额度保持。
整集截止时间在续跑时不重置，已开始的任务按原机制收尾。

## Skill 与证据

核心 `episode-production` 保持简短，输出契约、编剧、H3 格式、审修经验及有限案例
按需读取。官方 Ref2VA 原文、许可及来源随 skill 提供。`prepare` 的 manifest 列出
真实可读的参考路径；代码工作树负责 skill，运行目录中的旧同名 skill 不覆盖它。

经验保留已有证据的边界：不把换 seed、缩短请求、关键帧或某种音效视为万能修法。
正常运行仍由 pipeline 自动执行和修复，不由外层操作者逐段手改。

CLI 与看板读取同一份生产快照，展示每集阶段、等待对象、具体问题、工具结果及池占用。
生成次数显示实际回执数据；没有数据时显示缺失，不以回合数推算。

## 验证边界

测试采用模拟模型/供应商结果、真实跨进程文件锁及实际前端渲染。
覆盖同端点争用、工具交接、同 UUID 续跑、正常交接超过三回合、暂停/重启、
缓存/任务恢复、已完成 finish、预算及既有冻结请求。

本轮没有调用真实图片、文本或视频生成服务，也没有部署或启动生产。
真实多集吞吐和新 skill 的模型服从性仍需在隔离小批量验证后判断。
现有独立验收仍以片段画面、ASR及技术检查为主，不等同于新增了整集剧情判官。

完成验证：全量 **2000 passed**（36.21 秒），包含原有冻结回放、跨进程模型资源
争用、共享 H3 参数、正常/异常交接、同会话恢复、暂停、外部批任务接管、
宿主完成回执、循环依赖检查和 Node 看板渲染。Skill 结构校验通过。
另做一次独立原文/实际图片的离线写作演练；它验证技能可操作性，不是 Qwen 或 H3 实拍评测。

## 诊断事件与看门狗（2026-10-09）

宿主把实际操作、任务/阶段迁移、审查完成、验收保存和成功发布追加到
`ctx.run_dir/episode_state.jsonl`。它是可删除的诊断记录，不是另一份生产状态；
所有记录均写同一运行目录，章节目录的审查/发布函数不反向依赖Agent。
事件写入失败不改变原动作；只读status/inspect不追加事件，重复预算更新不算新进展。

`execution.json`和`agent-run.json`仍决定PID、阶段与停止状态。看门狗只借助
最后有效事件的时间补充活动证据，取三个时间的最大值；旧事件不能压过更新状态。
告警带last_stage，但仍只报告“活动超时且当前宿主进程已退出”，不检测或杀死活着的等待任务。

```bash
.venv/bin/python scripts/episode_watchdog.py --runtime <各run_dir的父目录> --once
```

runtime应按真实run_dir定位，不能把章节目录或某个运行快照的代码目录当作它。
默认不加--once时每120秒读取一次，告警追加到父目录的watchdog-alerts.jsonl。
本次不新增自动启动、恢复或Conductor动作，保留既有暂停、终态及最终回执豁免。
实施边界见[台账与诊断计划](plans/2026-10-09-ledger-facegate-state.md)。
