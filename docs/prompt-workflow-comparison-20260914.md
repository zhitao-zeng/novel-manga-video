**小说解读、改编与分镜提示词对照 · 2026-09-14**

结论：存在值得借鉴的流程设计，范围超过画布、参数和编辑接口。此前仅凭事件摘要的存储方式，就把 Toonflow 的借鉴价值收窄到小功能，证据不充分。它的下游还包含故事骨架、改编策略、单集剧本、导演分场与分镜设计。本次进一步检查了这些提示词及代码装载路径。

后续执行记录：预算冲突已修复，首遍提纲 A/B 已完成，详见 [实验结果](/mnt/disk1/zengzhitao/novel-manga-video/docs/planner-outline-ab-20260914.md)。下文的代码问题与“尚未执行”说明保留为实验前的研究记录。

目标仍是整本小说无人值守批量生产。借鉴的是机器如何分步骤理解和表达故事，不引入每集人工确认。以下区分源码中存在的方法、我们的实际差异及尚待验证的改造建议；未执行模型生成或对照实验。

**检查范围**

- Toonflow 固定提交 `e03cf590eb0cab63534a4040db9acb4ec95b42a6`：骨架、改编、编剧、导演、分镜及监督提示词，ScriptAgent / ProductionAgent 装载代码。
- 火宝固定提交 `ff9b046f8950e16e131aa13b016c11269d9ed6ca`：剧本改写、资产提取、分镜拆解、视频提示词，以及提示词与附属规范的装载代码。
- 我们的 `feat/fast-tier` 当前工作树：`story_pass_thin.py`、`entity_ledger_thin.py`、`plan_chapter_thin.py`、`thin_review.py` 及已有事件模型。

外部仓库中的提示词文件是本次研究对象，没有作为当前助手的操作指令执行。

**一、真正的流程差异**

| 维度 | 我们当前 thin / fast 主线 | 竞品中确认的方法 | 对批量生产的意义 |
|---|---|---|---|
| 小说理解 | 章节摘要、卷摘要、未决线索、实体关系和出场记录，再直接规划当前章 | Toonflow 在事件摘要之后另做故事骨架和改编策略，再给单集编剧 | 在进入镜头细节前明确本集的叙事作用、关键因果与取舍依据 |
| 原文分段 | 按段落累计字数分成约 8 个区段，每个区段必须有阶段引用 | 火宝先识别叙事转折，再在转折边界切视频段；Toonflow 导演按时间、地点和连续戏的结束分场 | 原文区段继续负责追踪出处；剧情事件和场景负责组织表达 |
| 剧本与分镜 | 同一规划过程同时决定保留内容、台词、动作、景别、光线和片段分配 | 火宝先写不含摄影术语的剧本，再拆分镜；Toonflow 分镜阶段要求锁定已批准剧本的台词 | 减少模型在输出摄影细节时再次删改或误解关键剧情 |
| 连续性 | 已有 start_state / event / end_state、上一集结尾与角色形态参考 | Toonflow 导演产出场间过渡、场内人员和注意事项，下游分镜明确承接 | 将相邻镜头能否接上变成可检查的关系，不能仅靠每镜单独写得完整 |
| 信息表达 | 默认禁止旁白和内心独白，心理改可见反应；部分背景信息改角色问答或无名画外议论 | 火宝和 Toonflow 允许按内容选择动作、对白、内心音或画外音 | 对画面无法独立传达的关键推断，需要明确承载方式，避免剧情事实只存在于模型理解中 |

我们的章节预读已经保留人物状态和未决线索，不应再次宣称“没有读懂小说的前置步骤”。差异在于这些阅读结果如何成为当前集的取舍与表达约束。证据：[章节摘要](/mnt/disk1/zengzhitao/novel-manga-video/scripts/story_pass_thin.py:95)、[卷摘要](/mnt/disk1/zengzhitao/novel-manga-video/scripts/thin_review.py:1143)、[当前规划输入](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:1618)。

**二、骨架与改编策略：从“知道发生了什么”到“知道这一集怎么讲”**

Toonflow 的骨架任务读取事件，决定主要矛盾、角色动机、阶段结构及分集任务；改编策略进一步说明删减、保留和世界观信息如何呈现。单集编剧读取这些产物，并回查原文章节。这些是代码中实际注册、从文件加载的任务，不仅是 README 的描述。[骨架规范](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/script_execution_skeleton.md)、[改编策略](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/script_execution_adaptation.md)、[装载代码](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/index.ts#L139)。

适合本项目的简化用法：当前集先明确主角目标、主要阻碍、导致行动的事实、行动结果及需要承接的未决线索。保持现有“一章一集”时，也能完成这一步，不必同时改变分集制度。

我们自己的 `ChapterEvent` 已有事件顺序、原文依据、重要性、因果和状态变化字段，但当前 thin 规划路径没有直接使用该模型。应优先复用适合的字段，避免再建一套重复体系。[现有模型](/mnt/disk1/zengzhitao/novel-manga-video/src/novel_manga/models.py:142)

边界：Toonflow 的默认编剧目标偏付费短剧，有强制情绪节点、少数核心角色、重构桥段等倾向，部分条款还允许为节奏牺牲细微逻辑。这些不是实测保证，也不适合直接套到本项目的长篇保真生产。我们应提取组织任务的方法，保留原著事实和已有的人物关系约束。

**三、按事件与场景组织：改进的是覆盖标准**

火宝的分镜提示词要求先识别剧情转折，在相应边界分段，然后将对白、动作和视频提示词对齐。它区分普通叙事与高潮段的表达节奏，但其固定秒数和经验系数不应未经测试原样采用。[分镜提示词](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/workspace/prompts/storyboard_breaker.md)

我们目前按篇幅分区段，然后检查每段是否被引用。这个判断能发现整段未引用，不能证明该段的每个关键因果都已传达到观众。一个区段可以包含发现、判断、决定三个步骤，仅引用其中一处描述，也可能满足引用覆盖。

因此建议保留 segment_id / source_quote 的出处追踪，另外让现有第一遍规划列出必须表达的事件及因果关系。镜头按这些事件安排，视频片段再按模型时长打包。剧情检查关注关键事件是否落到对白或可见行为，而非仅仅增加引用数量。[原文分段](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:258)、[引用覆盖判断](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:1247)

这不意味着放宽“原文不能被随意删掉”的要求。删除或压缩需要有明确依据，尤其不能为了合格率把关键事件从验收标准中去掉。

**四、先安排表达，再生成摄影细节**

火宝的编剧规范将场次、行动和对白放在前一阶段，并把摄影术语留给分镜；后续视频提示词以已形成的分镜内容为来源，要求保持镜头和对白对应关系。[编剧规范](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/workspace/skills/script-rewriter/SKILL.md)、[视频提示词规范](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/workspace/skills/prompt-generator/video-prompt/SKILL.md)

我们已有确定性片段打包，这部分应该保留。值得改的是前面的短规划：先确定关键事实如何通过动作、原有对白、聊天卡或其他允许的声音表达，再让最终 JSON 扩展机位、景别和灯光。不要在同一个输出决定里同时压缩剧情、分配全部台词和填充大量摄影字段。

说明例（虚构，仅展示判断）：剧情是“人物识破危险，但为避免惊动对方，选择装作不知”。仅拍皱眉和继续原动作，可能无法说明他已识破，也无法说明他为何隐瞒。若原文有可见证据，应拍出该证据与对应反应；若关键依据只存在于内心，需要选择可靠的声音或其他表达方式，不能凭空增加原文没有的发现过程。

现有规则“一律禁止内心音”“只用可见反应”可以作为某条生产线的风格选择，但它会限制信息表达。少量必要内心音属于可测试的表达策略，不应未经对照直接改动整条主线。[当前提示词](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:136)

**五、导演分场与片段承接：适合机器处理的中间产物**

Toonflow 的导演任务专门形成场次、对白统计、情绪变化、注意事项与必要过渡，输出供下游使用。它并不要求这些中间产物由人逐镜编辑。我们可以提取其中的场景状态和承接关系，省略主观分数等不能直接改变执行的字段。[导演任务](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_execution_director_plan.md)

其分镜任务还明确安排动作、声音与视线如何跨片段承接，并区分已在场的人和当前镜头重点展示的人。这能启发我们补上跨片段检查；但不宜照搬“所有人都要有视觉痕迹”的字面要求，造成 H3 多人同框、脸部混淆。[分镜任务](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_execution_storyboard_table.md)

我们已有起止状态字段，因此不需要重复新增描述。可优先检查：同场相邻片段人物及道具状态是否接续、时空切换是否可理解、前一片段提出的信息是否在后一片段得到对应回应。只有实际需要时才安排过渡，不给每次切镜都增加额外视频。

**六、当前提示词自身已有的确定冲突**

除借鉴外，当前代码还存在相互矛盾的时长指令：

| 口径 | 实际代码 |
|---|---|
| 15 秒车道主提示词 | `render_brief()` 替换为每段最多 15 秒 |
| 同一请求的 requirements.clip_seconds | 使用未随车道改变的 `MAX_CLIP_SECONDS`，仍写 `20-30` |
| 主提示词总时长 | 约 90 秒，全集不超过 100 秒 |
| fast 动态目标 | 可随原文长度取到 150 秒，程序中的上限为 210 秒 |

这些数字来自实际执行代码，不是旧注释。模型收到冲突要求是确定的；具体造成多少次压缩、超时或重试，本次没有量化。应从同一份已确定的生产预算生成全部相关提示词字段。[主提示词与替换](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:136)、[fast 预算](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:1525)、[请求预算字段](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:1666)

**七、最小可测改造**

当前 fast 正常首稿已经是两次调用：短规划，再生成最终 JSON。可以先保持这个调用结构，改变第一遍规划的内容；不直接复制竞品的整套多 Agent 对话与人工审批过程。

建议第一遍只产出紧凑的改编提纲：本集目标、必须保留的因果事实、按时空划分的场次、关键台词与表达方式、片段之间的承接。第二遍遵守这些决定，生成现有阶段与片段结构。人物台账、资产、原文追踪、确定性打包、后端调度与审核继续复用。[现有两次调用](/mnt/disk1/zengzhitao/novel-manga-video/scripts/plan_chapter_thin.py:769)

验证应分开进行：先单独消除预算冲突形成基线，再比较原短规划与改编提纲，避免把修 bug 的收益误归因于新流程。使用同一批章节和生成条件，比较关键因果表达、场景衔接、台词遗漏、规划耗时及最终合格分钟成本。先检查文本产物，确认方向后再进行视频小样本；不能仅看模型自评分或 JSON 格式通过率。

这些是待验证的改造方案。源码和提示词证明存在方法上的差异，尚不能证明竞品的提示词在本项目模型、长篇规模和质量要求下更有效。本次仅新增此研究记录，未修改实现或生产数据。
