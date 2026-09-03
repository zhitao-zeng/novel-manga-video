# 薄流水线（thin pipeline）

一章小说 → 一次模型调用出分镜剧本 → 确定性打包成 ≤30 秒的 Seedance 2.5 片段 → 资产卡 + 视频 + ASR + 拼接。
不经过 `pipeline.py` 的 v5 规划链，也不改 `src/`；只读复用 ingest、models、providers、render、qc 和 ASR 辅助函数。
2026-09-02/03 用它交付了《焚天记》前 10 集（每集 64–111 秒，共约 15 分钟）。

## 文件

| 文件 | 作用 |
|---|---|
| `scripts/plan_chapter_thin.py` | 一章一次调用（Qwen3.8 本地服务，先 35 秒思考摘要再严格 JSON）。硬门只有三条：原文引用逐字且 8 段全覆盖、角色与说话人在圣经内、整集时长 ≤105 秒；最多返修一次。其余全部只报告。 |
| `scripts/build_clip_plan_thin.py` | 把阶段打包成 ≤30 秒、≤6 阶段的片段，按官方 Seedance 2.5 模板写提示词：【生成目标】、逐图 用于/不采用 绑定、【视觉语法】、【阶段n·景别】（开始时/机位/光源/主要事件/声音/结束时）、画面呈现/镜头采用/声音包括、【保持一致】、【不要】。自带只报告的克制检查。 |
| `scripts/render_clips_thin.py` | 资产卡（只到本集引用到的编号）、Seedance 请求（参考图 base64 内嵌）、静音切块 + SenseVoice、硬门（人声能量、CER ≤ 0.5，一次重生成）、硬切拼接、剧本原句字幕、封面/结束卡、媒体 QC。 |
| `scripts/thin_asr_segments.py` | 在 ASR venv 里一次加载模型识别多个语音块。 |
| `scripts/plan_all_ch2_10.sh` 等四个 | 10 集批跑时用的驱动脚本，含当日绝对路径，仅作范例。 |
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

产物在 `outputs/<id>/<id>_N/`：`chapter_script.md`（人读的剧本）、`clip_plan.md`（每段完整提示词）、`thin_media_report.json`、`media_qc_report.json`、成片与封面。所有远程任务都有 `.task.json` 侧车，重跑只补缺的、变了的。

## 已知的接口问题与对策（都在代码里）

- 图床迁移后旧链接返回 HTML 页 → 参考图一律 base64 内嵌。
- 下载不校验，HTML 或截断响应被存成 `.jpeg` → 生成前先清掉小于 20 KB 或解不出的卡；一张坏定妆卡会让依赖它的表情卡以无关的错误失败。
- 图片接口临时故障返回"图片生成失败，请稍后重试" → 6 轮、每轮退避 90 秒。
- 本机 ffmpeg 4.4 的多输入 xfade 链会把某段冻在首帧 → 硬切 concat。
- 严格 JSON 模式偶尔陷入无限空白 → JSON 步 max_tokens 9000，快速失败进返修。
- Seedance 会加即兴群众杂音 → 字幕按 ASR 定时、用剧本原句，对不上的块不出字幕。
- 一段里两名相貌相近且都有参考图的少女会串脸 → 不说话且未被画面点名的角色不给参考图；已知案例用 `clip_overrides.json` 单段修。

## 经验

- 模型对"秒数"不敏感，对"几段、几个阶段、多少字"很听话；调长度用具体数字。
- 每段引用 3 个及以上角色时每人只给一张定妆卡。
- 圣经 `visual_style` 仍写 3D 国漫，实际卡片是二维赛璐璐；视频提示词里的画风句由 `visual_grammar.json` 的 `style_line` 覆盖，不改圣经和已有卡片。
