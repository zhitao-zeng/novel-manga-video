# 渲染与媒体模块解耦 · 2026-09-16

本轮保持现有请求、模型参数、并发、重试、缓存、字幕和质检策略。包含整理前运行工作树中“无对白场景允许空字幕”的修复；其他资源配置与供应商本地改动保留。

## 职责和依赖

| 所有者 | 职责 |
|---|---|
| scripts/render_clips_thin.py | 原命令入口，只解析参数、构造流程、执行和输出结果，保留全部原参数。 |
| scripts/render_flow_thin.py | ThinMediaRunner 编排：准备、缓存、生成、分析、必要的现有修正、合成、质检和历史记录。 |
| media/context.py | 独立的 RenderContext、ClipResult 和 AssemblyResult 类型；不新增落盘格式。 |
| media/asset_factory.py、assets.py | 共享资产工厂、所需卡片构建、损坏检查、隐私拒绝后的原处理、资产记录。 |
| media/adapters.py | 画幅与参考图适配，复用原 SD/H3 provider。 |
| media/generation.py | 请求材料、参考音频预算和编号、供应商调用。 |
| media/cache.py | 请求匹配、现有尝试查找、失效缓存及 sidecar 归档。 |
| media/policy.py、resources.py | 原重试条件和常量、原文件锁并发槽；不增加调度器。 |
| media/analysis.py | 音轨提取、既有 ASR、语音结果计算及重查；不调用视频生成。 |
| media/subtitles.py、chat_card.py | 字幕对齐分页和群聊插卡。 |
| media/postprocess.py | 封面片尾、画幅、拼接与合成；BatchRenderer 明确实现原硬切与字幕边距，不临时替换实例方法。 |
| media/common.py | 音量检测、封面标题、既有参考图摘要等工具。 |
| 原 qc.py、repair_history.py | 原技术检查、候选发布和修复历史；职责和记录顺序不变。 |

媒体模块不导入生产脚本。建卡、审查和修复历史改为导入工具所属模块。旧整集运行器移入 experiments/legacy，实验和相应测试继续保留；不恢复旧 API。

## 内部调用

- Python 调用方从 render_flow_thin 导入 ThinMediaRunner；本集数据访问 runner.context。命令行调用方式不变。
- RenderContext 保存当前小说的配置、画幅、参考音频预算、风格规则、计划、反馈和临时状态；配置、画幅和可变集合不跨运行共享。
- 生成模块只执行本次供应商调用，重试循环和有效次数协调仍由流程层按原规则执行。
- 缓存查询不触发视频生成；cache-only 缺段时保留已有素材、报告和成片。
- 分析返回现有结构，补亮等有副作用的现有处理由编排显式调用。成片输出目录由原历史服务决定。
- 流程继续先写 thin_media_report.json，再 record_render；技术或内容未通过的候选不因此获得发布资格。

## 验证

- SD2.0、SD2.5、H3、无对白、画外音、群聊加时间跳转卡，共六组固定素材对照。
- 使用模拟 provider 和已给定 ASR，真实执行 FFmpeg；未调用真实图片、视频或文本生成服务。
- 六组的请求参数、request.json、缓存命中、FFmpeg 命令、ASS、合成报告、音视频参数和质检结果与旧代码逐项一致；最终均为 25 fps。
- 新增回归验证同一进程中不同小说和横竖屏隔离、成功与异常释放槽位、不重复提交不确定任务、缓存缺段不生成不覆盖、原命令 dry-run 可用。
- 合成测试检查画幅、实际帧数、片段顺序、AAC 双声道、字幕边距和重复执行。中间 concat 文件的名义帧率可能与实际呈现不同，最终交付的帧率另外由完整合成对照验证。

独立导出的实际提交版本完整测试 788 项通过。原入口已从约 2088 行缩为 67 行，流程编排与媒体实现分别维护。

原始对照材料和测试日志保存在本机 outputs/render-decoupling-20260916/，不进入 Git。

不重建人物库、不重规划、不重拍历史章节；诸天继续暂停。生成请求的示例验证不代表真实模型质量验收。
