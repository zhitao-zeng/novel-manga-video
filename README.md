# Novel Manga Video

将小说批量改编为漫剧视频。正式生产使用薄流水线，支持 SD2.0、SD2.5 和本地 H3。

## 安装与验证

```bash
uv sync --extra dev
PYTHONPATH=src:scripts uv run pytest
```

## 生产入口

- `scripts/build_bible_thin.py`：读取小说、切章并建立初始人物和场景资料。
- `scripts/pipeline.py`：检查配置、查看状态、启动或停止批量生产。
- `scripts/status_server.py`：现有实时页面和前端看板。
- `configs/pipeline.json`：小说与生产资源配置。

阶段性的操作说明见 [薄流水线](docs/thin-pipeline.md)。代码整理期间继续沿用已有的生产、准备和修复执行器。

早期上传/下载生成 API、`novel-manga` 命令和原 API 容器部署已移除。历史媒体和任务记录不迁移；模型、素材、凭据不进入 Git。
