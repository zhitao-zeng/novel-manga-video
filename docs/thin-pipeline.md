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
   产物在 `outputs/doupo-2d/`：`story_bible.json`、`story_bible.md`（人读）、`profile.json`（含自动检测的题材 `genre`，可改或用 `--genre` 指定）、`visual_grammar.json`（模板加题材禁忌，`location_time` 已按地点预填键名）、`novel.json`（记录原文路径和切章结果）；群聊类题材还会生成 `chat_screen.json`。
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

实测量级（诸天万象录 10 集）：规划每章 1.6–5.5 分钟，建卡每张 1–2 分钟串行，出片每集 6–12 分钟（3 集并行时约 5.5 分钟一集），审片每集半分钟。瓶颈是那台单序列的 Qwen 服务：规划、圣经增长、审卡、审片全在它上面，每章约 4 分钟。

**给规划器的角色和地点是裁过的**（规划器 v11）：整本圣经从不整个发过去，几千章下来会是几百个模型用不上的人。一个角色会被提供，当他是主角、本章原文点了他的名或任一别名（别名这一条很关键，旧规则只匹配正名，导致同一个人以网名出现时被当成新角色写进圣经）、或者他在最近 3 集里出过场（这一条让"他/她"指代的人不会丢）。地点同理。出场记录在 `outputs/<novel>/cast_index.json`，每集规划成功后追加；文件不存在时会从已有的 chapter_script.json 现场重建，老书不需要迁移。实测第 36 集提供 21 个角色（圣经里有 91 个），其中 18 个是本章点名、3 个靠最近出场带进来。

**卷摘要防跨卷漂移**：逐章 recap 只回溯 5 章，几百章之后主线会丢。`thin_review.py volume --novel-dir X --first 1 --last 50` 把这一卷的逐章梗概压成一段主线、若干条未了结的线索和人物处境，写进 `outputs/<novel>/volumes.json`；`thin_batch.py` 在每个卷检查点自动调用，并把结果附在 `volume_review_NNN.md` 里。规划器会带上最近两卷的摘要（payload 里的 `previous_volumes_recap`）。一次本地调用约 13 秒，不花钱。

**声音一致性是可以量的**：`asr_models_eval/.venv/bin/python scripts/voice_consistency_thin.py outputs/X` 把每个能归属到单一说话人的语音块做 CAM++ 声纹（模型在 `/mnt/disk1/zengzhitao/models/speaker/`），报三档相似度：同集内、跨集、与其他角色。46 集实测 0.47 / 0.38 / 0.25，同一人的判定线是 0.55——也就是说同一个角色跨集不是同一个声音，连同一集不同片段之间也不是。原因是 Seedance 的请求只收文本和参考图，没有音频参数，每段自己编音色；圣经里的 `voice_profile_id` 是 v5 老流程的死字段。

**成本台账**：`scripts/cost_report_thin.py --novel-dir outputs/X`（或 `--all`）统计实际计费用量——每次 Seedance 尝试的秒数（含被门拒掉的那些，因为一样付了钱）、图片生成次数（按 `series_assets` 下的 task 边车计，重画和审核重试都算）、本地模型调用次数；卡片目录是符号链接时不重复计入。单价填在 `configs/pricing.json`，填了就直接换算成钱，不填只报用量。`--csv` 导出每集明细。

## 提速：快速档、合章、并行规划

- **Qwen 并发**：vLLM 容器原来 `--max-num-seqs 1`，用 `/mnt/disk1/zengzhitao/tmp/qwen_recreate.sh <N>` 重建（旧配置自动存档，传 1 即回滚）。并发 4 时批跑可以 `--plan-parallel 3` 几章同时规划。
- **合章** `--merge N`：每集覆盖 N 个连续章（集号 k = 第 (k-1)N+1 到 kN 章），`--chapters` 此时按集号数。网文短章建议 2。
- **快速档** `profile.json` 的 `tier: fast`（或 `--tier fast`）：规划保留一个 400 字以内的短思考步（去掉后首稿质量大跌，返修反而更多）、最多返修两次、目标按字数放大（每 3000 字约 60 秒，上限 100 秒）、8 个区段都要带到、漏引区段自动记为跳过、超长只记警告、群消息超长截断；每角色一张参考图；480p、语音门不重生成；无人值守时只审卡不审片；圣经每 5 集增长一次。质量档就是默认值。两档共用圣经、卡片和目录，可以先用快速档粗剪整本，再对挑出的集 `--tier quality --rerender` 精修。
- **实测 v14（第 51–70 章 10 集，卡片大多已存在）**：规划 2–4 分钟完成，第 3 分钟开始出片，15 分钟结束（上一版 37 分钟）；单集平均 8.6 分钟，其中等 Seedance 86%、后处理 9%；峰值 23 段同飞、单段平均 273–388 秒；实际每小时约 32 集，池子填满后可到 50 集以上。
- **实测（诸天万象录第 11–30 章，两章一集共 10 集，2026-09-04）**：并行 3 时 10 集 6 分钟规划完；Seedance 480p 每段中位 240 秒，9–13 段同时在飞零限流，但延迟随并发上升（9 段同飞时中位 351 秒），吞吐约为 3 倍并发得 2 倍，甜区 8–12 段；每集渲染器约 10 分钟；新章节的建卡是首轮最大开销（15 个新角色加动画化重画约 40 分钟）。
- **接口拒绝的三种自动处理**：参考图被判真人（错误里 content[N] 从 1 数）→ 精确重画那张（地点卡按空场景重建，角色卡已动画化过的加强一次）；生成结果被输出审核拒（视频或音频，含"疑似版权歌曲"）→ 加合规声明重试一次；提示词文本被输入审核拒 → 软化场景措辞（台词不动）加合规声明重试一次。都只一次，剩下的进报告。
- **出片并行与全局片段池**：`--inflight N`（默认 20）是跨进程的槽位锁，所有正在渲染的集共用，总在飞恒定；`--workers 0`（默认）让每段各占一槽，不再分两波；`--parallel` 只决定同时拼接的集数（8 左右即可）。Seedance 实测 17 段同飞中位 277 秒、p90 434 秒、偶发限流由退避自愈，甜区 15–20 段。
- **提交前预审** `--prescreen`（批跑默认开）：先让本地 Qwen 判提示词的审核风险，≥0.6 直接软化场景措辞再首次提交，减少被拒的空跑。
- **快速档省钱两刀**：不建表情卡（快速档每角色只用一张参考图）；每集最多 3 段、约 90 秒封顶。
- **后处理**：拼接前的帧率规整 4 段并行、更快的编码预设。
- **多实例 Qwen**：`QWEN38_LOCAL_BASE_URL` 写成逗号分隔的多个地址（如四个单卡实例 18120–18123，`/mnt/disk1/zengzhitao/tmp/qwen_start4.sh` 起，每台并发 6），规划、圣经增长、审卡审片按章节散到各台，某台连不上自动换。单卡实例（张量并行 1）比双卡更划算，INT8 权重 28 GB 单卡放得下。
- **建卡并行**：`scripts/build_cards_thin.py` 一个进程建一个资产（每资产文件锁、图片接口限流退避、建完当场审核并修一次）；批跑的 `CardFactory` 按 `--card-parallel`（默认 6）同时建，每集只等自己引用的卡；圣经一增长出新角色和地点就提前排进队列。渲染开始时原有的"建缺卡"逻辑保留作兜底。

```bash
QWEN38_LOCAL_BASE_URL=http://127.0.0.1:18120/v1,http://127.0.0.1:18121/v1,http://127.0.0.1:18122/v1,http://127.0.0.1:18123/v1 \
.venv/bin/python scripts/thin_batch.py --novel-dir outputs/X --chapters 1-500 --merge 2 --tier fast --parallel 8 --inflight 20 --plan-parallel 8 --card-parallel 6 --unattended --prune
```

## 屏幕上的聊天消息（群聊、私聊）

默认画面里不允许任何可读文字（视频模型画中文易乱码）。聊天消息不再交给 Seedance 画，改成自己渲染插卡（`scripts/chat_card.py`，渲染器 v17）：

- 规划器把消息标成不发声的 `chat_message` turn（发消息的人 + 消息原文，逐字取自原文、≤36 字，超长的截到一个标点并加省略号，一个阶段最多四条）。群聊消息 `chat_target` 留空；一对一私聊写成对方的名字，卡片标题就是对方的名字、气泡上方不显示昵称。
- 打包器给含消息的阶段写"不要拍屏幕内容——手机屏幕背对镜头、被手指遮住或只见反光"，并恢复最严格的"画面中不出现任何文字"约束；提示词里另加一条"不要生成字幕条、台词字幕、字幕栏"，因为 Seedance 有时会自带字幕。
- 渲染器用 PIL 画微信风格的界面（`outputs/<novel>/chat_screen.json` 的群名、本人昵称、布局；`render` 字段为 `card` 时启用，`video` 回到旧的让模型画屏幕的做法），消息逐条弹出、每条约 1.15 秒配一声轻提示音，超过 5 条自动分成多张卡顺着往上滚，背景是该片段的模糊画面。卡片作为独立片段硬切在对应片段之前：先看到消息，再看人物的反应。
- 头像从角色卡的立绘裁头（固定取头部窗口，缓存在 `series_assets/avatars/`），没有卡的角色用带首字的色块头像。
- 卡片不花 Seedance 秒数、文字逐字准确、不过审片的 `chat_text_ok`；消息不进字幕、不进语音门。
- 单独渲染一集的卡片：`python scripts/chat_card.py --novel-dir outputs/X --episode-dir outputs/X/X_11 --preview`。

## 题材预设

会随题材变的规则不写在代码里，放在 `configs/genres/<key>.json`（现有 `generic` 通用、`xianxia` 古风玄幻、`urban` 现代都市），`profile.json` 的 `genre` 指定用哪份；起书时 `build_bible_thin.py` 按圣经里模型给出的类型和开头几章的关键词自动选，`--genre` 可强制。每份预设的字段：

| 字段 | 作用 | 古风玄幻 | 现代都市 |
|---|---|---|---|
| `era_allowed` / `era_rejects` | 规划器的时代设定说明；`era_rejects` 追加进每段的【不要】 | 禁电灯眼镜印刷品手机 | 禁古装油灯宫殿刀剑 |
| `text_on_props` | 画面描述里出现可读文字是硬门（gate）还是只报告（report） | gate（碑文改无字纹路） | report（招牌屏幕常见） |
| `anonymous_roles` | 画外无名角色的称谓表 | 测验员、族人、弟子、长老 | 路人、同学、同事、医生、司机、保安 |
| `card_style_suffix_3d` | 3D 角色卡追加的风格句 | 国漫年番式、古风布料 | 皮克斯式概括，现代服装也动画化 |
| `location_policy` | 地点卡：`empty` 全空 / `sparse` 主体空、远处允许模糊行人 | empty | sparse |
| `soften` | 审核拒绝时软化措辞的替换对（叠加在通用词典上） | 刀剑砍劈、血海 | 威胁、暧昧、赌债、绑架 |
| `grammar_rejects_extra` | 起书时并进视觉语法的禁忌 | 碑文、战气特效、现代物件 | 招牌文字、古代物件、车牌商标 |
| `chat_screen` | 是否默认生成聊天界面模板 | 否 | 是 |
| `crowd_default` | 群众场面的默认处理，写进规划请求 | 弟子族人列队 | 街头路人模糊背景 |
| `moderation_note_extra` | 审核连拒后自动重规划时追加的导演意见 | 打斗改对峙 | 威胁暧昧改间接 |

新增题材就复制一份改字段；三道硬门、审核处理、并发缓存这些通用机制不受预设影响。已有目录已按检测结果补上 `genre`（诸天万象录 → urban，焚天记 → xianxia）。

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
| `scripts/chat_card.py` | 微信风格的群聊/私聊插卡：PIL 画界面、逐条弹出、每条一声提示音、头像从角色卡裁头；渲染器在拼接时把卡片硬切在对应片段之前。 |
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
- Seedance 会加即兴群众杂音 → 字幕按 ASR 定时、用剧本原句（渲染器 v18）：语音块按顺序在整段剩余剧本里找最像的原句（相似度 ≥0.35，跳句有小惩罚；阿拉伯数字按读音比对，1000万 = 一千万）；块边界离标点 3 字以内吸附到标点，字幕不会从词中间起；匹配到的文本按剧本行拆条（一行一个说话人，问答不同屏），每条按字数分时间，再按阅读速度封顶（0.8 秒 + 每字 0.45 秒）和垫底（每字 0.2 秒，不压下一条）。匹配不上的语音只看两个客观量：不足 0.8 秒或识别出的有效字不足 6 个的算杂音不出字幕，其余按听到的显示——不使用任何词表或叙述文本模糊匹配（试过，模型是改写不是照读，重合度不足 0.3，没有意义）。代价是模型偶尔把画面描述念出来时会作为字幕出现（第 11–14 章样本里每 6 集约 2 条）。报告里每块标 script_span / asr_text / dropped_murmur / silent。改字幕规则后重烧不花 Seedance 费用：对已出片的集重跑 `render_clips_thin.py --novel-dir … --episode …`，片段走缓存，只重新拼接（每集约 1 分钟）。
- 一段里两名相貌相近且都有参考图的少女会串脸 → 不说话且未被画面点名的角色不给参考图；已知案例用 `clip_overrides.json` 单段修。
- 一段出错（网络、ASR）不再拖垮整集：按段记录，成片报告 `status: clips_failed`，重跑只补那几段。
- 图片接口的内容审核拒画某张卡（现代题材女性角色偶发）→ 用去掉敏感词并加"端庄得体、衣着完整"的提示词重画一次，再拒就立即报错，不再空转 6 轮。
- 现代题材的 3D 卡容易被画成写实真人质感 → 建卡后的自动审卡会判为像真人并动画化重画，每个角色多两张图；这是预期行为。

## 经验

- 模型对"秒数"不敏感，对"几段、几个阶段、多少字"很听话；调长度用具体数字。告诉模型的上限和代码检查的上限来自同一组常量，别再出现"写够了仍被拒"。
- 每段引用 3 个及以上角色时每人只给一张定妆卡。
- 隐私重画会改变角色长相（更圆、更低龄）。新书建卡后先看 `cards_sheet.jpg`，像真人的卡可以先删掉重建，比等渲染时被拒再重画更省。
- 焚天记 2D 集的圣经文件里仍写着 3D，实际由 profile 说了算；提示词画风句可用 `style_line` 覆盖，不改圣经和已有卡片。
