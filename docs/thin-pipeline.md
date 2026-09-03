# 薄流水线（thin pipeline）

一章小说 → 一次模型调用出分镜剧本 → 确定性打包成 ≤30 秒的 Seedance 2.5 片段 → 资产卡 + 视频 + ASR + 拼接。
不经过 `pipeline.py` 的 v5 规划链，也不改 `src/`；只读复用 ingest、models、providers、render、qc 和 ASR 辅助函数。
2026-09-02/03 用它交付了《焚天记》前 10 集（每集 64–111 秒，共约 15 分钟）；同日又用同一套脚本、只换画风/画幅变量出了 3D 国漫横屏版。

## 文件

| 文件 | 作用 |
|---|---|
| `scripts/plan_chapter_thin.py` | 一章一次调用（Qwen3.8 本地服务，先 35 秒思考摘要再严格 JSON）。硬门只有三条：原文引用逐字且 8 段全覆盖、角色与说话人在圣经内、整集时长 ≤105 秒；最多返修一次。其余全部只报告。 |
| `scripts/build_clip_plan_thin.py` | 把阶段打包成 ≤30 秒、≤6 阶段的片段，按官方 Seedance 2.5 模板写提示词：【生成目标】、逐图 用于/不采用 绑定、【视觉语法】、【阶段n·景别】（开始时/机位/光源/主要事件/声音/结束时）、画面呈现/镜头采用/声音包括、【保持一致】、【不要】。自带只报告的克制检查。 |
| `scripts/render_clips_thin.py` | 资产卡（只到本集引用到的编号）、Seedance 请求（参考图 base64 内嵌）、静音切块 + SenseVoice、硬门（人声能量、CER ≤ 0.5，一次重生成）、硬切拼接、剧本原句字幕、封面/结束卡、媒体 QC。 |
| `scripts/thin_asr_segments.py` | 在 ASR venv 里一次加载模型识别多个语音块。 |
| `scripts/thin_profile.py` | 画风/画幅变量：读 `outputs/<novel>/profile.json`，三个脚本共用；`STYLE_VISUAL` 里是各画风的定妆卡/圣经描述，`FRAMES` 里是各画幅的画布、Seedance 比例和构图句。 |
| `scripts/plan_all_ch2_10.sh` 等八个 | 批跑驱动脚本（2D 竖屏四个、3D 横屏四个：规划循环、渲染循环、串行收尾、并行补渲染），含当日绝对路径，仅作范例。 |
| `configs/fentian/visual_grammar_3d_h.json`、`profile.3d-16x9.json` | 3D 横屏版用的视觉语法和 profile 范例。 |
| `configs/fentian/visual_grammar.json` | 全书视觉语法：四轴 + 禁忌 + 各地点时间与主光源。放到 `outputs/<novel>/` 下即自动生效。 |
| `configs/fentian/clip_overrides.example.json` | 单段修正范例：限制可见角色、身份区分、额外禁忌。放到某集目录后重新打包，只有该段提示词变化。 |

## 跑法

```bash
cd /mnt/disk1/zengzhitao/novel-manga-video
set -a; source .env; set +a
export PYTHONPATH=src NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
export PHANROUTER_INLINE_REFERENCE_IMAGES=1   # 必须：图床链接会过期/迁移

.venv/bin/python scripts/plan_chapter_thin.py <novel.md> --novel-id <id> --title <书名> --episode-index N \
  --bible outputs/<id>/story_bible.json --output-root outputs [--max-redo 2] [--min-seconds 85] [--notes "导演意见"]
.venv/bin/python scripts/build_clip_plan_thin.py --episode-dir outputs/<id>/<id>_N --bible outputs/<id>/story_bible.json
.venv/bin/python scripts/render_clips_thin.py --novel-dir outputs/<id> --episode <id>_N --workers 4
```

三个脚本都接受 `--style 2d|3d --frame 9:16|16:9`，命令行优先于 `profile.json`；不写则按 2D 竖屏（即前 10 集的原样）。

## 画风与画幅变量

`outputs/<novel>/profile.json`：`{"style": "2d" | "3d", "frame": "9:16" | "16:9"}`。

- `style` 决定：圣经 `visual_style` 在运行时被 `STYLE_VISUAL[style]` 替换（只改内存，不改文件），定妆卡工厂据此走 2D 或 3D 分支；剧本和提示词里的画风名；`visual_grammar.json` 没写 `style_line` 时的默认画风句。注意 3D 文案里不能出现"二维/卡通/赛璐璐/2d"任何一个词——工厂先查这几个词，圣经里一句"禁止二维"就会把整套卡画成二维（第一版 3D 就是这么翻车的）。
- `frame` 决定：画布尺寸、Seedance `ratio`、建立镜头图的比例（角色卡永远竖版）、提示词里的"镜头采用……"构图句、封面/结束卡的横版排版。
- 换画风或画幅就换一个 novel 目录（卡片、片段缓存都按目录走），已有的集不受影响。

同一本书两种画风的目录：`outputs/fentian-thin-v4`（2D 竖屏）和 `outputs/fentian-3d-h-v1`（3D 横屏）。

产物在 `outputs/<id>/<id>_N/`：`chapter_script.md`（人读的剧本）、`clip_plan.md`（每段完整提示词）、`thin_media_report.json`、`media_qc_report.json`、成片与封面。所有远程任务都有 `.task.json` 侧车，重跑只补缺的、变了的。

## 已知的接口问题与对策（都在代码里）

- 图床迁移后旧链接返回 HTML 页 → 参考图一律 base64 内嵌。
- 下载不校验，HTML 或截断响应被存成 `.jpeg` → 生成前先清掉小于 20 KB 或解不出的卡；一张坏定妆卡会让依赖它的表情卡以无关的错误失败。
- 图片接口临时故障返回"图片生成失败，请稍后重试" → 6 轮、每轮退避 90 秒。
- 本机 ffmpeg 4.4 的多输入 xfade 链会把某段冻在首帧 → 硬切 concat。
- 严格 JSON 模式偶尔陷入无限空白 → JSON 步 max_tokens 9000，快速失败进返修。
- Seedance 会加即兴群众杂音 → 字幕按 ASR 定时、用剧本原句，对不上的块不出字幕。
- 一段里两名相貌相近且都有参考图的少女会串脸 → 不说话且未被画面点名的角色不给参考图；已知案例用 `clip_overrides.json` 单段修。
- 3D 卡偶尔画得接近真人照片，Seedance 以 `InputImageSensitiveContentDetected.PrivacyInformation` 拒收整段 → 渲染器找出该段引用、且没在其他成功段里用过的角色卡，把原卡挪成 `.photoreal-rejected.jpeg`（连同 `.task.json` 侧车，否则接口会因请求不匹配拒绝重画），以它为参考按"明显动画化的 3D 国漫角色"重画，再重提一次。3D 画风文案里也加了反写实的约束。

## 经验

- 模型对"秒数"不敏感，对"几段、几个阶段、多少字"很听话；调长度用具体数字。
- 每段引用 3 个及以上角色时每人只给一张定妆卡。
- 2D 集的圣经 `visual_style` 文件里仍写着 3D 国漫，实际卡片是二维赛璐璐；现在由 profile 的 `style` 统一说了算，视频提示词的画风句仍可用 `visual_grammar.json` 的 `style_line` 覆盖，不改圣经和已有卡片。
