# 薄流水线（thin pipeline）

一章小说 → 一次模型调用出分镜剧本 → 确定性打包成 ≤30 秒的 Seedance 2.5 片段 → 资产卡 + 视频 + ASR + 拼接。
不经过 `pipeline.py` 的 v5 规划链，也不改 `src/`；只读复用 ingest、models、providers、render、qc 和 ASR 辅助函数。
2026-09-02/03 用它交付了《焚天记》前 10 集两套：2D 竖屏（每集 64–111 秒）和 3D 横屏（71–110 秒），后者只换了画风/画幅变量。

硬门只有三条：原文引用逐字且 8 段全覆盖、角色与说话人在圣经内、整集估算 ≤105 秒；渲染侧只拦人声能量和 CER ≤ 0.5（一次重生成）。其余全部只报告。

## 新小说操作手册

前提：gpu16 上仓库 `.env` 已配好（NOVEL_LLM_* 给圣经生成，QWEN38_LOCAL_* 给章节规划，PHANROUTER_* 给图和视频，NOVEL_ASR_COMMAND / NOVEL_SENSEVOICE_MODEL_DIR 给 ASR），Qwen 容器 `novel-manga-qwen38-vlm` 在跑。

```bash
cd /mnt/disk1/zengzhitao/novel-manga-video
set -a; source .env; set +a
export PYTHONPATH=src:scripts
```

1. **准备原文**：一个 .md/.txt（.docx/.pdf 也行），每章以"第X章 标题"独立一行开头；一章就是一集。放到 `inputs/<书名>.md`。
2. **生成圣经并建目录**（一次模型调用，几分钟，不花钱）：
   ```bash
   .venv/bin/python scripts/build_bible_thin.py inputs/斗破苍穹-前10章.md --novel-id doupo-2d --title 斗破苍穹 --style 2d --frame 9:16
   ```
   产物在 `outputs/doupo-2d/`：`story_bible.json`、`story_bible.md`（人读）、`profile.json`、`visual_grammar.json`（模板，`location_time` 已按地点预填键名）、`novel.json`（记录原文路径和切章结果）。
   **审 `story_bible.md`**：名字、主要角色是否齐、外貌服装有没有原文依据、地点能否画成空场、切章对不对。直接改 `story_bible.json`。
3. **填视觉语法**：`visual_grammar.json` 的 `location_time` 给每个地点写时间和主光源（如"夜，银月为主光，灯火为次光"），否则规划器会自己决定昼夜，容易与地点卡冲突。`style_line` 留空即用 profile 的画风句。
4. **第一章剧本**（不花钱）：
   ```bash
   .venv/bin/python scripts/thin_batch.py --novel-dir outputs/doupo-2d --chapters 1 --stage plan
   ```
   看 `outputs/doupo-2d/doupo-2d_1/chapter_script.md` 和 `clip_plan.md`（每段完整提示词）。要改方向就用 `--notes-json`（`{"1": "导演意见"}`）加 `--replan` 重来。
5. **建卡**（花图片钱，角色 ×2 张 + 地点 ×1 张）：
   ```bash
   .venv/bin/python scripts/thin_batch.py --novel-dir outputs/doupo-2d --chapters 1 --stage assets
   ```
   看 `outputs/doupo-2d/series_assets/cards_sheet.jpg`。不满意的卡删掉对应 jpeg 和两个侧车文件再跑一次；像真人照片的 3D 卡不用管，渲染时会自动重画。
6. **第一集成片**（花视频钱；无人值守场景见下一节，四五六步合成一条命令）：
   ```bash
   .venv/bin/python scripts/thin_batch.py --novel-dir outputs/doupo-2d --chapters 1
   ```
   满意后跑其余章：`--chapters 2-10 --parallel 3`。规划串行（Qwen 单序列），渲染按 `--parallel` 并行；已完成的集自动跳过，重复执行同一条命令永远安全。结束打印一张表并写 `batch_report.json/.md`。
7. **交付**：成片 `outputs/<id>/<id>_N/<id>_N.mp4`，封面 `<id>_N_cover.jpeg`。传 Mac 用 rsync 走 tailscale，逐文件 md5 校验。

同一本书换画风或画幅：另起一个 novel-id（如 `doupo-3d-h`，`--style 3d --frame 16:9`），卡片和片段缓存都按目录走，两套互不影响。

## 无人值守（Docker / 榜单场景）

三个检查点各有一个自动判定（`scripts/thin_review.py`，本地 Qwen3.8 视觉模型答固定问卷，严格 JSON，温度 0），修复都有上限、绝不停下来等人：

| 检查点 | 判定 | 自动修复（各一次） | 修不好 |
|---|---|---|---|
| 圣经 | 逐章抽取人物并计数，泛称（少年、老者、父亲…）一律不算；缺失的具名或有台词的人物 | `build_bible_thin.py --fill`：对照已有角色去重、外貌服装必须具体，否则不入库 | 写进 `bible_review.json` 的 needs_human |
| 角色卡/地点卡 | 像真人程度、是否符合设定（不看道具姿势）、两张是否同一人、有无文字或多余人物；地点卡有无人物、昼夜是否与 `location_time` 一致 | 像真人 ≥ 0.6 → 动画化重画；其余不符 → 删卡重建 | 留在 `series_assets/cards_review.json` 和交付报告 |
| 成片 | 每段 4 帧 + 该段角色卡 + 预期台词 + 语音识别文本：串人、多出角色、地点昼夜、文字水印、崩坏 | 判 fail 的段把审片给出的修正句写进 `review_feedback.json`，提示词加【导演修正】重生成一次（其他段走缓存） | 留在 `episode_review.json` 和交付报告 |

```bash
.venv/bin/python scripts/build_bible_thin.py <原文> --novel-id X --title 书名 --style 2d --fill
.venv/bin/python scripts/thin_batch.py --novel-dir outputs/X --chapters 1-10 --parallel 3 --unattended
```

结束写 `outputs/X/delivery_report.md`：圣经补了谁、卡片修了什么还剩什么标记、每集时长/语音门/自动修正段/剩余标记、交付文件列表。`--review-only` 只判定不花钱修复，用来先看判定对不对。

在焚天记 20 集上的校验（2026-09-03）：旧版第四集的串人段被准确指出（少女穿成了另一角色的白袍金星和绿耳坠）；被 Seedance 拒收的两张原卡判为 0.95/0.85，重画后 0.35；2D 全套卡和 3D 两集好片零误报；圣经判定抓到原文出现 24 次却不在圣经里的丹老。判定是模型，仍会有漏和误，所以每项只修一次、其余标记给人。

## 长篇（几百到几千章）

一本圣经一直长，不按卷另写：

- **起步**：`build_bible_thin.py --bible-chapters 5` 只用前 5 章生成初始圣经（老流程的 12000 字摘录对 3000 章等于只看了三个碎片）。
- **逐章增长（自动，默认开）**：批跑每章规划前抽取本章人物和地点，新的具名人物、新地点追加到同一个 `story_bible.json`，编号只增不改；称呼类人物只写建议，记录在 `bible_growth.json`。
- **别名**：判定认定是已有角色别称或昵称的名字写进 `bible_aliases.json`（如 逍遥散仙 → 沈玄川），后续章节的增长永久排除，规划器和打包器把别名映射回正名，不会出现一人两卡。人工也可以直接往这个文件里加。
- **逐章切片**：规划器只把主角组、本章提到的人物和地点放进提示词和 JSON 枚举，整本圣经从不整个送进模型。
- **前情**：规划器把自己写的每章摘要存进 `recap.json`，下一章带最近 5 章的摘要，只用于连续性，不拍前情。
- **按需建卡**：渲染只建本集引用到的卡（旧逻辑会把编号前缀全建一遍）。
- **流水式**：`--stage all` 规划完一章就送去渲染，同时规划下一章；`--parallel` 控制同时渲染的集数。
- **按卷复核**：每 `--volume-size`（默认 50）章写一份 `volume_review_NNN.md`：本卷新增人物地点、待确认的称呼类建议、标记。
- **清理**：`--prune` 在一集拼完后删中间音频、过期片段和审片抽帧，每集从约 230 MB 降到约 50 MB；片段视频和 ASR 结果保留供缓存。
- **短章**：少于 `--min-chapter-chars`（默认 300）字的章（作者的话之类）跳过。

量级：规划每章 3–5 分钟串行（本地 Qwen 单序列），渲染每集 12–15 分钟、5 集并行，3000 章约两周，视频约 13,500 段，是最大的一笔开销。建议按卷交付。

## 屏幕上的聊天消息（群聊类小说）

默认画面里不允许任何可读文字（视频模型画中文易乱码）。群聊是剧情主体的书用 `chat_message`：规划器把消息标成不发声的 turn（发消息的人 + 消息原文，逐字取自原文、≤24 字，一个阶段最多三条），打包器写成"屏幕内容：手机屏幕特写正对镜头，微信群聊界面依次弹出消息气泡，文字为清晰可读的简体中文、与下列内容逐字一致：【谁】「消息」"，并加 <手机消息提示音>；【保持一致】改为"除屏幕上指定的聊天消息外不出现其他文字"。审片多问一项 chat_text_ok（屏幕文字是否为清晰简体中文且与预期一致，允许截断，不允许乱码），不合格带"消息文字必须逐字一致、无乱码"重生成一次。视觉语法禁忌里的可读文字一条要写明"手机屏幕上剧本指定的聊天消息除外"。消息不进字幕、不进语音门。

## 单集手动操作

批跑脚本内部就是这三条命令，需要单独调一集时直接用：

```bash
.venv/bin/python scripts/plan_chapter_thin.py <novel.md> --novel-id <id> --title <书名> --episode-index N \
  --bible outputs/<id>/story_bible.json --output-root outputs [--max-redo 2] [--min-seconds 85] [--notes "导演意见"]
.venv/bin/python scripts/build_clip_plan_thin.py --episode-dir outputs/<id>/<id>_N --bible outputs/<id>/story_bible.json
.venv/bin/python scripts/render_clips_thin.py --novel-dir outputs/<id> --episode <id>_N --workers 4 [--assets-only]
```

三个脚本都接受 `--style 2d|3d --frame 9:16|16:9`，命令行优先于 `profile.json`。所有远程任务都有 `.task.json` 侧车，重跑只补缺的、变了的：片段按提示词+参考图缓存，重规划会自动作废旧的片段计划和成片报告。

渲染器退出码：0 通过；2 有段没过语音门或薄 QC（成片照常拼出，报告里标明）；3 有段没生成出视频（不拼接，重跑只补那几段）。

## 文件

| 文件 | 作用 |
|---|---|
| `scripts/build_bible_thin.py` | 新书入口：一次模型调用出圣经，写 profile / 视觉语法模板 / 人读版圣经 / novel.json。 |
| `scripts/thin_batch.py` | 一键批跑：规划（串行）→ 建卡 → 渲染（并行）→ 报表；按状态跳过已完成的，每集一把锁。 |
| `scripts/thin_review.py` | 三个自动判定（圣经补漏、卡片、成片）和卡片修复；`--unattended` 时由批跑调用，也可单独跑。 |
| `scripts/plan_chapter_thin.py` | 一章一次调用（Qwen3.8 本地服务，先思考摘要再严格 JSON）。硬门三条；返修时给具体数字目标、识别原样重发、改写引用附最接近原句。 |
| `scripts/build_clip_plan_thin.py` | 阶段打包成 ≤30 秒、≤6 阶段的片段，按官方 Seedance 2.5 模板写提示词（【生成目标】、逐图 用于/不采用、【视觉语法】、【阶段n·景别】、【保持一致】、【不要】），只报告的克制检查。 |
| `scripts/render_clips_thin.py` | 资产卡（只到本集引用到的编号）、Seedance 请求（参考图 base64 内嵌）、静音切块 + SenseVoice、硬门、硬切拼接、剧本原句字幕、封面/结束卡、媒体 QC；接口故障自愈见下。 |
| `scripts/thin_asr_segments.py` | 在 ASR venv 里一次加载模型识别多个语音块。 |
| `scripts/thin_profile.py` | 画风/画幅变量：`STYLE_VISUAL` 各画风的卡片描述，`FRAMES` 各画幅的画布、Seedance 比例和构图句。 |
| `configs/templates/` | `profile.json`、`visual_grammar.json` 模板。 |
| `configs/fentian/` | 焚天记的两份视觉语法（2D 竖屏、3D 横屏）、profile 范例、`clip_overrides.example.json`（单段修正：限制可见角色、身份区分、额外禁忌）。 |

## 画风与画幅变量

`outputs/<novel>/profile.json`：`{"style": "2d" | "3d", "frame": "9:16" | "16:9"}`。

- `style`：圣经 `visual_style` 在运行时被 `STYLE_VISUAL[style]` 替换（只改内存），定妆卡工厂据此走 2D 或 3D 分支；剧本和提示词里的画风名；`style_line` 留空时的默认画风句。3D 文案里不能出现"二维/卡通/赛璐璐/2d"任何一个词——工厂先查这几个词，圣经里一句"禁止二维"就会把整套卡画成二维。
- `frame`：画布、Seedance `ratio`、建立镜头图比例（角色卡永远竖版）、提示词里的构图句、封面/结束卡的横版排版。

## 接口问题与对策（都在渲染器里，正常路径不触发）

- 图床迁移后旧链接返回 HTML 页 → 参考图一律 base64 内嵌。
- 下载不校验，HTML 或截断响应被存成 `.jpeg` → 生成前清掉小于 20 KB 或解不出的卡；一张坏定妆卡会让依赖它的表情卡以无关的错误失败。
- 图片接口临时故障"图片生成失败，请稍后重试" → 6 轮、每轮退避 90 秒。
- 3D 卡偶尔接近真人照片，Seedance 以 `InputImageSensitiveContentDetected.PrivacyInformation` 拒收整段 → 找出该段引用、且没被任何成功片段用过的卡（`series_assets/.privacy_ok.json` 跨进程记录），原卡挪成 `.photoreal-rejected.jpeg`（连同侧车），按"明显动画化的 3D 国漫角色"重画后在同一 attempt 内重提一次；重画串行、每张卡最多一次、提交前等待正在重画的卡。
- 本机 ffmpeg 4.4 的多输入 xfade 链会把某段冻在首帧 → 硬切 concat。
- 严格 JSON 模式偶尔陷入无限空白 → JSON 步 max_tokens 9000，快速失败进返修。
- Seedance 会加即兴群众杂音 → 字幕按 ASR 定时、用剧本原句，对不上的块不出字幕。
- 一段里两名相貌相近且都有参考图的少女会串脸 → 不说话且未被画面点名的角色不给参考图；已知案例用 `clip_overrides.json` 单段修。
- 一段出错（网络、ASR）不再拖垮整集：按段记录，成片报告 `status: clips_failed`，重跑只补那几段。
- 图片接口的内容审核拒画某张卡（现代题材女性角色偶发）→ 用去掉敏感词并加"端庄得体、衣着完整"的提示词重画一次，再拒就立即报错，不再空转 6 轮。
- 现代题材的 3D 卡容易被画成写实真人质感 → 建卡后的自动审卡会判为像真人并动画化重画，每个角色多两张图；这是预期行为。

## 经验

- 模型对"秒数"不敏感，对"几段、几个阶段、多少字"很听话；调长度用具体数字。告诉模型的上限和代码检查的上限来自同一组常量，别再出现"写够了仍被拒"。
- 每段引用 3 个及以上角色时每人只给一张定妆卡。
- 隐私重画会改变角色长相（更圆、更低龄）。新书建卡后先看 `cards_sheet.jpg`，像真人的卡可以先删掉重建，比等渲染时被拒再重画更省。
- 焚天记 2D 集的圣经文件里仍写着 3D，实际由 profile 说了算；提示词画风句可用 `style_line` 覆盖，不改圣经和已有卡片。
