# Novel Manga Video

把 `txt / markdown / docx / pdf` 小说转为 9:16 国漫画风漫剧。生产控制器是普通 Python 程序，不依赖
Codex；故事规划、生图、生视频、TTS、ASR 和强制对齐均为可替换 provider。

## 可执行生产协议

主流水线固定执行以下顺序，LLM 不能跳过质量门禁：

```text
小说 → 按原文章节分集 → 系列圣经
     → 单人角色资产 + 无人物场景资产
     → scene → shot → turn
     → 全部逐句音频 + ASR + 对齐
     → 说话人独立近景 + 逐字台词提示词 + 参考音频视频（最多 2 并发）
     → 轻量人脸一致性评分（仅监测，不重生成、不阻断）
     → 无字幕成片 → ASS 烧录 → 媒体/字幕/ASR 最终准入
```

关键约束：

- 有章节时严格一章一集；无章节时只在句子或段落边界按 3,000—6,000 字拆分。
- 角色 `turnaround.jpeg`、`expressions.jpeg` 与场景 `establishing.jpeg` 先生成、全书复用。
- 复用身份和场景资产，不复用不同说话人的完整构图；每个可见对白 turn 都有独立近景。
  对白关键帧只允许输入当前说话人的完整角色资产，禁止把被对话者或其他角色拼入参考板；场景、
  情绪和差异化机位由提示词约束。
- 每个 turn 绑定同一份锁定文本、固定音色、音频、字幕对齐、关键帧和视频片段。
- 实际提交的关键帧提示词、视频提示词和参考板哈希会写入该 turn 的 `.mp4.request.json`，便于审计和续跑失效判断。
- 对白逐字写入 SD2.0 提示词，并把最终锁定音频作为参考音频；不再执行口型截图、SyncNet 或闭嘴尾帧修复。
- LatentSync 被硬禁用；旧口型环境变量或包含 `latentsync` 的视频模型/命令会在启动时被拒绝。
- 生产成功必须同时通过媒体、字幕结构、字幕像素烧录和逐句 ASR 门禁。
- 输出固定为 1080×1920、25/30 fps、H.264/AAC MP4，以及 1080×1920 JPEG 封面和结束画面。

运行 `novel-manga contract` 可取得 `StoryBible`、`EpisodePlan` 和完整生产计划 JSON Schema，交给任意
模型或外部编排器使用。

只验证规划模型、不调用生图、生视频或 TTS 时，使用 `plan`：

```bash
export NOVEL_PLANNER_BACKEND=openai-compatible
export NOVEL_LLM_BASE_URL=http://127.0.0.1:18001/v1
export NOVEL_LLM_API_KEY=local
export NOVEL_LLM_MODEL=qwen3.5-27b-awq-local
export NOVEL_REQUEST_TIMEOUT=600

uv run novel-manga plan /input/novel.txt \
  --novel-id demo --title 小说名 --output outputs/plans
```

输出包含通过 Pydantic Schema 和原文引用/角色/场景校验的 `story_bible.json`、逐集
`episode_NNN_plan.json` 和不含凭据的 `planning_manifest.json`。同一入口可连接任意 OpenAI-compatible
本地或远程模型。模型输出不合格时，控制器把结构化错误反馈给模型并限次修订；默认最多修订 2 次，
通过 `NOVEL_PLANNER_MAX_REVISIONS=0..2` 配置。达到上限仍不合格会在媒体生成前失败。

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
受 Python 状态机约束的规划 Agent：它负责故事圣经、分镜和校验失败后的修订；章节切割、资产缓存、
并发、GPU 调用、合成和准入仍由确定性控制器执行。

角色音色不写在作品脚本中。使用 `NOVEL_VOICE_MAP_JSON` 按角色名配置，例如：

```bash
export NOVEL_VOICE_MAP_JSON='{"narrator":"mature_male","林晚":"young_female"}'
```

未显式配置的角色由资产工厂分配稳定的后备音色，并写入 `series_assets/manifest.json`，后续各集复用。

生产模式若缺少 `NOVEL_ASR_COMMAND` 会立即拒绝启动。口型检查和口型修复不属于生产准入。

## 任意模型的命令协议

命令由环境变量配置，程序使用参数数组调用，不经过 shell。命令必须把结果写入 `--output` 指定路径。

### 规划器

```text
$NOVEL_PLANNER_COMMAND \
  --operation build_bible|plan_episode \
  --input request.json --output response.json
```

请求中包含 `contract=novel-manga-planner/v2`、原文、故事圣经、要求和 JSON Schema；响应必须严格符合
对应 Schema。若校验失败，后续请求增加 `repair={revision,previous_response,validation_errors}`。
配置 `NOVEL_PLANNER_BACKEND=command` 可强制使用该后端。

### 本地生图、生视频和 TTS

选择 `--provider command` 后需要：

```text
$NOVEL_IMAGE_COMMAND --prompt TEXT --width 1080 --height 1920 \
  [--reference FILE] --output IMAGE

$NOVEL_VIDEO_COMMAND --prompt TEXT --image FILE --duration SECONDS \
  --fps 25 --width 1080 --height 1920 \
  --reference-audio AUDIO --output VIDEO

$NOVEL_TTS_COMMAND --text TEXT --voice VOICE \
  [--instructions TEXT] --output AUDIO.wav
```

这三个适配器可以包装任意本地模型、HTTP 服务或任务队列。视频适配器必须真正接收
`--reference-audio`；最终合成仍使用锁定的原始 TTS 音频。

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
烧录到最终 MP4。参考音频视频偶尔会自带错误文字，因此当前渲染器会先在下方字幕安全区覆盖黑色
清理层，再绘制正式 ASS 字幕；这个清理层只在剧情段出现，不影响片头片尾。

## 重试与恢复

- TTS 逐句运行 ASR；坏句最多按 `NOVEL_MAX_UNIT_ATTEMPTS` 重试，默认 2。
- 视频只按锁定提示词、角色参考资产和参考音频生成一次，不因口型抽检而重试。
- 带旧口型后处理字段或 LatentSync 来源标记的视觉缓存不会被新版运行时复用。
- 请求身份由文本、音色、提示词、参考资产、参考音频、模型/命令摘要共同决定；身份不变才复用。
- 旧版本和失败尝试保留在 `work/*_attempts/`，不会覆盖唯一已知素材。
- 旧版只有容器 QC 的 `qc_report.json` 不会被当作新版准入报告复用。

## Docker

```bash
docker build -t novel-manga-video:0.4.0 .

docker run --rm --gpus 'device=0' --cpus 4 --memory 32g \
  -v /path/to/input:/input:ro \
  -v /path/to/output:/output \
  -v /path/to/models:/models:ro \
  -e PHANROUTER_API_KEY \
  -e NOVEL_PLANNER_COMMAND -e NOVEL_TTS_COMMAND \
  -e NOVEL_ASR_COMMAND -e NOVEL_ALIGN_COMMAND \
  novel-manga-video:0.4.0 generate /input/novel.pdf \
  --novel-id 1 --title 小说名 \
  --provider phanrouter --admission-mode production --output /output
```

镜像包含完整 Python runtime 和诊断脚本，但不包含 `.codex`；删除 Codex 环境不影响执行。模型、权重和
适配器通过 `/models` 挂载。

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
