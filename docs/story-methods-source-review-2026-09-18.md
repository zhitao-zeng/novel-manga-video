# 六套原始 Skill：职责、缺失环节与本地接入研究

2026-09-18。六套含山音编剧、导演两个仓库，共七个仓库。已拉取并保存原始文档与参考资料，核对提交版本；未安装成全局 Skill，未执行上游脚本，未修改生产提示词、模型配置或暂停状态。

结论：当前 `planning/methods/` 是六种轻量创作偏好，不能称为六套原 Skill 的流程实现。它们最终都进入同一套“原文八区段 → 每区段一到两拍 → clips/stages”结构。原方法里的场景写作、导演设计、表演与连续性交接，大部分被缩成几项说明文字，没有相应的独立执行与复核。

六套本身也不是六个同类小说编剧器。应先分清编剧、导演、摄影与提示词编译的职责，再谈公平对照。

## 原始资料与版本

可读快照位于 `outputs/experiments/six-skills-source-study-20260918/sources/`；原 `.skill` 包也已保留并解包。每个目录的 `origin.json` 记录来源及提交。API 查询遇到限流后改用 Git 拉取，没有把缓存日期当成最新版本。

| 方法 | 上游版本 | 本次重点研读 |
|---|---|---|
| [山音编剧](https://github.com/Shanyin-ai/shanyin-screenwriting-master) | `ca20ce245ae028e64418393d6cf4688b7dbe3d89` | SKILL、核心戏剧动作与视听写作、短片流程、How-to-Tell、时长与双轨节奏 |
| [山音导演](https://github.com/Shanyin-ai/shanyin-director-master) | `30f0daecaf0754e08cf88ece83fabf3a2372a016` | SKILL、导演核心方法、镜头组、动作—反应与切镜、九列分镜及估时 |
| [社区 shortfilm-prompt](https://github.com/jnMetaCode/ai-shortfilm-prompts) | `80d6fc22c5a61667e96a6b1202dc361253fd1f28` | SKILL、多镜叙事模板、项目人物/风格锁、出入镜状态 |
| [Drama Skills](https://github.com/zenstory-ai/drama-skills) | `fcaba9c5d8f2614cad6ddf8b081c83d2e27d1754` | 原著分析、开发、写作、分镜、审查五个 SKILL；情节点与载体、声音设计、场景交接、动作落实、导演方案比较 |
| [造梦师](https://github.com/popopo-99/zy-cinematic-realism) | `e79c53dfb75d6015f66c222a27032b846cd508c1` | SKILL、场景原则、连续性 Bible、Shot Delta、模型编译边界 |
| [Leos 六部门](https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1) | `2ed50edb3c7838fc9ffb68e50824e0ebff59c7d4` | SKILL、导演语法、表演谱、输出契约、连续性与修片记录 |
| [Visual Skills](https://github.com/smixs/visual-skills) | `92be33a5a73325fb3d8e0c73b22744b114e2a90e` | video SKILL、dramaturgy、universal rules、导演/编剧/剪辑角色模式 |

研读聚焦小说到剧本、场景和镜头的交接；没有声称把所有模型适配器、长片模板和全部类型示例逐篇读完。上游关于特定供应商能力、固定镜数或成功率的陈述，不作为我们 H3 的实测结论。

## 六套方法实际做什么

### 1. 山音：编剧先成立，导演再决定怎样拍

编剧从核心戏剧动作、人物目标与前史出发，发展结构、场景及可表演正文；导演继承这些成果，再做定调、节奏、镜头组、局部视听修订和逐镜拆解。镜头组负责让几次观察共同完成一项叙事功能，单镜并不等于一段小说。原方法允许通过信息延迟、动作与反应的不同排列改变观看体验。[编剧说明](https://github.com/Shanyin-ai/shanyin-screenwriting-master/blob/ca20ce245ae028e64418393d6cf4688b7dbe3d89/README.md)、[导演流程](https://github.com/Shanyin-ai/shanyin-director-master/blob/30f0daecaf0754e08cf88ece83fabf3a2372a016/director-master/SKILL.md)、[镜头设计](https://github.com/Shanyin-ai/shanyin-director-master/blob/30f0daecaf0754e08cf88ece83fabf3a2372a016/director-master/references/shot-design.md)。

**当前缺口**：本地有戏剧动作和双轨节奏字段，但没有完整的分场剧本；`shot_group` 只是提纲里的一句话。第二遍直接生成技术分镜，无法核对一次揭示是否真正经历了铺垫、观察与反应。

**批量接法**：保留“分场正文 → 导演节奏与镜头组 → 逐镜”的交接，在内部自动执行。沿用原文已成立的目标和结局，不为填人物弧光而让绝望的主角突然接受现实。按需采用短片/剧集方法；不能只因片长一两分钟就把小说改成另一个概念故事。

### 2. 社区：多镜短片提示词与一致性组织

原 Skill 的五部分是提示词组织结构：主题、人物场景、氛围、摄影、分镜，并非五步小说分析。多镜模板在写第一镜前先固定角色/道具识别描述、整体风格，再核对相邻镜的动作方向和状态。它适合把已确定的故事组织成可生成的短片段。[原 Skill](https://github.com/jnMetaCode/ai-shortfilm-prompts/blob/80d6fc22c5a61667e96a6b1202dc361253fd1f28/skills/shortfilm-prompt/SKILL.md)、[项目模板](https://github.com/jnMetaCode/ai-shortfilm-prompts/blob/80d6fc22c5a61667e96a6b1202dc361253fd1f28/templates/project-planner.md)。

**当前缺口**：本地把它简化为物件、同期声等写作偏好，既没有实际按镜传递的物件状态，也没有完整复用的风格约束。试稿里“平底锅”在提纲出现，并不等于结尾获得了新的意义。

**批量接法**：先有可读场景，再执行主体/道具、风格、相邻状态与同期声的设计。不要把它包装成自带完整小说拆解能力的编剧器；其固定镜数、镜头品牌、手持呼吸和人为瑕疵等偏好，也不直接变成本项目规则。

### 3. Drama：原文功能、场景剧本、分镜分别负责

原著分析区分事件事实与叙事作用，并记录重要信息由行动、对白还是心理叙述承载；写作负责当集承诺、因果、场尾状态及实际场景正文；分镜再决定观众何时看到什么。其动作落实要求关注关键状态变化是否真正发生，而不只统计来源引用。声音设计也要求进入、撤出、留白与衔接有故事作用。[原著提取](https://github.com/zenstory-ai/drama-skills/blob/fcaba9c5d8f2614cad6ddf8b081c83d2e27d1754/skills/short-drama-novel-analyze/references/chapter-extraction.md)、[写作](https://github.com/zenstory-ai/drama-skills/blob/fcaba9c5d8f2614cad6ddf8b081c83d2e27d1754/skills/short-drama-write/SKILL.md)、[镜头手艺](https://github.com/zenstory-ai/drama-skills/blob/fcaba9c5d8f2614cad6ddf8b081c83d2e27d1754/skills/short-drama-storyboard/references/shot-craft.md)。

**当前缺口**：本地只有单集承诺、因果递进和几项逐拍字段，没有“原文心理信息 → 可听/可见载体 → 场景 → 镜头”的明确转换。八段都有引用，仍可能一镜从晚上拍到次日早晨。

**批量接法**：复用其职责分离，保留全文作为依据；为不可直接拍到的信息设计具体表达，再拆镜。人物/动物/物件沿用本项目开放动作字段。原文发生顺序与观众观看顺序分开记录，有明确来源的闪回、冷开场可以成立，不能被区段编号排序一律否决。

### 4. 造梦师：锁定场景，再表达摄影与连续变化

它主要是电影化场景、图像提示词与模型转译方法。Scene Master 固定事实、空间、当前动作和观察位置；连续性分支保留基础状态，只逐镜记录变化以及由此产生的手部占用、干湿、物件位置等后果。转译模型语法时不重新创造场景。[原 Skill](https://github.com/popopo-99/zy-cinematic-realism/blob/e79c53dfb75d6015f66c222a27032b846cd508c1/zy-cinematic-realism/SKILL.md)、[连续性](https://github.com/popopo-99/zy-cinematic-realism/blob/e79c53dfb75d6015f66c222a27032b846cd508c1/zy-cinematic-realism/references/continuity-cards.md)、[编译](https://github.com/popopo-99/zy-cinematic-realism/blob/e79c53dfb75d6015f66c222a27032b846cd508c1/zy-cinematic-realism/references/prompt-compiler.md)。

**当前缺口**：本地要求写观察位置和身体细节，但逐镜状态并未真正串接；“拿着湿衣服→穿上→下一镜又拿着”及短发/肩上长发冲突仍可能出现。它也不天然负责把整章小说写成完整剧情。

**批量接法**：作为场景与连续性处理，而不是替代编剧。将它的职责映射到已有场景解析和编译边界，不重新建一套人物库，也不要求每镜生成参考帧。上游该套标注 CC-BY-NC-4.0；此次仅保存研究资料，后续若直接分发其正文或实现，需要按实际许可处理。

### 5. Leos：共享场景上的表演、摄影和连续性协作

六角色各有职责：先确定场次目的与空间，再给表演、镜内活动、摄影和连续性建议，最后编译提示词。表演按刺激、接收、身体调整、实际动作和余波展开；重要道具动作必须保留前后状态。原文六角色并不等于必须启动六个独立进程。[原 Skill](https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1/blob/2ed50edb3c7838fc9ffb68e50824e0ebff59c7d4/SKILL.md)、[表演指导](https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1/blob/2ed50edb3c7838fc9ffb68e50824e0ebff59c7d4/references/performance-direction.md)、[连续性](https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1/blob/2ed50edb3c7838fc9ffb68e50824e0ebff59c7d4/references/continuity-and-learning.md)。

**当前缺口**：本地加了 stimulus、adjustment 等字段，却没有检查它们在镜头中的先后与落实。提纲写了完整故事，最终仍可能只生成前半章。

**批量接法**：一份共同场景，按职责完成表演与空间设计，随后合并为明确镜头；独立复核具体冲突，不机械产生六份重复意见。最终继续调用我们的 H3 英文模板，不照搬上游纯文本排版或另起六个常驻代理。

### 6. Visual：叙事节拍、镜头功能与剪辑节奏分层

其视频方法把戏剧变化、逐镜职责、剪辑节奏分开，要求说明机位为何改变、视线在切点落到哪里、声音或视觉元素如何贯穿、末镜留下什么。主要细节位于 references，SKILL 本身明确不能替代这些内容。它侧重短视频、导演和生成提示词，完整长篇编剧并非其默认职责。[原 Skill](https://github.com/smixs/visual-skills/blob/92be33a5a73325fb3d8e0c73b22744b114e2a90e/video/SKILL.md)、[叙事与剪辑](https://github.com/smixs/visual-skills/blob/92be33a5a73325fb3d8e0c73b22744b114e2a90e/video/references/dramaturgy.md)、[角色模式](https://github.com/smixs/visual-skills/blob/92be33a5a73325fb3d8e0c73b22744b114e2a90e/video/references/role-modes.md)。

**当前缺口**：本地有镜头职责和切点字段，但最终没有独立剪辑时间安排，也没有验证声音设计实际出现在哪些镜头。此次提纲说有节奏变化，正文仍是一段原文一个镜头。

**批量接法**：保留观众信息、镜头职责、切点和声音衔接的交接；将剪辑时长与模型素材时长分开。引用中的固定比例、秒数阶梯和每镜细节数量只作示例，不能成为增加碎镜或装饰的理由。

## 真实同源试跑给出的证据

已完成同一份完整《盛唐天工》第一章、同一人物/地点资料、同一模型与参数的六次自主改编。实际请求除方法信息外一致；没有把用户六版分镜喂给模型。所有模型响应正常停止，没有因输出预算截断。详细原始稿和比较见：

`outputs/experiments/story-methods-full-source-20260918/六种方法自主创作对照.md`

| 当前本地分支 | 镜数 | 字段检查 | 人工通读发现的具体问题 |
|---|---:|---|---|
| 山音 | 16 | 通过 | 第6镜在红砂岩取水中背包；第8镜用抽象判断表达时间变化；结尾缺少明确的期待与核对 |
| 社区 | 16 | 通过 | 先整理营地后取背包；第13镜合并宰羊、烤肉、熏肉；第14镜重复出发 |
| Drama | 8 | 通过 | 第5镜一镜跨夜晚与早餐；提纲擅自确认唐代并写成失忆 |
| 造梦师 | 16 | 通过 | 第9镜跨吃兔、睡梦与醒来；第14镜一句话走完三天；长发动作冲突 |
| Leos | 8 | 未通过 | 成稿止于吃兔，漏掉山羊、跋涉和盆地结尾；14拍只落实8拍 |
| Visual | 8 | 通过 | 第2、3镜重复取背包；第5镜跨夜；提纲中的声音和剪辑设计没有充分落实 |

以上是对当前本地实现、这个模型、这一章和一个种子的判断，不是原 Skill 能力排名，也不能把全部问题单独归因于模型。六稿都不宜直接投给视频模型；字段通过的五稿尤其说明：引用完整不等于故事表达完整。

## 本地共同层需要调整的地方

1. **原文区段只负责定位，不负责分配戏份。** `blueprint.py:57` 限制每区段1–2拍，归一化按区段顺序展开；最终并非硬性最多16镜，但很容易按区段各填一两镜。应按事件与连续时空组织场景，可跨区段引用，也允许一个密集区段拆多个场景。
2. **先写场景，再拆剪辑镜头，再打包生成请求。** 当前一次提纲后直接写 `clips/stages`，技术容器过早成为创作单位。导演不能靠给一个大事件段增加 camera 字段来完成拆镜。
3. **事实忠实与讲述方式分开。** 共同提示要求保持原文顺序，首遍校验拒绝倒序；这使有依据的冷开场/闪回难以表达。原文时序不改，观看顺序由方法有意选择，最后核对观众能否理解。
4. **方法决策必须被后续实际消费。** 提纲里的母题、延迟揭示和表演刺激，要能在场景正文、具体镜头与声音事件中找到实现；不能只保存一段说明就算完成。
5. **内容复核与机械检查各司其职。** 保留引用、完整性、身份等现有检查；补做有位置和证据的因果、时空、重复动作与信息揭示判断。不要再加一批关键词字典或凭空质量分数。
6. **表达与编译分开。** 本次归一化会出现“沈行舟拉出背包背包”等重复，源于结构化动作再次拼到事件文字前。应保留这项真实加工缺陷，与编剧模型的跨夜合镜区分处理。H3 最终仍使用当前英文模板，不能把上游所有负面词、中文说明或别家模型语法直接拼进去。

## 下一轮怎样才算测对

先保留这轮为现状基线，不继续换 seed 挑好稿。修订只在实验路线进行，批量生产不直接替换。

- 输入仍是同一份完整原文和相同人物基础资料；参考六稿仅作事后比较，不能进入创作输入。
- 山音、Drama 按各自编剧流程产生独立的分场剧本，再进入各自导演拆镜。
- 社区、造梦师、Leos、Visual 应明确其导演/视觉职责：先用相同的通用小说改编步骤获得可读场景，原方法接管自己负责的设计。这是“完整改编路线的对照”，不能声称四套原 Skill 都自带小说编剧器。若只比较导演效果，则四者输入同一份已确定剧本，另列实验。
- 先交可读剧本与逐镜表。对比故事是否完整、动作与持物是否连续、关键转折是否由可见/可听表达完成，以及六路线是否产生实质不同的观看体验。
- 记录真实调用次数、耗时和失败，不能用某一路更多重试换出的最佳稿冒充单次结果。采用相同的返修预算，只有指出具体问题后才做定点修订。
- 用户已明确批量操作：阶段间自动交接，不逐场要求人工点通过；不创建六套队列、不启动六个常驻代理、不强制生成关键帧或表情卡。

本轮到原始资料研究和现状基线为止，尚未执行上述新路线，不能声称创作质量已经改善。没有图片/视频生成，没有部署、提交或推送。
