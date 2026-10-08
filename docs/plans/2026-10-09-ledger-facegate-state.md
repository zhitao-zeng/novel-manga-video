# 台账写锁、settle索引与Agent诊断记录（2026-10-09更新）

基线main：9bfc403。实施分支：codex/ledger-state-20261009，独立工作树：
`/mnt/disk1/zengzhitao/novel/worktrees/ledger-state-20261009`。
使用项目现有Python环境：`/mnt/disk1/zengzhitao/novel/novel-manga-video/.venv/bin/python`。
旧episode-agent树仅作为计划来源，其他会话未提交原稿和在产运行快照保留，不从旧树部署。

## 目标与边界

实施Part A与Part C。Part B已搁置，保留在[独立章节](2026-10-09-facegate-deferred.md)，本轮不安装人脸依赖、不加载/下载模型、不启用人脸门。
维持台账claims/证据/corrections语义、身份缓存输入列表、模型提示词和裁决规则。
不减账，不改演员设计，不建卡、不生成或重拍视频，不启动、停止或重载其他会话任务。
A3是用户授权的文字裁决与生产台账操作，须在A1/A2验证后，核对实际写入者、备份并对照证据后执行。
当前基线：光明纪元323条claims，296条pending；每次真实操作前重新读取，不把这个数字当固定验收值。

## Part A1：完整的台账读改写锁

- ledger_store提供`ledger_lock(novel_dir)`，用同一entity/ledger.lock和fcntl独占锁。
- 锁必须在加载待修改Ledger之前取得，在保存及所需索引写回之后释放。
- CLI的接受/拒绝/撤销/dedup/settle/index按入口持锁；extract/resolve的锁由ledger_flow拥有，不嵌套重复获取。
- 逐章读书的提取保留并行；每次顺序落账持锁并重新加载Ledger，摘要、人物库增长和卷总结留锁外。
  收尾重新加载最新台账后关联卡并保存索引，不能用开工时的旧对象覆盖后续写入。
- setup的名单播种及reconcile --fix也在同一锁内重新计算缺口。只读查询不额外取写锁。
- 必要的台账归属判断模型调用仍处于本次台账修改范围，不引入乐观事务、版本哈希或新队列。
- 用两个同时启动的进程验证“锁内加载→新增→保存”都保留，覆盖正常退出和异常释放；不使用顺序启动冒充并发。

## Part A2：证据数据一次加载，按canonical实体索引

- Ledger加载每章mentions与relations一次，保留原文件顺序，构建内存字典。
- entity_chapters按当前canonical归集，所有已合并实体的旧章节归入幸存实体；不能只并入两端原ID及canonical两个索引。
- evidence_pack支持显式章节列表；mentions循环只遍历相关章，relations沿原章节/行顺序从内存读取。
- resolve_chapter每次写mentions/relations后同步内存；保留原证据截断、limit、共现章、关系最多8条及提示词全文。
- settle建一次实体索引，在裁决前构造完整证据包，裁决和合并顺序不变。
- 对照旧算法验证普通/已合并别名/多级合并/共现/关系与当场新落账；构造Ledger后不再读mentions或relations文件。
- 索引速度与模型耗时分开计；总时长超过30分钟不自动认定索引失败。

## Part A3：光明纪元存量settle

1. 核对本书真实entity写入者和在途代码版本。正在运行但只操作隔离快照的Agent不等于共享台账写入者；不擅自停止其他任务。
2. 只读比较当前全部待裁决对的新旧证据包。文字必须逐字一致，记录加载/构包耗时与文件读取数量。
3. 在同一写锁内重新加载、备份entity目录及已有派生索引，记录owner、基线计数和运行代码版本。
4. 使用既有判官、提示词和裁决流程，保留变身/乔装的人工边界。模型请求异常不能作为“反证”写成rejected；本轮应在写回前中止并保留原台账。
5. 执行后重建原索引，报告accepted/rejected/closed/pending及合并变化；留存裁决原句与人工项，不要求强制归零。
6. 抽查新接受合并的原文证据。原人物库/图片/视频/预算不改，不将台账改好当作成片已通过。
7. 若实际共享写入者仍在使用未加锁代码，先等待该写入窗口结束，不能靠新锁保护不遵守它的旧进程。

## Part B：搁置

独立草稿保留；校准分布不可分就止损的判断保留。以后重新启用需单独重新核接口、标签与坐标解码，不夹带在A/C中。

## Part C1：只做诊断投影，不建立第二份状态真相

文件名episode_state.jsonl，统一写在Agent的ctx.run_dir；共享审查/发布函数仍只处理章节目录和业务结果，不反向依赖Agent。
record(run_dir,stage,result='observed',**detail)追加小型JSON事件，记录真实状态迁移；写入失败不影响原生产动作，不虚报验收通过。

实际挂钩：

| 位置 | 事件与时点 |
|---|---|
| episode/tool_state.py::record_state | 原execution.json写成功后的operation事件 |
| episode/bridge.py::update_state | 原agent-run.json写成功后，仅状态/阶段/任务标识有变化时追加job或lifecycle事件 |
| episode/media_operations.py::review | 宿主实际完成审查/报告写回后追加review_saved |
| episode/completion.py::finish | 原验收写回后记录验收；真正publish_if_ready成功后记录published；最终回执完成后沿原record_state记录收尾 |

lifecycle.record_finish只改内存，不向其中塞文件I/O。只读status/inspect不追加事件。
保留现有报告和写入顺序、运行UUID、暂停和任务回执，记录不承担调度、裁决、发布或预算。

## Part C2：看门狗读附加活动证据

- execution.json/agent-run.json仍决定当前PID、阶段与结束/暂停状态。
- 活动时间=max(现有两个文件活动时间,最后有效投影ts)；旧投影不能压过更新状态。
- 只读文件尾部，末行不完整时读取前一有效行；没有可用投影时保留原逻辑。
- 告警增加last_stage作诊断，仍仅对“超时且当前宿主进程已退出”报警。
- 不宣称能检测活着但卡住的进程，不杀任务、不自动恢复，不把等待GPU/模型槽判成故障。

## 验证与提交

先各组相关回归，再完整保留测试。模型用模拟返回，媒体沿固定FFmpeg素材，无真实视频生成。
关键验证：真实并发台账写入、旧对象不得覆盖、合并别名证据完整、缓存及时同步、旧算法逐字对照、构包不读两类文件；
真实宿主挂钩写同一run目录、失败不伪造published、只读不记事件、旧投影不压新状态、终态与活PID豁免保持。
按“更新计划/台账锁/证据索引/诊断投影/真实清账记录”分步提交并合回main，不push；在途板只关闭本会话。
生产数据的备份和裁决产物不进Git，提交通用代码/测试/经验；其他会话原稿及运行快照不改。

## 回退

代码按提交回退；diagnostic JSONL删除不改变权威记录，只有附加last_stage证据消失。
清账回退必须在确认无共享写入者且持同一锁时恢复entity与派生索引备份，不用git回退代替生产数据恢复。
