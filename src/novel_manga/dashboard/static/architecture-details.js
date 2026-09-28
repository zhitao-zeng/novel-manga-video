/* Maintained architecture catalogue, not runtime task state. Paths are repository-relative. */
window.ARCHITECTURE_DETAILS = {
  source: {
    title:'原文、身份与资产上下文', note:'原文输入与身份资料的建立和读取。新书初始化、已有书的章节身份解析是不同入口，不会每次都重建整本人物库。',
    steps:[
      ['读取与切章','程序','读取小说，按章节、合章或既有分集记录选择本集原文。','原文文件 / parts.json','本集 source 与 segments.json','ingest.py · application/planning/parts.py'],
      ['新书资料初始化','模型 / 按需','新书建立人物、地点等初始资料；已有书继续使用其已保存资料。','原文、画风和画幅','story_bible.json 与小说资料','application/planning/initialize.py'],
      ['章节实体解析','模型 + 程序','依据当前章证据解析人物、别名、形态和出场方式，不只按名字包含关系合并。','章节原文、人物库、既有身份记录','本章身份与在场上下文','application/identity/flow.py'],
      ['未登记实体处理','模型 / 条件触发','对未绑定的实体作判断；需要建档的补入后重新读取上下文，区分临时实体与具名人物。','未匹配实体及其原文证据','补全的候选或未解决问题','application/review/bible.py · application/planning/cast_completion.py'],
      ['组装本集输入','程序','合并本章人物、道具、场景、前情和小说配置。前情用于连续性，不作为本集新增剧情。','身份上下文、profile、已保存前情','规划请求上下文','application/planning/context.py · application/planning/flow.py']
    ], branches:[['原文损坏或缺失','记录输入阻塞；重拍不能修复原文。'],['别名或身份存在歧义','继续保留证据和待处理问题，不能凭字符串相似度强制合并。']]
  },
  plan:{
    title:'规划内部：创作、检查与补丁', note:'以下是共同交接顺序。默认提纲路径、六种方法路径、沙箱分镜路径按配置选择，并不是每次串行跑三套编剧。',
    steps:[
      ['选择规划路径与预算','程序 / 配置','读取 planning_backend、story_method、片长、画幅与章节范围。','原文、人物上下文、小说配置','当前规划方式与预算','application/planning/flow.py · planning/budget.py'],
      ['提纲或分场创作','模型','默认路径生成覆盖提纲；方法路径先写分场剧本，再按选定方法导演拆镜。','完整本集原文、人物与创作要求','提纲，或分场剧本与导演结果','application/planning/requests.py · application/planning/method_pipeline.py'],
      ['形成结构化分镜','模型 + 程序','默认路径输出镜头字段；已采用的作者分镜保留其表达，只补允许修改的绑定字段。','提纲或采用的作者分镜','分镜草稿、人物/道具/对白字段','application/planning/storyboard.py · planning/binding.py'],
      ['结构与原文检查','程序','核对字段、引用、对白、覆盖与时长等现有约束；不把结构正确等同于故事好看。','分镜草稿、原文片段与预算','问题列表、规范化后的镜头','planning/validation.py · planning/source_checks.py'],
      ['人物在场与姿态检查','模型 / 按条件','用人物档案、原文和相邻镜头状态检查在场、坐站及动作衔接；按现有策略记录或进入修订。','完整分镜及邻镜状态、人物档案','在场判断、姿态状态和问题','application/planning/presence.py · application/planning/posture.py'],
      ['有限轮次局部补丁','模型 + 程序','把可修问题分批交给模型，携带实际草稿和上下文；每批保留已经完成的有效修改，再回到检查。','问题及其镜头、剩余轮次与时间','修订草稿、补丁尝试记录','application/planning/requests.py · planning/decisions.py'],
      ['保存剧本并交给编译','程序','保存被采用剧本、报告与原文定位；未解决的问题按当前规划策略阻塞或记录。','最终草稿与检查结果','chapter_script.json、规划记录','application/planning/flow.py · planning/outputs.py']
    ],branches:[['默认提纲','提纲 → 结构化分镜；检查与修订沿用当前规划策略。'],['六种创作方法（可选）','完整原文 → 分场剧本 → 方法专属拆镜 → 依据原文复核与修订。'],['沙箱 / 作者分镜（可选）','提案 → 采用分镜 → 绑定与校验；待选择不是生成失败。'],['补丁后仍不满足要求','在预算内继续既有修订策略；预算耗尽或不可修问题记录失败，不无限重规划。']]
  },
  pack:{
    title:'开拍准备：从剧本到实际请求',note:'这里同时展开打包和 prepare 的关系。已有视频或已被修复管理器接管的章节，prepare 不会抢占处理。H3 翻译与最终输入检查只在对应路径运行。',
    steps:[
      ['加载与解析场景','程序','加载剧本与身份资料，保留已确定的动作、对白和入镜关系；附加已有姿态记录。','chapter_script.json、身份及姿态记录','可编译的镜头','application/packing/service.py · application/identity/scene.py'],
      ['切段与原文映射','程序','按当前时长和对白策略形成片段，保留镜号、拆分范围和原文地址。','镜头、片长与画幅配置','片段、shot_indexes、segment_ids','story/compilation.py · application/packing/split.py'],
      ['绑定实际参考素材','程序','选择角色、场景、道具与音频；按穿戴和已有面罩状态选择相应参考，不把每张图都算作一个演员。','人物/道具/穿戴状态、资产索引','references、对白编号和素材绑定','application/packing/assets.py · application/packing/visor.py'],
      ['中文编译与心声分流','程序 / 配置','生成中文请求；开启后期心声时分开无台词表演与心声录音资料，保持说话人和字幕文本。','确定的场景和绑定','中文 prompt、inner_voice 元数据、clip_plan.json','application/packing/service.py · story/voice_delivery.py'],
      ['准备检查与建卡','程序 + 模型 / 按需','prepare 检查剧本和实体绑定、资产完整性，准备可拍章节。生成入口也保留片段级开拍检查。','计划、剧本、现有图片与音频','可执行片段或 pre_render_check.json 中的阻塞','application/preparation/flow.py · application/preparation/readiness.py'],
      ['H3 英文请求转换','模型 + 程序 / H3','按现有模板完成英文表达、主体和声音编号；导演修正落实到本次请求，不再仅堆旧备注。','中文请求、绑定及修正意见','H3 英文 prompt 与结构检查结果','application/rendering/h3.py · story/h3.py'],
      ['实际输入一致性检查','视觉模型 + 程序 / 按策略','检查真正发送的参考图片及中英文请求。区分明确矛盾、细节不足和相容描述；相同输入复用检查。','实际图片、请求行、参考绑定','request_check 结果；矛盾进入准备阻塞','application/preparation/request_check.py']
    ],branches:[['计划 / 资产缺失或明确冲突','当前片段等待修正，不先占用视频生成次数。'],['只是描述不够细','不直接等同于矛盾；按现有检查结论处理。'],['prepare 发现已有视频 / 已接管','记录 existing_video / production_owned，留给原生产或修复流程。'],['只用缓存','跳过生成前的建卡修复；素材匹配失败即报告缓存缺失。']]
  },
  generate:{
    title:'生成内部：缓存、供应商、语音与重试',note:'片段并行处理，但每段有自己的素材和尝试历史。网络重试、片段技术重试、内容修复是不同层，不应把它们当成同一个计数。',
    steps:[
      ['片段就绪检查','程序','识别计划和参考资产阻塞；被阻塞的片段不提交，其他片段继续处理。','计划、参考资产、运行参数','本次可执行片段与阻塞原因','application/rendering/flow.py'],
      ['匹配缓存与保留旧片','程序','匹配当前请求的素材，或读取按源证据复核获准保留的旧片；必要时恢复音轨分析。','请求、修正记录、已有 takes','可复用视频或新生成需求','media/cache.py · application/repair/history.py'],
      ['心声录音准备','模型 / 按配置','后期心声片段准备独立声音素材，复用匹配的录音；画面请求不携带该段心声台词。','inner_voice、说话人和参考音频','心声录音与时长信息','media/inner_voice.py'],
      ['构造请求与取得槽位','程序','应用通道参数、参考图片/音频和本次种子，沿用全局在途限制与实例池。','最终请求及剩余生成次数','供应商请求和占用的资源','media/generation.py · media/resources.py · providers'],
      ['提交、恢复与下载','视频模型 + 程序','调用 SD 或本地 H3，沿用任务恢复、下载与异常处理。提交结果不确定时按原规则保留状态。','供应商请求','原始视频、任务记录、素材元数据','providers/phanrouter_video.py · providers/local_h3.py'],
      ['音轨 / ASR / 技术检查','模型 + 程序','提取音轨、复用或运行 ASR，对照计划检查台词和意外说话，再计算片段技术结果。无对白仍可能检查多余语音。','原始视频、预期对白','analysis / ASR、passed 与具体 issues','media/analysis.py · media/speech.py'],
      ['选择重试或保留素材','程序 / 按策略','按失败类别、通道和剩余次数决定复用、请求修正、重试或停止；后续尝试失败仍保留已有可用素材。','本段结果、缓存与预算','selected take、尝试记录或失败原因','media/retries.py · application/rendering/attempt.py']
    ],branches:[['缓存命中','直接分析/复用，不提交新视频任务，不计新生成。'],['技术或语音不通过','按原重试策略及预算处理，不保证必定再拍；耗尽后保留失败证据。'],['供应商拒绝','由显式恢复步骤处理资产或请求；不是任意整章重规划。'],['提交结果不确定','不盲目重复发送可能已计费的任务。'],['片段异常','记录该段失败，其他在途片段继续完成。']]
  },
  assemble:{
    title:'后期、技术质检与报告',note:'后期处理消费已选素材，不负责判断剧情该怎么修。小说级豁免与范围配置继续决定哪些技术检查阻断、哪些仅报告。',
    steps:[
      ['选择本次素材与输出目录','程序','读取各片段 selected，使用当前成片或原有候选目录策略。','片段结果与发布状态','合成素材顺序、输出目录','application/rendering/flow.py · application/repair/delivery.py'],
      ['字幕对齐与分页','程序','使用原对白、ASR 和现有对齐结果制作字幕；无对白动作允许空字幕。','对白、音轨及 ASR','字幕事件与 ASS','media/subtitles.py · media/pagination.py'],
      ['心声、插卡与画幅处理','程序 / 按配置','混入后期心声，处理群聊插卡、画幅、封面片尾和音量。','素材、声音、字幕和小说配置','处理后的片段与封面片尾','media/inner_voice.py · media/chat_card.py · media/postprocess.py'],
      ['FFmpeg 合成','程序','按片段顺序合成视频及音轨，保持当前生产格式。','处理后的素材与字幕','最终或候选 MP4','media/postprocess.py'],
      ['成片技术质检','程序','检查画幅、字幕、音频、黑屏和冻结等；是否阻断按现有策略及小说豁免决定。','MP4、封面、片尾、ASS','media_qc_report.json、thin_passed','application/rendering/flow.py'],
      ['写报告与实际用量','程序','先写媒体报告，再记录实际生成历史；缓存缺失时保留原报告和成片。','合成结果、片段尝试','thin_media_report.json、生成历史','application/rendering/flow.py · application/repair/history.py']
    ],branches:[['缓存合成缺片','返回 cache_miss，不为补片调用生成，不覆盖原成片。'],['技术检查不通过','保留报告与失败原因，不能仅因导出 MP4 就标记可交付。'],['静音或冻结提示','查看当前小说 / 修复范围的豁免；不要把某本书的放行策略当作全局策略。']]
  },
  review:{
    title:'审查内部：实际视频、证据与判定',note:'这里的 review 审的是已生成片段。它与生成前的剧本检查、请求检查是不同环节；片段审查结果也不证明整集连起来一定好看。',
    steps:[
      ['确定当前审查素材','程序','优先取媒体报告中的 selected；心声后混音存在时使用对应素材，保留实际视频标识。','clip_plan、媒体报告、已有素材','本轮待审 takes','application/review/episode.py · application/review/store.py'],
      ['复用有效审查','程序','同一素材、适用策略和原文定位可复用旧 verdict；素材或相关证据变化时重审或补检查。','旧 episode_review、当前视频与证据','复用项和待审项','application/review/episode.py · review/storage.py'],
      ['取画面与原文证据','程序','准备视频/抽帧、原文片段、人物资料、计划与已识别语音，供当前判官路径使用。','视频、segments、bible、ASR','审查输入','application/review/evidence.py · application/review/video_sheet.py'],
      ['画面审查与计数','视觉模型 / 按模式','检查可见主体、人物身份、动作、物件与剧情表达；启用计数路径时区分穿戴装备和独立空甲。','画面与原文/计划证据','结构化 verdict 与具体证据','application/review/judges.py · application/review/cast_video.py'],
      ['终裁和输入归因','模型 / 按模式','相关路径进一步区分实际画面问题与请求矛盾，检查“计划本来就这样”是否有原文支持。','候选问题、当前请求和源证据','确认问题、误报或输入缺陷','application/review/adjudication.py · application/review/judges.py'],
      ['归类、补入语音问题','程序','按已有策略形成 must_fix 等处理级别，补入独立测得的意外语音，并保留已确认的问题。','verdict、语音分析、小说规则','flags、feedback、确认问题','review/policy.py · media/speech_repair.py · application/review/confirmed.py'],
      ['检查完成度并汇总','程序','区分已通过、待审、审查异常、未解决和过期素材，写回当前验收结果。','内容审查、技术报告、当前计划','episode_review.json、episode_execution.json','application/review/execution.py']
    ],branches:[['判官调用失败','记录 review_error，不能伪装成检查通过；其他片段继续。'],['没有对应视频','属于待审/缺素材，不能作为“没有问题”通过。'],['只 review','保存反馈与验收结果后结束，不调用修复或生成。'],['补查 / 精判（独立作业）','另有 verify、second_pass、audit_flow 等入口，并非每次单集审查必跑全部模式。']]
  },
  repair:{
    title:'修复内部：选原因、做候选、写回与复审',note:'只修改需要处理的片段及必要的拆分关联。批量自动审修调用单集执行器；修复管理器继续负责自己的扫描、派单与暂停续跑。',
    steps:[
      ['收集当前问题和历史','程序','读取 review、当前请求、实际素材、已做修改、实际生成次数和已有阻塞。','episode_review、clip_plan、repair_history','候选问题与有效预算','application/repair/inputs.py · application/repair/managed.py'],
      ['判断原因与选择路由','规则 + 模型 / 按需','区分源证据需复核、请求冲突、剧本表达问题与生成偏差；选择保留、改请求、改分镜或重拍。','问题证据与当前输入','处理动作及原因','repair/policy.py · application/repair/diagnosis.py'],
      ['源证据复核或生成候选','模型 + 程序','复核可能误判的旧片，或执行一次局部修改。返回候选内容、范围和失败原因。','路由、原文、邻镜状态和当前剧本','候选剧本/请求，或旧片可保留证据','application/repair/source_recheck.py · application/repair/clip.py'],
      ['重新编译与检查改动','程序 + 模型 / 按需','候选沿用共用编译与检查路径；确认有效修改、原文归属、参考绑定和受影响范围。','候选及原版本','可采用候选或明确阻塞','application/packing · application/repair/managed.py'],
      ['发布候选与记录尝试','程序','仅把接受的候选按原顺序写回产物，保留修改历史和原片；准备候选本身不是一次视频生成。','已检查候选、变化范围','更新计划、反馈、trial / 修复记录','application/repair/publication.py · application/repair/history.py'],
      ['更新必要素材并复审','视频 / 视觉模型','保留旧片则跳过生成；其余受影响片段按剩余预算生成、合成，再审当前素材。','接受的修改或重拍动作','新素材及新 verdict','application/rendering/reviewed.py · application/review/execution.py'],
      ['继续、通过或阻塞','程序','问题解决则汇总通过；仍有可处理问题按原预算继续，否则记录未解决原因。候选发布遵守原检查。','最新报告与剩余预算','验收结果、待处理或 blocked','application/repair/managed.py · application/repair/delivery.py']
    ],branches:[['源证据复核通过','保留旧视频，发布新的审查结论，不空耗重拍次数。'],['没有有效修改 / 候选检查失败','不按“修好了”写回，不凭空消耗一次视频生成。'],['预算耗尽','停止该段新增生成，保留剩余问题和历史。'],['一段修复失败','逐段记录失败，不拖垮其余片段。']]
  },
  operations:{
    title:'批量管理、单集入口与状态读取',note:'三条管理流程是运行方式，不是三个重复的生产引擎。架构页不查询或启动任务；实时任务状态由原有看板读取。',
    steps:[
      ['统一操作入口','程序','pipeline.py 选择小说与 production / prepare / repair 流程，复用原有锁、暂停标记和执行器。','人工操作、小说配置','对应流程的启动/停止/状态操作','application/production'],
      ['production 批量生产','调度','处理原有章节生产；显式无人值守模式接入与单集相同的审修执行器。','章节范围、准备状态、资源配置','批量执行记录与交付报告','application/production/flow.py · application/production/execution.py'],
      ['prepare 开拍准备','调度 / 独立流程','准备尚未进入生产的剧本、资产及 H3 请求，记录输入损坏和需要重规划等状态。','章节范围与准备状态','h3_preparation 记录','application/preparation/flow.py'],
      ['repair 修复管理','调度 / 独立流程','扫描既有问题、选择可派任务，推进准备、渲染、复审等既有步骤，保留暂停续跑记录。','审查结果、历史和管理状态','修复任务与进度记录','application/repair/manager_flow.py · application/repair/manager_dispatch.py'],
      ['看板读取与汇总','程序 / 只读展示','读取实际进程、报告和保存状态，分别展示技术完成、内容问题与交付。查询不推进生产。','任务状态、产物、用量与进程','实时 / 看板 / 工作台','application/dashboard · reporting']
    ],branches:[['只规划 / 只生成 / 只审查','使用对应独立入口，不必启动整条管理流程。'],['停止管理流程','按原机制停止派单或写暂停标记；已有在途任务按原流程完成。'],['状态口径','生成文件存在、技术合格、内容审查完成、可交付是不同结果。']]
  }
};
