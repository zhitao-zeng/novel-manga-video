# Novel Manga Video

把 `txt / markdown / docx / pdf` 小说改编成可批量生产的 9:16、3D 国漫短剧。系统是普通 Python
控制器，不依赖 Codex；当前唯一可执行生产档是：

- `short-drama-adaptive-v1`
- 本地 DeepSeek 命令规划器
- 可复用人物卡、地点卡，必要时生成剧情关键帧
- Seedance 2.5 原生对白与环境声
- ASR 字幕、专名纠正和原生对白硬门
- 0.15 秒跨切、低电平 BGM、最终响度归一

旧 TTS、参考音频、MiniMax H3、ComfyUI、Qwen Image 本地生产分支已经移除；历史输出仍可读取，不能再执行。

## 当前数据流

```text
小说
  → 系列开发（压力引擎 / 主角惯常策略 / 关系压力网 / 逐章投影）
  → 独立系列审稿并版本化
  → 章节事实诊断
  → Showrunner（读取当前章投影，规划留存、信息差、选择与代价）
  → EpisodeContract
  → 每个 RetentionBeat 独立写 1–6 镜
  → 独立 direction pass（动作 / 机位 / 五维 handoff，可按 turn 边界拆镜）
  → structural / reviewed / craft 分级门禁
  → 人物卡、地点卡与剧情关键帧
  → 原生对白视频
  → ASR 字幕与有限重生成
  → 跨切、BGM、响度归一、媒体准入
```

剧本支持 `verbatim / abridged / derived`：`abridged` 只能从原句删子句并保持字序；`derived` 必须用
`serves` 指向已有 `event_id / fact_id`。允许无名听者、因果桥接和反应来把原文翻译成可拍内容；禁止新增
事实、事件、原创支线和 StoryBible 外的具名角色。`native_dialogue` 下禁止旁白和内心声，时间跳转改为字幕卡。

## 安装与测试

```bash
uv sync --extra dev
uv run pytest
```

只检查分集：

```bash
uv run novel-manga inspect examples/短篇示例.txt \
  --novel-id demo --title 雨夜来信
```

`mock + preview` 只验证工程闭环，不具备生产资格：

```bash
uv run novel-manga generate examples/短篇示例.txt \
  --novel-id demo --title 雨夜来信 \
  --provider mock --admission-mode preview --output outputs
```

## 本地 DeepSeek 规划

默认规划路由是 `http://127.0.0.1:4000` 的 `deepseek-local`；当前已核对该别名解析到
`DeepSeek-V4-Flash-0731`。Qwen 不是正式 A/B 的默认规划模型。

```bash
export NOVEL_PLANNER_BACKEND=command
export NOVEL_PLANNER_COMMAND="$(pwd)/.venv/bin/python $(pwd)/scripts/deepseek_local_planner_command.py"
export DEEPSEEK_LOCAL_ROUTER_URL=http://127.0.0.1:4000
export DEEPSEEK_LOCAL_MODEL=deepseek-local
export NOVEL_PLANNER_MAX_REVISIONS=2
export NOVEL_PLANNER_BEAT_MAX_RETRIES=1
export NOVEL_PLANNING_TIMEOUT_SECONDS=600

uv run novel-manga plan /input/novel.txt \
  --novel-id demo --title 小说名 --output outputs/plans
```

规划失败的语义是 `planning_failed`：保留各 beat 的请求、响应、修订和失败证据，不进入任何媒体阶段。
镜数和字数密度只进 A/B 报告，不通过拆 turn 凑数形成硬门。

可继续做只读验证与编译：

```bash
uv run novel-manga validate-plan /input/novel.txt \
  --bundle outputs/plans/demo --novel-id demo --title 小说名

uv run novel-manga compile-plan /input/novel.txt \
  --bundle outputs/plans/demo --novel-id demo --title 小说名
```

EP1 的本地规划 A/B 工具：

```bash
uv run python scripts/run_episode1_local_plan_ab.py \
  --source /path/to/chapter-1.txt \
  --story-bible /path/to/story_bible.json \
  --output /path/to/evidence

uv run python scripts/report_episode1_plan_ab.py --help
```

## 生产配置

生产必须使用命令规划器、原生对白和真实 ASR：

```bash
export NOVEL_PROVIDER=phanrouter
export NOVEL_ADMISSION_MODE=production
export NOVEL_FINAL_AUDIO_POLICY=native_dialogue
export NOVEL_PLANNER_BACKEND=command
export NOVEL_PLANNER_COMMAND="$(pwd)/.venv/bin/python $(pwd)/scripts/deepseek_local_planner_command.py"
export NOVEL_ASR_COMMAND=/models/adapters/asr_adapter
export NOVEL_VIDEO_WORKERS=2
export PHANROUTER_API_KEY=...  # 只在运行环境注入

uv run novel-manga generate /input/novel.docx \
  --novel-id 1 --title 小说名 \
  --provider phanrouter --admission-mode production --output /output
```

`command` provider 的媒体协议不经过 shell：

```text
$NOVEL_IMAGE_COMMAND --prompt TEXT --width 1080 --height 1920 \
  [--reference FILE] [--additional-reference FILE ...] --output IMAGE

$NOVEL_VIDEO_COMMAND --prompt TEXT --image FILE --duration SECONDS \
  --fps 25 --width 1080 --height 1920 \
  [--additional-image FILE ...] --output VIDEO

$NOVEL_ASR_COMMAND --unit-id ID --audio AUDIO --text REFERENCE --output result.json
```

视频适配器必须交付含原生音轨的 MP4；不会收到 TTS 或参考音频。ASR 至少返回 `hypothesis` 和
`backend`，可额外返回 `speaker_count / speaker_ids / segments`。字幕只取 ASR 结果，并用
`NOVEL_PROTECTED_LEXICON_JSON` 修正剧本中已知的专名。

原生对白硬检查只有三项：

1. 人声能量存在；
2. `CER <= 0.5`，失败后同契约重生一次，再失败改反应镜或过肩镜；
3. 单人契约不能检出多个说话者。

旧的 `0.12 / 0.35` CER 阈值保留在报告中，不再阻断原生对白成片。

## 规划命令协议

```text
$NOVEL_PLANNER_COMMAND \
  --operation build_bible|diagnose_episode|develop_series|review_series_development|
              plan_showrunner|plan_beat_script|plan_beat_direction|review_episode|
              update_series_state|blind_compare \
  --input request.json --output response.json
```

v5 请求携带 JSON Schema、当前章、StoryBible、系列投影、已释放事实、上一 beat 的五维 close state 和
结构化修订错误。每个 beat 单独消耗重试预算；耗尽后整集失败，不回退到确定性薄稿。

## HTTP 服务

默认镜像启动 Uvicorn 80 端口：

```text
GET  /ready
POST /upload_novel
GET  /generate_progress?novel_id=1
GET  /download/video/1_1
GET  /download/image/1_1_cover
GET  /download/image/1_1_ending
```

```bash
docker build -t novel-manga-video:0.13.0 .
docker run --rm -p 8080:80 -v /path/to/output:/output \
  -e NOVEL_PROVIDER=mock -e NOVEL_ADMISSION_MODE=preview \
  novel-manga-video:0.13.0
```

正式容器通过运行时环境注入 `PHANROUTER_API_KEY`、`NOVEL_PLANNER_COMMAND` 和
`NOVEL_ASR_COMMAND`。镜像不包含模型、媒体、凭据或 `.codex` 研究证据。

## 主要产物

```text
/output/<novel_id>/
├── story_bible.json
├── series_development/
│   ├── active.json
│   ├── series_development.vNNN.json
│   └── series_development_review.vNNN.json
├── series_assets/
│   ├── manifest.json
│   ├── characters/<id>/{spec.json,turnaround.jpeg,expressions.jpeg}
│   └── locations/<id>/{spec.json,establishing.jpeg}
└── <video_id>/
    ├── episode_contract.json
    ├── episode_plan.json
    ├── script_quality_report.json
    ├── production_preflight_report.json
    ├── production_plan.json
    ├── visual_group_plan.json
    ├── visual_generation_report.json
    ├── native_audio_selection_report.json
    ├── alignment_report.json
    ├── asr_report.json
    ├── content_trace.json
    ├── media_qc_report.json
    ├── admission_report.json
    ├── <video_id>.mp4
    ├── <video_id>_cover.jpeg
    └── <video_id>_ending.jpeg
```

成功成片固定为 1080×1920、25/30 fps、H.264/AAC MP4；封面和结束画面为 JPEG。每个成功集必须保留
原文到镜头的 `content_trace.json`，并通过最终媒体质量门。

实现细节见 [短剧方法](docs/short-drama-methodology.md) 与
[镜头契约](docs/shot-contract-pipeline.md)。
