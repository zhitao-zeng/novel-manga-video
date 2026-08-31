# 短剧导演系统改动记录

记录日期：2026-08-18

分支：`codex/api-version`

Draft PR：[zhitao-zeng/novel-manga-video#1](https://github.com/zhitao-zeng/novel-manga-video/pull/1)

## 1. 改动背景

本轮改动参考了“秋沐泽”账号六个不重复竖屏短剧样片。可以从成片观察到的规律包括：

- 0–3 秒先给异常结果、关系冲突或关键道具；
- 对白、人物反应和信息揭示承担主要推进，解释性旁白较少；
- 镜头变化服务说话人、反应、道具和关系变化，而不是持续随机运镜；
- 当前小问题得到部分兑现后，结尾继续打开更大的关系、身份或力量问题。

无法仅凭成片确认创作者实际使用了多少人物图、场景图、关键帧、参考音频或完整提示词。因此，代码学习的是成片的导演规律，不声称复刻小云雀后台工作流。

## 2. 总体架构变化

原生产思路容易退化为：

```text
小说 → 摘要旁白 → 每镜生图 → 随机让图动起来
```

现在调整为：

```text
章节事实诊断
  → 独立 Showrunner
      ├─ RetentionPlan：观众为什么继续看
      ├─ InformationState：观众和角色分别知道什么
      └─ CharacterStateDelta：人物本集发生了什么变化
  → 剧本与事件映射
  → ShotIntent：为什么这样拍
  → 自适应视觉输入与表演/摄影计划
  → AudioBeat：声音在什么触发点变化
  → TTS、视频、字幕、混音和质量门禁
```

OpenAI-compatible 与命令规划后端使用五阶段流程：

```text
章节事实诊断 → Showrunner → 剧本 → 独立审稿 → 连续性状态提交
```

Showrunner 阶段不猜镜头编号，只绑定当前章 `event_ids`。剧本形成后，控制器再确定性计算 `shot_indexes`，避免一个大 Prompt 同时决定商业节奏和全部摄影细节。

## 3. 第一阶段：短剧导演与自适应生产

实施提交：`a07322d Add adaptive short-drama direction`

### 3.1 剧情与留存方向

- 新增 `short-drama-adaptive-v1` 创作模式；
- 按言情、玄幻、复仇、悬疑、成长选择不同题材发动机；
- 每集只保留一个核心戏剧问题；
- 冷开场可以前置当前章后段结果，但必须附当前章逐字证据；
- 前置结果必须在原因建立后重新兑现；
- 按题材限制旁白字数比例；
- 忠实顺序模式 `faithful-chronological-v1` 继续保留作为回退。

### 3.2 人物资产

人物圣经从基础外貌扩展为：

- 视觉原型、脸部锚点、轮廓、发型和角色配色；
- 基础服装、逐集服装、标志性道具；
- 表情范围、动作习惯和稳定声音身份。

人物定妆资产负责锁定“这个人是谁”，不再把某一镜的静态姿势和构图写进人物母版。

### 3.3 生图与关键帧

新增自适应视觉策略：

| 镜头类型 | 默认视觉输入 |
|---|---|
| 单人普通对白或反应 | 人物资产 + 空场景 |
| 无人物空镜 | 空场景资产 |
| 多人精确站位 | 剧情关键帧 |
| 递物、抓手、打斗等精确交互 | 剧情关键帧 |
| 关键道具、结果揭示、高潮反转 | 剧情关键帧 |

每镜记录 `visual_strategy` 和 `keyframe_reasons`。本地 MiniMax H3 可以直接使用人物与场景资产；当前 PhanRouter SD2.5 适配器只有单张 `reference_image`，因此 API 路径仍安全回退到剧情关键帧。

### 3.4 表演与摄影

- 表演按“触发 → 察觉 → 主要动作 → 对方反应 → 收束”组织；
- 不再要求人物每隔一两秒机械转头、摆手或改变重心；
- 摄影机默认锁定；
- 只有空间揭示、人物位移、视点变化、权力或情绪转折才允许运镜；
- 限制全集运镜比例，并禁止相邻镜头连续明显运镜。

### 3.5 声音

- TTS 只负责需要锁定的对白、旁白、内心声和画外角色声音；
- 环境、音乐和拟音作为结构化声音意图，不写成旁白；
- 当前仍以逐句锁定 TTS 保证文字准确；
- 没有启用“只给一段音色参考、让视频模型生成全部对白”，因为现有适配器和最终重混流程无法安全验证完整原生人声或分轨，贸然启用会产生错词或双重人声。

### 3.6 配置变化

生产环境默认：

```bash
NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
NOVEL_LOCAL_VISUAL_STRATEGY=adaptive
NOVEL_INTRO_SECONDS=0
```

旧模式回退：

```bash
NOVEL_CREATIVE_PROFILE=faithful-chronological-v1
NOVEL_LOCAL_VISUAL_STRATEGY=keyframe
NOVEL_INTRO_SECONDS=4
```

## 4. 第二阶段：Showrunner 商业短剧决策层

实施提交：`ff8113c Add showrunner planning layer`

### 4.1 RetentionPlan

- 使用 0–1 相对时间而不是写死第 5、15、30 秒；
- 每集安排 4–8 个节点；
- 必须包含前 5% 的 `hook`、`question`、至少一个 `payoff` 或 `reversal`，以及后 20% 的 `cliffhanger`；
- 每个节点绑定当前章事件、逐字证据、观众问题、承诺、新信息和情绪变化；
- 默认不允许超过全片 25% 的中段注意力空窗。

### 4.2 InformationState

每个重要事实显式记录：

- 事实当前是已确认、潜在线索还是误读；
- 观众是知道、怀疑、被误导还是尚不知道；
- 各角色分别知道、怀疑、误解或不知道什么；
- 信息差用于观众领先、角色领先、同步揭示、误会还是暂时隐藏；
- 在哪个留存节点揭示。

这些信息全部绑定当前章 `event_ids` 和逐字 `source_quote`。

### 4.3 CharacterStateDelta

永久身份和剧情状态被明确分开：

- 永久身份：脸、发型、体型、基础声音和角色主色；
- 剧情状态：社会地位、关系、力量、情绪、信心和服装状态。

只有当前章确实发生变化时才记录 `before/after`，两者不得相同。剧本通过门禁后，状态才提交到跨集 `SeriesState`。跨集信息状态也保存来源集数和原文证据。

### 4.4 ShotIntent 与 AudioBeat

每镜新增：

- 戏剧功能；
- 权力关系；
- 目标情绪；
- 观众视觉焦点；
- 关联的信息事实；
- 关联的留存节点。

生图、关键帧、视频提示词和生产运行时会消费这些字段。承担信息揭示、兑现或悬念的镜头会更谨慎地使用剧情关键帧。

声音使用相对 `AudioBeat`：

```text
0%   ambience  建立空间环境
18%  impact    关键信息真正可读时触发
42%  duck      锁定台词开始时压低背景
90%  release   动作或反应落定后留一拍
```

音频变化必须由台词、动作、揭示或反应触发，不能机械铺满整镜。

### 4.5 新增质量门禁

`ScriptQualityReport` 新增并重新计算：

- `retention_beat_coverage`；
- `max_attention_gap_ratio`；
- `information_fact_grounding`；
- `character_delta_grounding`；
- `shot_intent_coverage`；
- `audio_beat_coverage`。

系统会阻断无原文依据的信息、缺失的人物状态变化、中段留存空窗、无效留存映射、未绑定镜头意图或音频节拍的生产计划。

### 4.6 规划协议

- 规划协议从 `novel-manga-planner/v3` 升级为 `v4`；
- 新增独立 `plan_showrunner` 操作；
- `novel-manga contract` 会输出 Showrunner JSON Schema；
- 本地模型 CLI 与命令规划适配器同步支持新操作；
- 确定性规划器无法做可靠语义判断时生成 `planning_mode=inferred_fallback`，并留下 warning，不伪装成模型完成的商业策划。

## 5. 文档与测试

- 方法论文档：`docs/short-drama-methodology.md`；
- 本实施记录：`docs/short-drama-change-record.md`；
- 新增 Showrunner、留存空窗、信息证据、人物状态、音频节拍和命令协议测试；
- 原有依赖未上传的测试数据改为自包含测试；
- 最终共 158 项测试通过；
- `python3 -m compileall -q src runtime tests` 通过；
- `novel-manga contract` 已验证 `v4`、`plan_showrunner` 和 Showrunner Schema；
- 提交前凭据扫描、媒体/模型/缓存文件审计和 `git diff --check` 通过。

## 6. 当前尚未验证的边界

- 本地没有调用真实生图、TTS、Seedance 2.5 或 MiniMax H3；
- 新旧策略还没有用同一小说、同一模型完成真实 A/B 成片；
- `AudioBeat` 已进入规划、视频提示、缓存身份和审计，但仓库尚无绑定授权素材库或声音模型的最终非语言混音器；
- PhanRouter SD2.5 多人物资产直出仍受单参考图适配器限制；
- 无法从参考账号成片确认其原始提示词、关键帧数量和参考音频策略。

下一步真实验证应使用同一章小说对比旧版和新版，重点检查前 15 秒吸引力、旁白比例、无效运镜、人物一致性、关键帧命中率、信息兑现节奏和最终声音层次。
