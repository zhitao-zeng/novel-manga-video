# 产线职责收尾与配置隔离 · 2026-09-17

本轮落实规划、人物账本、调度、看板缓存及实验隔离，并检查诸天现有章节。模型生成策略、原有文件格式、并发池、重试预算和发布门槛保持原规则；修复了按小说/判官切换时的配置串用。

## 当前职责

| 层 | 位置与职责 |
|---|---|
| 规划规则 | `planning/`：独立 PlannerContext，预算、文本处理、字段结构、提纲约束、人物查找、校验、统计与输出结构。 |
| 规划流程 | `planner_context_thin` 读章节资料；`planner_requests_thin` 组装和执行原模型请求；`planner_flow_thin` 编排与写回。原规划命令52行。 |
| 实体规则 | `entities/`：原文证据定位、候选检索、关系定义及问题结构。 |
| 人物账本 | `ledger_store_thin` 管数据和查找；`ledger_judges_thin` 管模型判断；`ledger_extraction_thin` 抽取；`ledger_resolution_thin` 处理身份决策；`ledger_views_thin` 提供索引、出场快照和诊断。原命令84行。 |
| 修复调度 | `repair/scheduling.py` 共享流程与容量定义；`repair_manager_dispatch_thin` 派单；`repair_manager_workers_thin` 执行/回收；`repair_manager_state_thin` 汇总；Manager保留状态与主循环。命令63行。 |
| 生产总控 | `conductor_dispatch/state/workers/capacity` 分开派单、状态、进程与容量调节；Conductor保留状态与主循环。命令37行。 |
| 批量执行 | `production_flow_thin` 编排，`production_render_thin` 渲染步骤与原重试，`production_assets_thin` 资产工作池，`production_reports_thin` 报告。命令112行。 |
| 看板 | `dashboard_inventory/history/resources` 采集与统计，`dashboard_ui_thin` 页面，`dashboard_service_thin` 组合数据，HTTP入口49行。 |
| 快照缓存 | `dashboard/cache.py` 保存已完成的历史与状态快照，过期后后台刷新；当前交付与控制器状态仍在每次响应重新读取。 |
| 实验 | 旧 `script_planning`、`creative_direction`、`production_plan` 移入 `experiments/legacy`；共享资产工厂仍在 `media/asset_factory.py`。 |

外部命令参数保留。内部调用方改为引用能力所属模块，不再以命令脚本作为工具库。没有添加数据库、任务队列、兼容入口或新的生产报告格式。

## 配置隔离与有意改变的行为

- 普通审查使用独立 ReviewRules。没有专门规则的小说恢复默认规则，不再沿用之前小说的 breakdown_pattern；角色等级随本次读取。
- PlannerContext保存片长、预算、别名、实体表、聊天方式和提示词。15/30秒、不同小说、不同类型交错处理不串用。复用上下文时，缺少聊天设置和类型模板也会恢复默认值。
- 打包时参考音频、群聊、类型规则和保存的15/30秒上限也使用独立 CompilerOptions；拆段工具不再改写共用片长，原有锁和拆分规则保留。
- 账本和修复不再靠导入 `second_review` 改写进程环境。精判实例保存独立 JsonEndpoint；原有本地/Flash预设仍可选，模型与端点不会被另一实例覆盖。凭据不写入产物，配置对象表示也不显示密钥。
- 看板将完成的历史/状态快照保存到忽略的 `outputs/.dashboard/`。重启可以先显示上次历史结果，刷新失败保留旧数据并标注；缓存不决定交付、派单或重试。当前交付数从12变13等实时变化不受历史缓存限制。

## 对照与验证

- 5个冻结章节分别按15/30秒处理，共10组：20个模型请求、预算、schema和校验结果与整理前一致。
- 同一批5章的42个完整片段计划、请求与切段结果，与前一轮冻结结果一致。
- 账本合并/撤销前后的10份记录、场景快照和索引一致。
- 17种修复任务步骤的命令和选定非敏感环境参数、一个混合场景的4项派单结果一致。
- 原并发、缓存、发布、暂停、待准备和故障续跑测试保留。增加判官/小说交错隔离、暂停时不隐式接管、历史缓存重启与后台刷新等回归。
- 看板两页在加入缓存状态说明前与原HTML相同；现有JavaScript渲染测试继续通过。
- 实际提交版本846项、保留其他本地改动的工作树848项测试通过，九个命令入口在独立解释器中的启动检查通过；部署验证结果另存同目录日志。

验证中发现旧测试及一次调用迁移存在未完整模拟的文本判断请求，曾访问本地文本接口。已补齐模拟，并新增整个测试套件的真实HTTP连接拦截；以上最终完整测试在拦截启用后通过。没有启动真实视频生成，生产小说、人物库和视频没有被这些测试改写。

## 内容验收与运行边界

[诸天抽样核对](zhutian-content-audit-20260917.md)覆盖9章，其中5章有45段现有视频，抽查135帧。确定优先重规划2130、2131、2372；其余按报告局部处理。这里只输出核对结论，没有执行重规划或重拍。

本轮保留其他会话的23项本地改动。诸天保持暂停；部署只重载必要的管理与看板进程，任务记录、预算、已有视频和缓存不清零。运行对照、测试日志和抽样材料保存在本机 `outputs/architecture-20260917/`。
