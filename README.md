# Novel Manga Video

将小说批量改编为漫剧视频。雾月、星海、诸天共用同一条生产线，视频生成后端支持 SD2.0、SD2.5 和本地 H3。

## 安装与测试

```bash
uv sync --extra dev
uv run pytest
```

需要 ffmpeg；运行时的模型地址与凭据通过项目 `.env` 和环境变量提供。模型、素材和生成视频不进入 Git。

## 新书初始化

```bash
.venv/bin/python scripts/build_bible_thin.py inputs/book.txt \
  --novel-id demo --title 示例 --style 3d --frame 16:9
```

这一步读取小说、切章并生成初始人物和场景资料。随后在 `configs/pipeline.json` 中配置该书的章节范围、规划与视频资源。

## 唯一生产管理入口

```bash
.venv/bin/python scripts/pipeline.py validate
.venv/bin/python scripts/pipeline.py status
.venv/bin/python scripts/pipeline.py status --novel zhutian-card --json
.venv/bin/python scripts/pipeline.py start --novel xinghai
.venv/bin/python scripts/pipeline.py stop --novel xinghai
.venv/bin/python scripts/pipeline.py start --novel zhutian-card --flow prepare --chapters 2001-2660,2671-3848 --workers 6
.venv/bin/python scripts/pipeline.py start --novel zhutian-card --flow repair
.venv/bin/python scripts/pipeline.py stop --novel zhutian-card --flow repair
```

以上 start 是人工操作示例，不会在安装或查看状态时自动执行。

| 流程 | 负责什么 | start / stop 的含义 |
|---|---|---|
| `production`（默认） | 原有批量读书、规划、建卡、渲染与审片 | 启动原生产调度器；停止调度器，已有任务自行完成 |
| `prepare` | H3 开拍前的剧本和资产准备 | 复用上次章节范围和并发数，首次可从小说范围配置读取；停止派单并等待当前任务完成 |
| `repair` | 已有成片的审查、修复、重拍与复审 | 恢复原管理器和历史状态；停止时写入原生暂停标记，当前步骤可以完成 |

`status` 默认展示所有小说的三条流程，`--flow` 可过滤。JSON 与看板共用状态读取逻辑；在途按活着的工作进程统计，待办和阻塞按各流程保存的章节记录统计，三条流程不能直接相加当成交付总数。旧状态缺少计数时显示“未统计”。

底层执行器继续使用原锁、任务记录、缓存、并发池和重试预算。生产管理入口不会另建任务队列，也不会因启动而清空历史。

## 看板

```bash
.venv/bin/python scripts/status_server.py 8765
```

访问本机 `http://127.0.0.1:8765/` 或 `/board`。看板独立于已移除的旧生成 API，继续展示交付进度、修复、资源及三条流程的运行状态。

## 代码分工

- `src/novel_manga/story/`：身份、动作和对白的共享规则，不执行模型请求或调度。
- `src/novel_manga/story/compilation.py`：显式配置的片段编译，复用现有切段和提示词策略。
- `src/novel_manga/repair/`：修复决策、内存候选及场景修改；流程层负责验证后写回。
- `src/novel_manga/media/`：资产、生成、缓存、音轨分析、字幕和后期服务；每集使用独立 RenderContext。
- `src/novel_manga/review/`：审查字段、提示词、证据合并、判词解释、片段版本识别和原有补查队列存取。
- `src/novel_manga/model_client.py`：规划、身份核对和审查共用的 JSON 模型调用。
- `src/novel_manga/bible.py`：新书初始化所需的人物库生成。
- `src/novel_manga/batch_control.py`：已有执行器的操作入口与状态汇总。
- `src/novel_manga/` 其余模块：小说读取、数据结构、资产、视频后端、媒体处理和质检。
- `scripts/`：正式产线执行器、操作脚本和看板。
- `experiments/`：A/B、测速、手写样片、原文重建试验及旧规划方法；详见 [实验说明](experiments/README.md)。生产不得导入实验。
- `outputs/`：既有小说、计划、任务记录和媒体，整理代码不会迁移这些文件。

本文件是当前操作入口说明。[薄流水线](docs/thin-pipeline.md) 保留技术背景；带日期的文档是当时的记录，旧入口命令不作为当前操作指南。

剧本、修复与打包的接口和职责见 [解耦说明](docs/scene-contract-decoupling-20260916.md)。

渲染入口保持 `scripts/render_clips_thin.py`，流程编排位于 `scripts/render_flow_thin.py`；详见 [媒体模块说明](docs/render-module-decoupling-20260916.md)。

审查入口保持 `scripts/thin_review.py`，取证、模型评审、人物库补全、卡片和整集报告分别编排；详见 [审查模块说明](docs/review-module-decoupling-20260916.md)。

复审和补查命令只负责入口，状态存取、执行编排与成片发布分别维护；详见 [复审状态与发布说明](docs/review-state-decoupling-20260916.md)。

早期上传/生成/下载 HTTP API、`novel-manga` 命令、旧整集控制器及专用 API 容器已移除，不提供兼容入口。已有剧本、人物库、视频和任务历史保留。
