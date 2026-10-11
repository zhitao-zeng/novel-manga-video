# 在途工作板(多会话协作的唯一事实来源)

规则见 AGENTS.md「多会话协作」。每个会话开工登记、收工销账。
格式:`| 会话/负责人 | 干什么 | 分支/worktree | 动的文件范围 | 开工日期 | 状态 |`

## 当前代码核对（2026-10-09）

main已包含Agent产线（8a4a12c）、配置/缓存/Phanthy（704da0f）、恢复/ASR（71f6c67）和固定15项结构整理（9bfc403）。
当前main的未提交内容只有本工作板草稿。下方旧登记中的“未合并”“主工作区有10+源码改动”是原时点记录，不能当作当前main状态。
其他会话的未提交原稿及运行快照仍留原目录；代码合入不等于对应生产任务收工，本会话不代其销账或清理。

## 在途

| 会话 | 干什么 | 分支/worktree | 范围 | 开工 | 状态 |
|---|---|---|---|---|---|
| codex（身份资料统一） | 光明冻结A/B、证据检索与章节资料共用、统一采用和按需查证，保留独立覆盖与资产检查 | codex/identity-unification-20261009 | identity、reading/lean、规划与审修交接（review/evidence快照）、冻结评测及文档；不切在产身份源 | 2026-10-09 | 本轮e2a4252修已登记新名仍返回UNKNOWN及无卡目录漏项；f888926同步main。5db478f：v11按当前绑定补取真实档案，复核/修订共享证据；107已不绑天虎而用29章老人e026，但108仍错绑壮汉(e067→应e036)，115无效patch、244在场核对失败，真实失败留档。全量3264通过，新增结构修订证据回归及相关71项通过，独立工作树干净；目标未完成，全300/光明历史/性能待验。未合回main/push/正式迁移/媒体生成或启动生产。 |
| codex（身份自动闭环） | 读书落账与章节绑定自动核对、一次修订及复核；临时主体接规划、明确新名接增长；失败章不建档 | codex/identity-auto-closure-20261009 → main | identity/planning/story共用入口、13项闭环回归、冻结元数据与说明 | 2026-10-09 | 已完成；6373b36已合main，工作树全量通过、main相关44项通过；真实文字3类成功，复杂群体仍失败并止于写回前；本工作板草稿未提交，未push/重启生产/画卡或重拍 |
| kimi(本会话) | 十日终焉接入+验收(分镜300/300待ch221,渲染2-11待额度) | main 直接改动(历史原因)+ 已过审 | outputs/shiri、configs/pipeline.json 的 shiri 段 | 2026-09-28 | 进行中 |
| kimi(本会话) | 光明纪元资产盘点:声线库已建(91人),圣经分诊表已出,最小档修正因子代理被杀(额度)中断 | main | outputs/guangming-ds-100 | 2026-10-08 | 待续 |
| kimi(本会话) | 光明剩余80章接续:压缩修复已合main(00d60a6)、703续跑中、800规划DS路由+评审追问修复(e981f76) | main 直接改动 + 既有 run_dir | scripts/plan_chapter_thin 703/800、.runtime/guangming-agent-autoclosure-20261010/continuation | 2026-10-10 | 进行中;不重置计数/不碰其他会话WIP |
| 未知会话 | 主工作区有未提交改动(pipeline.json/cards.py/bible.py/asset_builder.py 等 10+ 文件),导致 tests/test_production_runtime.py::test_locked_series_asset_survives_prompt_revision_without_image_call 在工作区红(干净 HEAD 上绿) | 未登记 | src/novel_manga/application/{assets,review}/ 等 | 2026-10-08 | ⚠ 未登记在途,认领请补登记 |
| codex(历史会话) | episode-agent 整集制作线 | worktrees/episode-agent + episode-agent-minimal + episode-scheduling | application/agents/episode/ 等 | 2026-10-02 | 在产(光明纪元),未合并 |
| codex(历史会话) | LLM 缓存顺序工作 | episode-agent worktree 内 | scripts/llm_cache_order_*、docs/superpowers/plans/ | 2026-10-08 | 状态未知 |
| codex(本会话，光明剩余恢复) | 用户批准：发布ASR已修4章、处理6项回归、整合运行代码、85优先/48后续及37分诊；文字恢复与拍摄额度分开 | codex/guangming-resume-20261008（独立整合worktree） | 既有Agent/缓存/Phanthy/ASR修正，6项回归的最小修正；.runtime/guangming-recovery-20261008及本书已验收输出 | 2026-10-08 | 进行中；保留未知会话在main/episode-agent的未提交原稿，不reset、不覆盖、不代为提交其未完成功能；整合经验证的已授权修正 |


| codex（遗留改动分组） | 本次合并保留：main本地pipeline/agent_phanthy配置；episode-agent的LLM缓存、Phanthy、恢复/ASR等其他会话WIP | 原main与worktrees/episode-agent；恢复整合在codex/guangming-resume-20261008 | configs、llm、providers、恢复/ASR及对应测试；不混入本轮提交 | 2026-10-08 | 配置、LLM缓存与Phanthy已合入main（704da0f）；恢复/ASR已合入main（71f6c67）；其他未提交原稿留原工作树，生产任务未启动 |





| codex（修复经验摘要） | 整理85集里154条案例引用，核验31个原始案例，修正有效修法注入 | codex/repair-memory-summary-20261008 | agents/episode/recovery.py、current_task.py、memory_summary.py、相关回归与经验文档；.runtime/guangming-recovery-20261008内审计产物 | 2026-10-08 | 摘要完成：31例/10类经验，原memory不覆盖；61项运行快照回归及85任务离线回放通过。新启动切片已指向9ee90f1+memory修正快照，未重启在跑宿主。episode-production技能已增加修复手册并在真实沙箱安装，86项相关回归通过；12集5文字并发恢复正在运行（guangming-memory-rerun-12-20261009），保留原时间/视频次数。后续定位并修正图片URL行内注释解析与阶段人物卡API路由，已部署到同一运行快照；原预算保留，真实新出图效果待验。源分支未提交/未回合/未push，原整合树保留 |

| codex（光明继续修复20261009） | 用户要求继续：配置失败的原候选恢复、QA回执诊断、28已验收集归档及剩余批次 | codex/repair-memory-summary-20261008 | input_records/input_repair/recovery及相关回归；运行快照与本书原run_dir，主目录仅已验收分集归档；不写共享book entity/claims/types | 2026-10-09 | 本轮新增8章正式通过，662/800、剩138；23集已归档、11集因共享参考版本冲突保留工作区。配置6/已有审核11/内容7批已结束；代码9bf1ed4已提交、main baa839d已集成，合并2946测试绿、独立复核无重要问题，未push；GPU81 H3已恢复。532依赖额度/704姿态名单和其余138继续待处理；未写共享entity，原计数保留 |

| codex（光明收尾恢复5集） | 继续已授权恢复：466/545/683/745/780仅已有视频审核与正式验收，保留实际拍摄计数 | codex/repair-memory-summary-20261008；原运行快照 | .runtime/guangming-recovery-20261008/acceptance-recovery-5-20261009及上述原run_dir；不改共享身份台账、输入或拍摄额度 | 2026-10-09 | 已结束：5/5正式通过、实际拍摄数不变；745已镜像主目录，466/545/683/780因共享参考图版本不同保留原工作区。全书667通过、剩133；未改共享身份台账、输入或拍摄额度 |

| codex（执行剩余133方案） | 用户批准方案：审核/首拍交接、姿态协议与骨架证据/场次权限、片尾黑帧计量、按原因有限恢复；125原文恢复 | codex/repair-memory-summary-20261008（baa839d基底） | packing/story posture、agents/episode input_contracts/input_records/input_compilation/media_operations及测试/技能；本书原run_dir与.runtime恢复池，125源文件/章节偏移；不写共享entity/claims/types | 2026-10-09 | 源码a188e5d及后续小修，最终回归与复核中；532真实读取不再假联动，704协议通过后真实地点承接仍需Agent修；125正文213→2183字，其他799章内容核验不变。491/762已有视频复核仍未通过。后续全133任务有限入池，不重置视频预算 |

| codex（Phanthy少量审修） | 用户追加glm-5.3-flash并行，1文字槽，125/703/800；普通规划/绑定/翻译仍稳定DS/Qwen，Phanthy负责Agent审修 | codex/repair-memory-summary-20261008；main ce8ea48 | 私有运行配置/有限任务池；API Key仅从既有私密文件读入进程，无源码或日志 | 2026-10-09 | models、Chat和模型工具发起→真实读进度→结果回传→最终回答验证均通过；GLM普通身份绑定8000输出截断，125/703已按剩余时钟准备修正路由续接，等待原Phanthy任务结束，不叠加第二文字槽；其他恢复池继续运行 |

接手登记（2026-10-10）：codex 已按用户要求接手 GPU16 旧“短剧”会话 `01a07b12-cf78-78e2-bcec-c116edc21b20` 的身份链路收尾，沿用 `codex/identity-unification-20261009` / `worktrees/identity-unification-20261009`。范围为该树 identity/story、相关测试及实施记录；保留原WIP与其他会话工作，预计1–2小时。HEAD核对为8c516df，先查3275通过/1失败及v13四章已有回放。仅代码与验证，不提交/推送/合并/生产切换；详细进展与收工记录在该工作树 `docs/active-sessions.md`。

本次接手收工：上述工作树HEAD仍8c516df，全部WIP未提交。原失败夹具接手前已修，当前补齐按范围重开新反证、语义疑点分责与失败上下文，3281全量通过。最终v16四章实验准备完成、原编号/卡/旧裁决保留；原文验收仍未过（107白虎、108壮汉/人数、115两车数量、244医生/初七）。完整结果见工作树实施记录及 `.runtime/identity-unification-20261009/takeover-scoped-routing-v16-20261010/verification-audit.json`。全300/光明历史/性能及独立审阅未验，不切生产、无媒体生成/推送/合并，其他会话工作保留。

续接（用户本会话已确认）：同一身份工作树正在闭环108/115数量、107/108身份、244查证，预计1–2小时，最后一次独立代码审阅。其他会话工作保留，本次仍不提交/推送/合并/切生产。

用户追加授权（2026-10-10）：同一Phanthy网关并发有效限额由1提高到2；只改configs/pipeline.json中旧phanthy条目的slots 1→2，其他条目、既有WIP与其他会话进程保留。新的身份私有验证读取2槽继续；不切生产身份源、不重启模型或其他任务。

## 已收工(留档备查)

| 会话 | 干了什么 | 分支 | 结局 |
|---|---|---|---|
| authored-split 会话 | authored 拆镜 keep_authored 修复 | fix/authored-split-keep-authored | 已合并(09-29,c74effc) |
| tier-decouple 会话 | tier 拆四独立键 | refactor/tier-decouple | 已合并(09-30,04be7fe) |
| prompt-chain 会话 | H3 提示词起草统一 | main | 已提交(1f26570) |
| 整合会话 | H3 544P/穿戴核对/时长压缩/配额停机 | main | 已提交(5e934cb) |
| codex（人物证据与资产修复） | 完整Agent产线＋人物证据/资产/卡审/快照修复，2841项整合测试通过 | main（8a4a12c；整合树codex/merge-episode-agent-20261008） | 已提交并合回；未push、未恢复生产；其他配置/缓存/恢复WIP保留原处 |
| codex（配置/缓存/Phanthy整合） | 本地公开配置、LLM prefix-cache脚本/回归、Phanthy端点与沙箱传输接入；完整2852项测试通过 | main（704da0f） | 已提交并合回；私有凭据不入Git，未push、未重启模型、未启动恢复任务 |
| codex（光明恢复线回合） | ASR合成交接、实际take验收、提交前探针、doctor工具和看门狗修正；完整2900项测试通过 | main（71f6c67；整合树codex/merge-guangming-recovery-20261008） | 已提交并合回；未push、未启动生产；原恢复树及其他会话未提交草稿保留 |
| codex（结构债1–5） | 规划、Agent生命周期、工具、输入修复与共用能力拆分；5项独立代码提交，完整2901项回归通过 | main（089e324；记录见structure-debt-20261008.md；整合树codex/production-structure-20261008） | 已合回；未push、未启动生产；其余10项留固定清单，其他会话原稿保留 |
| codex（结构债6–12） | 渲染/人物库拆分、共用字段、有效配置、独立运行状态、媒体验收契约与普通/分集/Agent产物定位；独立树2906项及main最终2906项回归通过 | main（db6fab4；4项代码提交、说明与测试隔离提交；独立树codex/production-structure-6-12-20261008） | 2026-10-09已合回；未push、未启动生产，缓存/预算/资产保留；剩余13–15，其他会话原稿保留 |
| codex（结构债13–15收尾） | 9个命令实现归入application、9个根级模块归位、补齐看板分集统计、架构/全景/映射/测试说明同步；独立树2906项/main2906项通过，冻结夹具和configs不变 | main（9bfc403；67ff348/7f6d20e/eba1938/9bfc403；独立树codex/production-structure-finish-20261009） | 2026-10-09已提交合回；固定15项闭合，未push、未启动或重载服务、未调用真实模型；其他会话原稿和本工作板草稿保留，下一步按实际业务问题推进 |
| codex（台账锁/settle/状态投影） | A/C已完成：跨进程写锁、canonical证据索引、诊断事件和看门狗；独立树及main2926项通过。光明231对证据一致，构包25.828→0.965秒；548次既有FlashNext裁决后复核纠正3误并及林齐代表方向，pending296→0 | main（5b6f587；6e70faa/a882992/6ef126c/d6c5db1/87d60a8/5b6f587；codex/ledger-state-20261009） | 2026-10-09已提交合回，B搁置；无生图/视频生成、服务重载、push。数据备份和审计在.runtime/guangming-ledger-settle-20261009，有owner。原有17名单缺口不盲播；通用身份语义/二字名问题待用户追加范围，不夹带。本工作板草稿与其他会话原稿保留 |
| codex（身份正确性七项） | 四批完成：身份语义/正名、异常不缓存与复审/撤销、来源约束对账和光明旧身份核对、按章缩锁；独立树及main各2971项通过 | main（44629d6；f084bef/a2d5883/d0be537/b790781；codex/identity-correctness-20261009） | 2026-10-09已提交合回，未push、未生图/视频生成、未重启或替换他会话快照；光明撤销6旧误并、恢复9代表、补3无名记录和1别名。11关系/13名单项仍待原文归属；原有4人工修正与595编号/卡关联保留。备份/74次文字核对在.runtime/identity-correctness-20261009；说明docs/identity-correctness-20261009.md。本工作板草稿原样保留未提交 |
| codex（台账索引收尾） | 权威保存统一同步两索引、纯内存派生、旧索引命令服从现有台账、初始化/对账共用证据约束；独立树及main各2981项通过 | main（88cfd5a；8929af7；codex/ledger-index-finalize-20261009） | 2026-10-09已提交合回；572在用实体/799章索引与当前台账逐项一致，11关系/13名单保留待确认。未push、未调用真实模型、生图/视频、重启或替换他会话快照；主目录仅共享工作板草稿保留未提交。说明docs/identity-correctness-20261009.md，运行核对留原index-resync.json |
| codex（台账遗留分诊） | 11关系分诊：10撤销错误候选、1正名归属；13孤立名册：12章节描述/群体/身体/声源、莲移来源追踪；补5临时个体并纠正神灵群体类型；共享解析/生产catalog接入已确证归属 | main（0b33945；0b9996a；codex/ledger-triage-20261009） | 2026-10-09已提交合回；独立树及main各3012项通过，326判断接受78/拒绝248/pending0，576在用实体两索引一致、正式名册未匹配0。29次文字请求及原文复核，备份/候选/采用在.runtime/ledger-triage-20261009；598原编号/姓名/有效卡关联及12既有人工作证保留，未知莲保来源、不假确证。未push、未生图/视频/改运行快照；其他工作板草稿保留未提交 |

| codex（GPU81四卡改H3、文字收敛DS） | 用户授权停GPU81 Qwen，4–7常驻H3，光明当前任务池文字/视觉检查改DS | codex/repair-memory-summary-20261008；私有运行配置 | .runtime/guangming-recovery-20261008内路由/任务池；GPU81指定Qwen容器与新H3实例；原影片/额度不重置 | 2026-10-09 | 已完成：GPU81指定Qwen停止、4–7常驻H3:30022，544P真实5秒测试25.03秒且全帧/音轨解码通过，已接本书15秒生产；133条任务全部DS8，原20运行宿主按回执迁移、实际take计数未减少，原停止任务未统一重开。配置与验证在.runtime/guangming-recovery-20261008/gpu81-h3-ds-20261009；无业务源码提交/push |

| codex（实例负荷监控计划） | 用户要求H3/LLM长期负荷、产量和闲置自动补入实施计划 | codex/instance-utilization-plan-20261009 | 仅docs/superpowers/specs与plans；只读现有监控和调度入口，不改在产服务/代码 | 2026-10-09 | 1–3已完成上线：独立只读10秒采样、15/60分钟逐实例统计、原18900看板加入光明；真实采样22分钟以上，15分窗口积满。3037全量通过，独立审查3项Important均RED→GREEN修复，0 Minor。自动补入未做；代码未提交/合回/push，服务从本worktree加载；证据.runtime/guangming-recovery-20261008/instance-monitor-20261009，原调度/额度/模型服务未改 |

| codex（共享实例监控） | 用户确认：采集/清单/历史移至项目共享层，单书仅归属视图；保留已有数据 | codex/instance-utilization-plan-20261009；6373b36基底 | instance_observer/history、dashboard配置与展示及回归；共享监控私有配置和产物；不改调度/额度 | 2026-10-09 | 已完成：共享清单/单采集器/统一历史，原371采样1433任务保留，5书只读归属；18900顶部共享实例，15/60窗口均积满。GLM无metrics明确未知；3063全量绿，独立审查3Important已RED→GREEN修复，0Minor。代码未提交/合回/push，服务仍读本worktree；证据.runtime/guangming-recovery-20261008/instance-monitor-shared-20261009；自动补入关闭 |

| codex（审计交接复核） | 核对另会话124章审计与96目标，完成已有质检通过452/454标准收尾 | 原运行快照code-c8acddb-recovery；无业务代码改动 | .runtime/guangming-audit-followup-20261009、452/454原run_dir、452主目录与全局统计；原审计报告不改 | 2026-10-09 | 已完成：26已过章本来不在96，731新风险决定实际执行、744该轮仅finish，吞提交两例定责需撤回；2章正式通过且拍摄次数不变，452发布、454因参考版本不同留工作区。706通过/94未过，674主目录/32待发布；证据handoff-review.md，未改业务代码或额度，未提交/push |

| codex（审计宿主修复） | 共性权限/回执/ASR、资产闭环、来源额度与有限恢复已实施 | codex/audit-host-recovery-20261009 | 原run_dir与私有完整快照code-final；3100源码/62实际快照测试绿 | 2026-10-09 | 已合main 2fa6de0，无push；一次独立审查7项Important均修；真实481零重拍正式验收，全书710/90，主676/待发34；17资产任务6并发+2长计划原余额续接在产，observer有限收口，自动补入关闭；其他草稿保留 |

| codex（上游与耗尽恢复设计） | 设计/计划已核定并Native实施，最终一次独立审查完成 | codex/audit-host-recovery-20261009 | docs/superpowers/{specs,plans}/2026-10-09-upstream-recovery*；原次数保留 | 2026-10-09 | 五任务源码完成并部署；20集有具体新入口，首批481正式通过，456/614重打包翻译后续接标准生成；其余71无新恢复依据保留分诊；600/1200一次窗口、两提案，第二次额外拍摄不自动授予 |

| codex（DS断连续接） | 旧批35连接拒绝按原余额有限续接 | 原运行快照；codex/audit-host-recovery-20261009 | .runtime/guangming-audit-host-recovery-20261009/ds-outage-resume-20261009及原run_dir | 2026-10-09 | 已收口1通过[483]/34挂起；DS实际Anthropic回答已验证，GPU52未由本会话重启；运行/计数/旧结果保留；后续已切新的上游有限批 |

| codex（恢复覆盖补全） | 检查/执行目标一致、部分修复、全长范围、全量任务分诊及真实通道补丁 | codex/audit-host-recovery-20261009 | main9ec1da9；独立code/code-speaker-fix/code-final快照，旧在产版不改 | 2026-10-09 | 已合main无push；3121源码/最终55运行包契约绿；89任务全部入池8并发，原目录/次数/剩余时间；新发现零数量人群剔除说话人与video额度拦input编译均修，符合证据者各有限续一次；当前全书712/88，仅正式门通过计数；其他草稿保留 |

| codex（Agent全量解堵） | 身份增量修订、出场/提及明确、无用分类null、相关来源裁剪已落地；源码3155/快照44项绿 | maincfc756b；codex/audit-host-recovery-20261009 | .runtime/guangming-agent-autoclosure-20261010；原run/次数/预算保留 | 2026-10-10 | 活跃目标：720/80；703身份审已实际通过但后续规划仍阻；800该轮已终止需查新回执；其他80章失败及耗尽恢复未闭环，下一步读当前原因；无push，其他草稿保留 |

## 2026-10-10 追加（ZCode 接续会话）

| codex（光明续接，ZCode） | 接手 audit-host-recovery 交接：703 压缩修复已提交(00d60a6)+同步 code-sparse 快照；发现另一 codex 单元已在跑 703-compression（pid 1888217，用旧 upstream-recovery 快照，无此修复），已避让并清理自己的重复 inventory 防双派 | codex/audit-host-recovery-20261009 | planning/compression.py+tests、.runtime/guangming-agent-autoclosure-20261010 | 2026-10-10 | 进行中 |
| codex（Phanthy 新预设） | 接入两个无费率 ID：phanthy-dsv4 / phanthy-phancy（实测透传可用、零扣分、实际服务 kimi-k2.6 reasoning 型，短预算会空 content） | main（95e37d7） | llm/config.py、configs/pipeline.json | 2026-10-10 | 已提交；slot=2 保守；未切生产路由，全 API 化待吞吐基准与费用对账 |

用户后续明确要求“都写成8”：同一127.0.0.1:8789网关的phanthy/phanthy-dsv4/phanthy-phancy/phanthy-ds41四条slots统一为8，实际共享上限8；其他地址限额及其他会话未提交改动保持。只重新加载本次私有验证进程，不重启模型或其他任务。
| codex（reasoning 型适配+221 试点） | 确认 Phanthy 全模型族不可关 thinking；按实测成功率(luna 2/3、ds41 1/3、glm 0/2)在 generate_outline 写入模型轮换重试；semantic_review/plan_patch/compression 预算按 reasoning 型重定；client 截断上限 8000→16000+空内容重置重发；发现并修复 worktree 无 .env 导致全部网关调用 401 的根因（.env 符号链接） | codex/audit-host-recovery-20261009（101e50c）+ main（615b1fa） | planning/requests.py、identity/semantic_review.py、planning/compression.py、llm/config.py+client.py、worktree/.env 链接 | 2026-10-10 | 进行中：221 第八次试点跑轮换+新预算，等 clip_plan |


身份接手最终交接（2026-10-10）：codex/identity-unification-20261009仍HEAD8c516df，原WIP与本轮修改未提交，不推送/合并/切身份生产源。全量3301通过；只读两门/对应处理器、采用范围与数量保真、流式原生JSON/65536输出/600秒/真正读取中断、局部观察记录及跨门协议检查已落盘，独立代码审阅已收口。107白虎e026、108张山/壮汉e036及十人、115地马/许流年及两车的聚焦真实证据通过；244仍声音/屏幕覆盖失败、调查300秒无提案，未计验收。原165实体/姓名/卡关联、旧claim和裁决保留；107/115部分证据为较早版本，已完成真实回复有复用，非四章全部最终代码从头实时重读、非媒体或全书验收。详情见工作树实施记录最终交接和.runtime/identity-unification-20261009/four-chapter-source-acceptance-20261010/final-handoff.json。共享新请求容量实测8，无更小运行limit；其他会话资源和未提交修改保留。244后续须对应场景处理闭环并复核身份采用。

244续修登记（2026-10-10）：用户明确“那就是还要修”，沿用identity-unification工作树及未提交修改。当前具体问题：第二场景复核新增声音/物件问题后尚未交处理器修正就进入调查；修正有界场景处理后重放并核身份采用，保留3301基线、前三章事实，仍不提交/推送/合并/切生产。

身份续修进展（2026-10-11）：沿用codex/identity-unification-20261009，最新全量3316通过72.92秒。实际发现并修共同章节视图旧cast回流（女孩UNKNOWN仍附旧e051、医生旧e016与新e005并列），保留原台账/裁决，以已审核章结果供下游；逐字位置、章级参与范围及旧身体披露保留。场景修订处理器只返回报告主体的替换/新增，未涉及条目由宿主原样携带，继续原覆盖/语义检查。v22四章真实回执进行中，未计内容验收；主代码和其他会话WIP不动，不提交/推送/合并/切生产。详情在工作树板和实施记录。
| codex（221 全链试点收尾） | 第二十次全 API 路由通过：分场稿 passed(03:44)、posture→luna 生效、clip_plan 3 clips 落盘 | main+worktree | outputs/shiri/shiri_221 | 2026-10-11 | 全链试点完成：规划(提纲/分场/导演稿/posture)全走 Phanthy API，轮换重试实战救场 2 次（luna 接管） |

身份续修进展（2026-10-11，v28）：该工作树全量3323通过73.44秒；原观察与当前actor编号、局部排除继承/合法来源恢复、必需段号和下游旧cast修复已验证。107经作者漏项反馈、108/115较早真实版本聚焦通过；244同批群体及虚构人物语义仍做最后真实回放，未计整章验收。main实现代码与其他会话WIP未动；不提交/推送/合并/切身份生产源。详情在身份工作树实施记录/计划。

身份接手本轮收工（2026-10-11，v28）：工作树codex/identity-unification-20261009代码重构与本地验证完成，3323全量通过；244最后真实回执219.934秒、指定聚焦原文和归档下游独立通过。已有107/108/115聚焦证据保留，107/244有作者问题反馈与成功回复复用，非最终代码四章从头盲测。原165实体/卡、57claims、旧裁决保留，未提交/推送/合并/切生产，其他会话WIP未动；详情与未验证部分见工作树实施记录及runtime continuation-final-handoff.json。

用户追加最终代码四章自动重跑（2026-10-11）：身份worktree v28/441 Python源固定，107/108/115/244从完整原文重新抽取到场景/身份复核采用，无旧模型回复或人工问题反馈，每章一次完整自动流程。私有副本仅保留原档案/裁决及源segments，旧章身份/源/场景/lean答案不进入输入；四章并行、共享8，65536与既有时限保留。运行目录.runtime/identity-unification-20261009/four-chapter-source-acceptance-20261010/four-chapter-fresh-final-v28-20261011，预计最多60分钟；生产代码/别会话WIP不动，无媒体或Git发布。

| codex（光明76集正式验收接续） | 依用户2026-10-11指令核对身份协议收工状态、4并发推进已授权素材修复、800按稳定源顺序；599及shiri不动 | codex/guangming-acceptance-20261011；worktrees/guangming-acceptance-20261011 | 本会话runtime证据/驱动副本、原光明run_dir；不覆盖其他WIP、不重置计数额度 | 2026-10-11 | 进行中，预计随本轮有限批结束；identity-unification最新四章完整验证仍在途，446/473/648及800先挂起 |

最终v28四章完整自动验证收口（2026-10-11）：107/115/244流程准备完成414.210/333.931/543.393秒，108场景复核上游502在296.889秒失败。无历史模型回复复用/作者hook/人工问题或补跑；运行中代码未改。独立源文/归档下游核对107聚焦通过，115核心ID/数量通过但seg8包在mentions有而主体资料缺项，244医生UNKNOWN/extra被误放行（原16/21/22同事件可证e005），所以四章自动验收未过。165实体/卡、57claims、25输入裁决全保留；本轮只实验/文档，无提交/推送/合并/切生产/媒体。证据目录.runtime/identity-unification-20261009/four-chapter-source-acceptance-20261010/four-chapter-fresh-final-v28-20261011/final-report.json；通用检索/复核与片段范围仍待修。
