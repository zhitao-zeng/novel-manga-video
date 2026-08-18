# Novel Manga Video

短剧化改编、Showrunner 留存/信息差/人物状态、人物设计、自适应关键帧、生图 / Seedance 提示词和声音分工见
[`docs/short-drama-methodology.md`](docs/short-drama-methodology.md)。
本轮实现范围、提交、门禁、验证结果和未验证边界见
[`docs/short-drama-change-record.md`](docs/short-drama-change-record.md)。

把 `txt / markdown / docx / pdf` 小说转为 9:16 国漫画风漫剧。生产控制器是普通 Python 程序，不依赖
Codex；故事规划、生图、生视频、TTS、ASR 和强制对齐均为可替换 provider。

## 榜单 HTTP 接口

镜像默认在 `80` 端口启动 HTTP SUT，提供文档要求的三个业务接口：

```text
GET  /ready
POST /upload_novel                  multipart: novel_id + file
GET  /generate_progress?novel_id=1
GET  /download/video/1_1
GET  /download/image/1_1_cover
GET  /download/image/1_1_ending
```

`POST /upload_novel` 校验并持久化上传文件后立即返回，后台执行现有生产管线。任务状态保存在
`/output/.jobs`，原文件保存在 `/output/.uploads`；容器重启后会恢复 `processing` 任务。完成响应中的
`video_list` 使用连续的 `{novel_id}_{index}` 编号，返回对应原文章节全文，封面和结束画面以 `.jpg`
文件名对外暴露，下载内容均为 JPEG。

API 镜像接口冒烟测试：

```bash
docker build -t novel-manga-video:0.12.0 .
docker run --rm -p 8080:80 -v /path/to/output:/output \
  -e NOVEL_PROVIDER=mock -e NOVEL_ADMISSION_MODE=preview \
  novel-manga-video:0.12.0

curl http://127.0.0.1:8080/ready
curl -F novel_id=1 -F file=@/path/to/novel.txt \
  http://127.0.0.1:8080/upload_novel
curl 'http://127.0.0.1:8080/generate_progress?novel_id=1'
```

`mock + preview` 只用于接口和工程闭环验证，不具备参赛资格。正式镜像运行必须设置
`NOVEL_PROVIDER=phanrouter|command`、`NOVEL_ADMISSION_MODE=production` 及对应媒体/ASR 后端。单个小说
内部的视频生成仍由 `NOVEL_VIDEO_WORKERS=2` 控制两并发；外层小说任务默认串行，避免 4 核环境过载。

## 可执行生产协议

API 与纯本地镜像共用下面这一份 Core；LLM 和媒体模型都不能跳过质量门禁：

```text
小说 → 按原文章节分集 → 题材发动机 + 戏剧问题 + 结果前置冷开场
     → ShowrunnerPlan（留存曲线 + 信息差 + 人物状态增量）
     → 一次完成全书系列圣经和各章审稿方案
     → 单人角色资产 + 无人物场景资产（全书复用）
     → scene → shot → PerformancePlan + CameraPlan + AudioPlan → turn
     → 必须准确的逐句音频 + ASR + 对齐
     → 自适应人物/场景直出或剧情锚点关键帧 + 专用封面
     → 旁白/画外镜头视频 + 可见对白参考音频视频
     → 轻量人脸一致性评分（仅监测，不重生成、不阻断）
     → 无字幕成片 → ASS 烧录 → 媒体/字幕/ASR 最终准入
```

两种部署只有 adapter 不同：

| 能力 | API 后端 | A100 离线后端 |
| --- | --- | --- |
| 规划 | OpenAI-compatible 或命令适配器 | Qwen3.5-27B-AWQ |
| 基础资产 | PhanRouter 图像 API | Z-Image-Turbo |
| 资产/关键帧/封面编辑 | PhanRouter 图像 API | Qwen-Image-Edit-2511 |
| TTS | API/命令 TTS | VoxCPM2（可选 Qwen 回退） |
| 视频 | Seedance 2.5 API | MiniMax H3 Ref2VA（ComfyUI，本地音频驱动） |
| ASR/对齐 | 外部命令 | Qwen3-ASR / ForcedAligner |

章节拆分、五阶段剧本、资产和音色绑定、连续镜头组织、锁定音频、字幕、渲染、缓存身份、内容追溯和
最终准入全部位于同一个 Python Core。`provider=phanrouter` 不执行模型生命周期钩子；
`provider=command` 可通过 `NOVEL_MODEL_LIFECYCLE_COMMAND` 将同一批 Core 请求交给单卡 supervisor。

关键约束：

- 有章节时严格一章一集；无章节时只在句子或段落边界按 3,000—6,000 字拆分。
- 角色 `turnaround.jpeg`、`expressions.jpeg` 与场景 `establishing.jpeg` 先生成、全书复用。
- 复用身份和场景资产，不复用不同说话人的完整构图；相邻同场景 turn 会按音频时长合并为连续视觉组，
  可见对白组仍优先建立清晰的说话人近景。
  可见对白镜头优先锁定当前说话人的角色资产；旁白和连续多人镜头使用场景与不超过两名角色的参考板。
- 每个 turn 绑定同一份锁定文本、固定音色、音频、字幕对齐、视觉输入决策和视频片段；
  普通单人/空镜可复用人物与场景资产，关键构图才生成剧情锚点帧。
- 每个 shot 必须先形成结构化表演计划和摄影机计划：动作按“触发→动作→反应→收束”排列；摄影机默认
  `locked`，只有明确的人物位移、空间揭示、情绪/权力转折才允许一次克制的短轨迹。全集移动镜头不超过约
  三分之一、强调运镜不超过约十分之一，相邻镜头不得连续明显运镜；连续视觉组最终只保留一份摄影机计划。
  运行时按真实参考音频长度重新分配表演时间段。
- 每集在分镜执行前形成 `ShowrunnerPlan`：4–8 个相对留存节点覆盖 hook、question、payoff/reversal 和
  cliffhanger；信息状态明确观众与各角色分别知道或误解什么；人物状态增量只记录有当前章证据的社会地位、
  关系、力量、情绪、信心和服装变化。门禁拒绝中段注意力空窗、无证据信息和无实际变化的状态增量。
- 每镜 `ShotIntent` 先声明戏剧功能、权力关系、目标情绪、观众焦点及留存节点，再决定景别、构图、关键帧和
  运镜；`AudioBeat` 以 0–1 相对位置记录由台词、动作、揭示或反应触发的静音、环境、冲击、音乐起落、duck
  和收束。它们进入视频提示和审计，但非语言声音的最终混音仍需媒体后端或后续素材执行器。
- 参考图只锚定人物身份、服装、环境和画风，不锁定静态姿势、人物画面位置或原始机位；关键帧生成的是
  “动作发生前一瞬”，为后续人物表演保留构图空间。摄影机始终留在同一行动轴一侧；同场对话中角色的
  左右位置和视线方向由稳定角色槽位决定，不随镜号随机交换。
- 实际提交的关键帧提示词、视频提示词和参考板哈希会写入该 turn 的 `.mp4.request.json`，便于审计和续跑失效判断。
- 封面不复用普通片头卡：优先选择人物较多且靠前的冲突关键帧作为参考，独立调用生图后端重新构图；
  可见文字只允许章节主标题艺术字、小说名和集数角标，禁止把旁白、台词或字幕写到封面上。
- 对白逐字写入视频提示词，并把最终锁定音频作为 `reference_audio`；API 后端使用 `sd2.5`，离线后端
  使用 MiniMax H3 Ref2VA。H3 每个镜头都接收专用表演音轨：画面内可见对白保留原声，
  旁白、内心声和画外对白变成等长静音，因此不会让画中人物替旁白张嘴。最终仍重新混入
  完整锁定 TTS；不执行口型截图、SyncNet、LatentSync 或闭嘴尾帧修复。
- LatentSync 被硬禁用；旧口型环境变量或包含 `latentsync` 的视频模型/命令会在启动时被拒绝。
- 生产成功必须同时通过媒体、字幕结构、字幕像素烧录和逐句 ASR 门禁。
- 输出固定为 1080×1920、25/30 fps、H.264/AAC MP4，以及 1080×1920 JPEG 封面和结束画面。

运行 `novel-manga contract` 可取得 `StoryBible`、`ChapterDiagnosis`、内含 `ShowrunnerPlan` 的 `EpisodePlan`、
`ScriptQualityReport`、`SeriesState` 和完整生产计划 JSON Schema，交给任意模型或外部编排器使用。

只验证规划模型、不调用生图、生视频或 TTS 时，使用 `plan`：

```bash
export NOVEL_PLANNER_BACKEND=openai-compatible
export NOVEL_LLM_BASE_URL=http://127.0.0.1:18001/v1
export NOVEL_LLM_API_KEY=local
export NOVEL_LLM_MODEL=qwen3.5-27b-awq-local
export NOVEL_LLM_MAX_TOKENS=6144
export NOVEL_LLM_DISABLE_THINKING=1
export NOVEL_REQUEST_TIMEOUT=600

uv run novel-manga plan /input/novel.txt \
  --novel-id demo --title 小说名 --output outputs/plans
```

输出包含 `story_bible.json`、逐集 `episode_NNN_diagnosis.json`、`episode_NNN_plan.json`、
`episode_NNN_script_quality.json`、`episode_NNN_series_state.json` 和不含凭据的
`planning_manifest.json`。OpenAI-compatible 后端按“章节事实诊断 → 独立 Showrunner → 剧本 → 独立审稿 → 连续性状态更新”
五阶段运行；命令规划后端使用同一五阶段契约，确定性后端生成带 warning 的保守 Showrunner 回退；所有后端都必须经过同一组事件覆盖、篇幅、因果和章末边界硬门禁。
模型输出不合格时，控制器把结构化错误反馈给模型并限次修订；默认最多修订 2 次，通过
`NOVEL_PLANNER_MAX_REVISIONS=0..2` 配置。达到上限仍不合格会在任何 TTS、生图或生视频调用前失败。

对于超过 3000 个有效字的章节，默认门禁要求至少 800 字左右的有效剧本、18 个 Turn、16 个镜头；
章节诊断为 dense 时要求至少 20 个 Turn。所有 critical 事件必须映射到分镜和改编账本，结尾必须落在
当前章最后的关键高潮或结果。`series_state` 中新增或改变的事实及跨集信息状态必须携带当前章原文证据，
防止把后文秘密写入当前集。

媒体生成前可独立执行引用与说话人校验：

```bash
uv run novel-manga validate-plan /input/novel.txt \
  --bundle outputs/plans/demo --novel-id demo --title 小说名
```

若模型把同一角色写成简称或别名，可先运行
`uv run novel-manga repair-plan --bundle outputs/plans/demo`，将其归一到故事圣经中的固定资产名。

最后可在不调用任何媒体 API 的情况下，确认规划能编译成逐镜头生产任务：

```bash
uv run novel-manga compile-plan /input/novel.txt \
  --bundle outputs/plans/demo --novel-id demo --title 小说名
```

## 离线预览

```bash
uv sync --extra dev

uv run novel-manga inspect examples/短篇示例.txt \
  --novel-id 1 --title 雨夜来信

uv run novel-manga generate examples/短篇示例.txt \
  --novel-id 1 --title 雨夜来信 \
  --provider mock --admission-mode preview --output outputs

uv run pytest
```

`mock + preview` 只验证工程闭环，报告中 `submission_eligible=false`，不能作为参赛成片。

## 生产运行

PhanRouter 负责生图/生视频时：

```bash
# PHANROUTER_API_KEY 由 shell 或密钥管理系统注入
export NOVEL_TTS_COMMAND='/models/adapters/qwen_tts_adapter'
export NOVEL_ASR_COMMAND='/models/adapters/asr_adapter'
export NOVEL_VIDEO_WORKERS=2

uv run novel-manga generate /input/novel.docx \
  --novel-id 1 --title 小说名 \
  --provider phanrouter --admission-mode production --output /output
```

规划器默认按以下优先级选择：`NOVEL_PLANNER_COMMAND`、OpenAI-compatible chat-completions、确定性规则。
因此 Qwen、DeepSeek、Claude 包装服务或任意本地模型都可以接入，不要求特定厂商。Qwen 在这里是
受 Python 状态机约束的规划 Agent：它负责章节诊断、剧本、独立审稿、连续性状态和校验失败后的修订；章节切割、资产缓存、
并发、GPU 调用、合成和准入仍由确定性控制器执行。

仓库同时提供常驻的 Qwen3-TTS OpenAI-compatible 适配器，避免每个 turn 重复加载模型：

```bash
CUDA_VISIBLE_DEVICES=0 QWEN_TTS_API_KEY=local-dev \
  /path/to/qwen-env/bin/python scripts/qwen_tts_openai_server.py \
  --model-dir /models/Qwen3-TTS-12Hz-1.7B-CustomVoice --port 18003

export NOVEL_TTS_BASE_URL=http://127.0.0.1:18003/v1
export NOVEL_TTS_API_KEY=local-dev
export NOVEL_TTS_MODEL=Qwen3-TTS-12Hz-1.7B-CustomVoice
export NOVEL_VOICE_MAP_JSON='{"narrator":"Uncle_Fu","林晚":"Serena"}'
```

服务实现 `GET /ready`、`GET /v1/models` 和 `POST /v1/audio/speech`，单进程只加载一次模型并串行保护
GPU 推理；小说主流水线仍可让图像/视频按 `NOVEL_VIDEO_WORKERS=2` 并发。

角色音色不写在作品脚本中。使用 `NOVEL_VOICE_MAP_JSON` 按角色名配置，例如：

```bash
export NOVEL_VOICE_MAP_JSON='{"narrator":"mature_male","林晚":"warm_female"}'
```

未显式配置的角色由资产工厂分配稳定的后备音色，并写入 `series_assets/manifest.json`，后续各集复用。

生产模式若缺少 `NOVEL_ASR_COMMAND` 会立即拒绝启动。口型检查和口型修复不属于生产准入。

## 任意模型的命令协议

命令由环境变量配置，程序使用参数数组调用，不经过 shell。命令必须把结果写入 `--output` 指定路径。

### 规划器

```text
$NOVEL_PLANNER_COMMAND \
  --operation build_bible|diagnose_episode|plan_showrunner|plan_episode|review_episode|update_series_state \
  --input request.json --output response.json
```

`build_bible` 保留兼容的 v2 请求；逐章编剧阶段使用 `contract=novel-manga-planner/v4`，请求携带当前章、
上一集状态、故事圣经、当前阶段要求和 JSON Schema。响应必须严格符合对应 Schema。若校验失败，后续请求
增加 `repair={revision,previous_response,validation_errors}`。命令适配器只需包装任意本地大模型或远程模型，
阶段编排、重试上限和媒体阻断由 Python 控制器负责。
配置 `NOVEL_PLANNER_BACKEND=command` 可强制使用该后端。

### 本地生图、生视频和 TTS

选择 `--provider command` 后需要：

```text
$NOVEL_IMAGE_COMMAND --prompt TEXT --width 1080 --height 1920 \
  [--reference FILE] --output IMAGE

$NOVEL_VIDEO_COMMAND --prompt TEXT --image FILE --duration SECONDS \
  --fps 25 --width 1080 --height 1920 \
  --reference-audio AUDIO [--additional-image FILE ...] --output VIDEO

$NOVEL_TTS_COMMAND --text TEXT --voice VOICE \
  [--instructions TEXT] [--speed 1.15] --output AUDIO.wav
```

这三个适配器可以包装任意本地模型、HTTP 服务或任务队列。视频适配器必须真正接收
`--reference-audio`；最终合成仍使用锁定的原始 TTS 音频。
本地 MiniMax H3 适配器还支持重复的 `--additional-image`。生产默认使用
`NOVEL_LOCAL_VISUAL_STRATEGY=adaptive`：普通单角色组把角色定妆图作为 Picture 1、空场景资产作为
Picture 2；无人物空镜直接使用场景资产；多人站位、人物/道具交互、结果揭示和高潮镜头自动回退剧情
关键帧。`keyframe` 可恢复旧式逐组关键帧，`h3-direct-single-character` 保留为严格的单角色对照模式。

### 对齐和 ASR

```text
$NOVEL_ALIGN_COMMAND --unit-id ID --audio AUDIO --text TEXT --output result.json
$NOVEL_ASR_COMMAND --unit-id ID --audio AUDIO --text TEXT --output result.json
```

对齐器可选；未配置时使用 ffmpeg 静音检测得到整句边界，再按字符权重分页，证据明确标为
`coarse_audio_bounds_with_character_weighted_pages`。对齐器输出格式：

```json
{
  "backend": "aligner-name@revision",
  "speech_start": 0.12,
  "speech_end": 2.84,
  "events": [{"start": 0.12, "end": 1.4, "text": "第一行"}]
}
```

ASR 最少返回 `hypothesis` 和 `backend`。程序先检查锁定 TTS 以决定是否逐句重试；最终合成完成后，再从
交付 MP4 按 turn 抽取实际音轨生成正式 `asr_report.json`。程序自行规范化文本、计算逐句及整集 CER，
不能用整集平均值掩盖坏句。

人脸一致性报告使用角色资产与对白关键帧的轻量漫画肖像区域相似度代理。它可以提示明显换脸，
但不是生物识别人脸置信度，不参与准入，也不会触发重生图或重生成视频。

正式字幕在所有片段合成后生成：程序根据锁定台词和音频对齐事件写出 `subtitles.ass`，再用 ffmpeg
直接烧录到最终 MP4。字幕使用描边而不是黑色矩形底框，最多两行，位于竖屏底部安全区。

## 重试与恢复

- TTS 逐句运行 ASR；坏句最多按 `NOVEL_MAX_UNIT_ATTEMPTS` 重试，默认 2。
- 视频只因后端失败或策略拒绝做有上限的工程重试；不会因口型抽检而重试。
- 带旧口型后处理字段或 LatentSync 来源标记的视觉缓存不会被新版运行时复用。
- 请求身份由文本、音色、提示词、参考资产、参考音频、模型/命令摘要共同决定；身份不变才复用。
- 旧版本和失败尝试保留在 `work/*_attempts/`，不会覆盖唯一已知素材。
- 旧版只有容器 QC 的 `qc_report.json` 不会被当作新版准入报告复用。

## Docker

API 版使用默认 `Dockerfile`；完全离线单 A100 版使用 `Dockerfile.offline`。两者构建的是同一个包和
同一个 HTTP 接口，不再维护离线专用的小说脚本或渲染分支。离线模型挂载及版本契约见
[`docs/offline-single-image.md`](docs/offline-single-image.md)。

```bash
docker build -t novel-manga-video:0.12.0 .

docker run --rm --gpus 'device=0' --cpus 4 --memory 32g -p 8080:80 \
  -v /path/to/output:/output \
  -v /path/to/models:/models:ro \
  -e NOVEL_PROVIDER=phanrouter -e NOVEL_ADMISSION_MODE=production \
  -e PHANROUTER_API_KEY \
  -e NOVEL_PLANNER_COMMAND -e NOVEL_TTS_COMMAND \
  -e NOVEL_ASR_COMMAND -e NOVEL_ALIGN_COMMAND \
  novel-manga-video:0.12.0
```

镜像包含完整 Python runtime 和诊断脚本，但不包含 `.codex`；删除 Codex 环境不影响执行。模型、权重和
适配器通过 `/models` 挂载。需要在镜像内直接使用旧 CLI 时显式覆盖入口，例如：

```bash
docker run --rm --entrypoint novel-manga novel-manga-video:0.12.0 --help
```

## 输出目录

```text
/output/<novel_id>/
├── 说明文件.json
├── manifest.json
├── story_bible.json
├── series_assets/
│   ├── manifest.json
│   ├── characters/<id>/{spec.json,turnaround.jpeg,expressions.jpeg}
│   └── locations/<id>/{spec.json,establishing.jpeg}
└── <video_id>/
    ├── <video_id>.mp4
    ├── <video_id>_cover.jpeg
    ├── <video_id>_ending.jpeg
    ├── episode_plan.json
    ├── production_plan.json
    ├── content_trace.json
    ├── alignment_report.json
    ├── tts_asr_report.json
    ├── asr_report.json
    ├── face_consistency_report.json
    ├── visual_generation_report.json
    ├── media_qc_report.json
    ├── admission_report.json
    ├── qc_report.json
    └── work/{turn_audio,keyframes,raw_video,segments,subtitles.ass,joined.mp4,...}
```

`manifest.json` 始终保留每章的记录和生成状态。只有新版 `qc_report.json` 的全部必需检查为 `passed`
时，生产集才会标记为 `succeeded`。
