# 结构债清单与两批拆分（2026-10-08）

盘点基线为 main `71f6c67`。按独立修改、独立验收的工作单元归并为15项；
函数内import和文件行数是线索，不重复计债。标准执行器与Agent共用Conductor，
且Agent目前只支持H3，保留两种执行方式不是一项待删除的债。

第一批在 `codex/production-structure-20261008` 实施1–5；第二批实施6–12，见文末。
历史视频、缓存、预算、任务记录与暂停标记保留；没有新增持久化格式、队列或模型调用。

## 固定清单

| 编号 | 内容 | 本轮状态 |
|---|---|---|
| 1 | 规划主流程混合资料、请求、重试与写回 | 完成拆分 |
| 2 | Agent会话生命周期过度集中 | 完成拆分 |
| 3 | 八个工具与证据/校验堆在同一模块 | 完成拆分 |
| 4 | 输入修复混合参数、候选、编译与发布 | 完成拆分 |
| 5 | 共享能力依赖Agent私有实现 | 完成本轮明确依赖修正 |
| 6 | 渲染编排仍夹带请求修正、缓存及次数处理 | 完成；请求修正、缓存、提交、分析和Context构造独立 |
| 7 | 人物库增量模块兼管多种操作与持久化 | 完成；读书事实、抽取、设计、增长和摘要独立 |
| 8 | 共用字段在Agent契约与修复中重复校验 | 完成；动作/穿戴/对白核心字段与值校验共用，保留接口差异 |
| 9 | 有效配置与覆盖关系解析分散 | 完成本轮集中解析；小说、已有计划与Agent环境由resolved_settings组装 |
| 10 | 渲染运行标记混进片段输入与宽Context | 完成；RenderOptions/RenderState与片段输入分离 |
| 11 | 跨步骤素材/分析/验收字典契约偏松散 | 完成本轮交接；媒体结果类型、选中take与验收共用实现 |
| 12 | 普通、嵌套Agent、分集产物发现规则不一致 | 完成；平集/分集/嵌套Agent读取共用定位，声线支持附加来源 |
| 13 | 9个thin脚本仍含业务逻辑 | 整理收尾 |
| 14 | render/bible/qc等根级模块归属 | 整理收尾；都在正式使用，不能按孤儿删除 |
| 15 | 架构文档与工作记录同步 | 整理收尾；本轮补充当前拆分职责 |

## 规划职责

`application/planning/flow.py` 只协调：

1. `source_inputs.prepare_source`：章节/分集合并、小说配置、预算及原文段落。
2. `cast_inputs.prepare_cast`：身份资料、演员/地点菜单和前文连续性资料。
3. `request_inputs.prepare_request`：已有分镜绑定或普通请求、dry-run。
4. `attempts.plan_attempts`：原有有界规划、补丁、压缩、复核与门记录。
5. `publication.publish_result`：原产物写回、下游失效、recap和演员历史。

`run_types.py` 中的交接类型只在内存使用。原文、剧本、计划和报告的JSON字段不变。
主函数从794行降到8行；复杂的规划策略继续由既有业务模块提供。

## Agent职责

- `flow.run_episode`：章节锁与流程协调，工具桥接收尾、暂停和有界续接。
- `lifecycle`：恢复/重试原记录、已有成片收尾、运行时限和资源等待计时。
- `startup`：恢复已接收工具、接续输入修复、首拍准备；不重复创作。
- `turns`：一次模型槽申请、CLI调用、释放模型槽后交接工具。
- `runtime`：环境应用/恢复（第二批把组装移至resolved_settings）；`prompts`：原有初始与续接说明。
- `progress`：实际进展判断；`resume_inputs`：既有输入/请求接续。

`EpisodeRunState` 是本进程的时限与状态，不落盘新格式。继续使用agent-run、execution、
工具回执和原会话UUID。流程文件1170→271行，主循环481→240行。

八个外部工具命令和参数保留。`EpisodeTools` 为组合入口，内部显式持有：
`DraftOperations`、`MediaOperations`、`RepairOperations`、`EpisodeEvidence`、
`EpisodeCompletion`、`EpisodeInspection`。这些服务没有多重继承，也不传入整个工具对象
作为私有方法代理。首拍准备明确接收草稿和修复操作；渲染也明确传递所需修复服务。
工具入口1134→75行；独立服务负责原有业务，未清零或放宽生成次数。

## 输入修复职责

`input_contracts` 保留原有参数约束；`input_candidate` 准备原有范围内的剧本/资产候选；
`input_compilation` 重编译、复用翻译、检查对白/时长/依赖和实际参考，返回原RepairProposal。
`input_repair` 管理收据、原文复核、资产操作、发布、刷新和原有异常还原顺序。
其主函数410→204行。没有修改无有效变更、逐段容错、费用/次数或失败候选发布策略。

## 共用能力归属

探针移至 `application/rendering/probe.py`。标准渲染不再依赖Agent包。
开拍判断与原稿归档放在 `application/production/artifacts.py`，接收明确的目录。
原native_episode是Agent的固定首拍适配流程，移至 `episode/pipeline_preparation.py`；
它仍调用共用prepare服务，外部不再调用Agent工具的私有开拍/归档方法。
旧内部导入路径已更新，不添加兼容转发模块。

## 验证口径

沿用现有五章25段冻结请求/缓存/修复对照，迁移测试的内部导入和替代服务位置，保留断言。
只新增一项依赖回归，阻止共用渲染/探针/归档重新导入Agent。
逐步回归：共享能力126+31；工具522；输入修复155；规划170+44；生命周期132。
补充核对规划、Agent生命周期和工具中的71处非文档长字符串（至少60字符），前后相同；
这只证明这些常量没有改写，不代替实际请求冻结和执行回归。

最终完整回归：**2901 passed，67.02秒**。普通批量和Agent入口help均正常。
冻结夹具文件未修改；模型请求、缓存和修复行为由保留的回归验证。
测试模拟模型与供应商，固定素材合成使用真实FFmpeg；
本轮不调用真实图片、视频或文本生成服务，不启动生产或重启服务，不push。

## 提交与合回

五项分别提交并合回main：

| 内容 | 提交 |
|---|---|
| 共用探针与生产产物职责 | efcc39b |
| 工具服务组合 | 68e1cc4 |
| 输入候选、编译与发布交接 | a44619f |
| 规划阶段与内存交接 | c14b30f |
| Agent生命周期、启动和模型回合 | 089e324 |

合回后保留其他会话的在途工作板草稿；不恢复诸天或光明的任务，不push。
第一批交付时剩余10项；第二批后的当前状态见下文。

## 第二批：6–12

实施分支 `codex/production-structure-6-12-20261008`，基线main `8022617`。
渲染和Context配置/状态/结果契约有直接依赖，放在同一整合提交；人物库、字段和定位分别提交。

- 渲染：`rendering/flow.py` 711→323行，实际规则放在requests、cached_takes、submission、
  clip_analysis，setup负责构造本集上下文。外部命令和ThinMediaRunner执行入口保持。
- 人物库：review/bible.py 690→68行，reading_roster是读书事实读取，entity_extraction负责抽取，
  character_design负责设计，bible_growth负责增量登记写回，volume_context负责地点光线/卷摘要。
- 字段：story/fields和field_validation共管共用语义。Agent草稿、源级补丁、规划Schema消费同一份；
  各自已有的必填、空值、附加键和数量规则保留，模型Schema与提示词未改策略。
- 有效配置：resolved_settings组装本集profile/frame/genre/style、原计划限制和Agent环境；
  configuration继续负责批量资源与配置源读取。环境仍是进程边界的传递方式。
- 状态：RenderContext保存本集资料/服务，RenderOptions保存本次选项，RenderState保存缓存、预算、
  技术目标与每段ClipRuntime。_generated/_softened等不再写进片段输入。现存报告/缓存格式不变。
- 结果：media/results明确首拍部分selected与完整ASR的区别；review/acceptance给出技术与内容结果。
  review/execution写回原quality_review和episode_execution；Agent与交付共用选中视频规则。
  缺chunks仍走当前take的原analyser，不能默认为空字幕；合法空数组保持有效。
- 定位：episode_paths共读普通书和有profile的嵌套Agent书，工作台可打开12-2等分集。
  嵌套工作区以输出目录相对路径的冒号地址进入原书接口，不迁移数据或新增持久化索引。
  build_voices_thin --source-dir可重复指定已有Agent集合；相同take/时间段只收一次，输出仍在novel-dir。
  修复范围的语音策略按原章节覆盖所有分集。交付的内部plan索引按完整集名，避免两部分互相覆盖。

验证：5个定位回归，完整**2906 passed / 60.97秒**；收尾边界23项、导入整理74项通过。
16种缓存/生成/拒绝/重试事件与原clip_retry_before冻结记录一致，未更新该夹具。
原五章25段等冻结夹具和configs均未修改；人物库6处长请求文本前后一致。
运行目录验证另发现本地.env污染模拟端点、健康检查测试；测试改为显式模拟端点与临时配置根，
原断言保留，58项相关回归通过。生产配置、配置优先级和健康检查的提示口径未改。
没有真实图片/视频/文本模型调用，没有重启服务、启动生产、清零预算、移动资产或push。
当前剩余为13–15三项收尾，不将所有潜在未来改造都计成新增债。

第二批代码提交：

| 内容 | 提交 |
|---|---|
| 普通/分集/Agent产物发现与声线来源 | 5155ddb |
| 读书事实、实体抽取、人物设计与增量写回 | 14980b9 |
| 共用字段与值校验 | 7efcdc1 |
| 渲染服务、有效配置、运行状态与验收交接 | 90c6da7 |
