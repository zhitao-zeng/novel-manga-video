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

## 代码与维护

正式入口保留在 `scripts/`；业务流程位于 `src/novel_manga/application/`，共享规则、媒体、模型接口和统计各自独立。

- [当前架构与修改位置](docs/architecture.md)：职责、依赖、配置来源和产物写入者。
- [测试与回归](tests/README.md)：请求、缓存、修复、媒体和看板的验证入口。
- [完整重构计划](docs/full-refactor-plan-20260917.md)与[实施记录](docs/full-refactor-progress-20260917.md)。
- [实验说明](experiments/README.md)：A/B、测速、人物库重建试验及旧整集实验。

看板页面和脚本位于 `src/novel_manga/dashboard/templates/` 与 `static/`；看板和用量命令共用 reporting 的统计。读取安装包中的服务时，用 `NOVEL_PROJECT_ROOT` 指向现有运行目录，产物不随安装位置迁移。

无对白镜头仍进行 ASR 检查，发现意外说话；语音是否阻挡交付继续按小说及章节范围配置。

早期上传/生成/下载 HTTP API、`novel-manga` 命令及旧整集控制器已移除。旧 shell 修复线的一次性接管入口也已退休；已有小说、人物库、视频、预算和任务历史保留。带日期的旧文档记录当时状态，当前操作以本 README 和架构说明为准。
