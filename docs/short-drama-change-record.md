# Planner v5 / native-dialogue change record

记录日期：2026-08-31
分支：`codex/api-version`

## 内容规划

- 新增一次全书级系列开发与独立 reviewer，版本化保存压力引擎、主角默认策略、升级阶梯、关系压力网和逐章投影。
- Showrunner 必须读取当前章投影；每集由投影、章节诊断与 Showrunner 合成 `EpisodeContract`。
- 剧本按 `RetentionBeat` 分段生成；每段只有 1–6 镜，单段失败只重试该段。
- 写作与导演拆成两个调用；direction pass 可按 turn 边界拆镜，并重新绑定 shot index。
- 新增 `abridged` 源锚定方式，以及 `device / serves` 外化契约。
- 扩写允许无名听者、信息载体、因果桥接和反应；禁止新增事实、事件、原创支线和 StoryBible 外具名角色。
- `protagonist_choice / cost_paid / episode_mode / opposition` 进入结构化每集契约。
- open/close state 拆为知识、权力、关系、物理、进行中动作五维，PRE 逐字段检查。
- 镜数、turn 数和发声字数改为 A/B 报告，不以拆 turn 凑数形成硬门。
- beat 重试耗尽后标记 `planning_failed`，保留中间产物且不授权媒体生成。

## 原生对白

- 唯一可执行音频档为 `native_dialogue`；可见对白不准备 TTS，也不传参考音频。
- 旁白和内心声在此档下阻断；时间跳转使用 `title_card`，无声动作使用 `silent_action`。
- 时长由台词字数与动作 beat 预算决定，限制在 4–14 秒；装配不再按外部音频裁视频。
- 字幕从 ASR 结果生成，并用 protected lexicon 修正剧本中已知专名。
- 原生对白硬门只检查人声能量、`CER <= 0.5` 和单人契约的说话人数；旧 CER 阈值仅报告。
- 原生音频选择后保留一个 VC 插入点；当前没有 VC 后端，因此明确记录 passthrough，不伪造实验结果。
- 片段使用 0.15 秒 acrossfade，BGM 低电平混合，`amix normalize=0`，末尾统一 loudnorm。

## 运行时收敛

- 正式规划器固定为本地 DeepSeek 命令适配器；EP1 A/B 使用 `deepseek-local`，已核对映射到 `DeepSeek-V4-Flash-0731`。
- 删除 TTS、参考音频、强制对齐、本地模型 supervisor、ComfyUI、MiniMax H3 和 Qwen Image 本地并行路径。
- 删除 relaxed、relaxed scale、bounded reviewer fallback 等放水路径；规划失败不再回退成确定性薄稿。
- 环境变量收敛到一个短剧生产 profile，只保留 provider、凭据、并发、超时和证据后端等真实运行参数。

## EP1 规划 A/B

同一章对比旧自动版、Planner v5 与手写 ceiling：

| 指标 | 旧自动版 | Planner v5 | 手写 ceiling |
| --- | ---: | ---: | ---: |
| 镜数 | 16 | 28 | 33 |
| 语义 turn | 19 | 56 | 42 |
| 发声 turn | 19 | 45 | 42 |
| 发声字数 | 397 | 617 | 676 |
| 最长发声 turn | 60 | 20 | 60 |
| voiceover 字数 | 108 | 0 | 90 |
| `serves` 覆盖率 | — | 100% | — |

同模型 DeepSeek 对匿名 A/B 的七个审查问题全部选择 v5。内容质量样本通过；另一次零缓存冷启动在 Showrunner
阶段失败，因此当前结论是“质量改进成立，冷启动可靠性尚未证明”，没有把一次成功冒充稳定性结论。

## 验证边界

- 当前 A/B 只跑规划，没有用两个版本各自生成整集媒体。
- 本地没有可调用的 VC backend，原生 vs 原生+VC 的 10 镜实验未伪造，等后端可用再执行。
- 生产成功仍要求 1080×1920、H.264/AAC、字幕烧录、ASR、视频侧审查、媒体 QC 与 `content_trace.json`。
