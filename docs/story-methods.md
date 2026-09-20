# 六种本地剧本方法

批量规划可选择六种创作方法。它们共用当前小说读取、人物资料、字段定义、验证、局部修订和打包流程；先写完整分场正文，再按各方法完成导演拆镜。

这是参考公开创作方法编写的本地实现，不是原 Skill 的完整移植，也不是相同质量的承诺。推理仍由当前配置的文本模型完成，本地代码负责请求、交接和检查。

## 选择方法

### 输入已经是完整分镜时

完整分镜不经过小说改编。使用同一个规划入口的 `--storyboard-sheet` 导入 XLSX：

```bash
.venv/bin/python scripts/plan_chapter_thin.py /path/to/storyboards.xlsx \
  --storyboard-sheet A_山音 --novel-id storyboard-a \
  --output-root outputs/experiments/storyboard-import --style 3d --frame 16:9
```

导入保留每一行的镜号、摄影角度、景别、动作、完整场景标识、台词/声音、机位/连续性、叙事目的和剪辑预算；按表格顺序保存，不重排闪回和冷开场。剪辑入出点从预算秒计算，忽略可能过期的公式缓存；不会把短镜的剪辑预算拉长成模型生成时长。需要新的集目录，避免覆盖已有计划和视频记录。

结果仍写入 `chapter_script.json`、`chapter_script.md` 和报告，状态为 `imported_needs_bindings`。这只是保真导入：未提供的画面起止状态不猜测，混合声音列不擅自拆成台词或静音，人物和参考资产尚未绑定。打包入口会明确拒绝这类草稿。当前尚未实现从该草稿自动完成技术绑定、H3 编译和跨镜声音剪辑；没有 `clip_plan.json`，也不会调用模型或生成视频。报告的逐字段一致只证明没有丢原稿，不代表编剧质量或成片验收。

### 输入是小说时

| 参数 | 方法参考 | 本地要求它具体完成什么 |
|---|---|---|
| `shanyin` | 山音编剧＋导演 | 核心戏剧动作、外部事件与内部反应的节奏差异、动作中的潜台词、镜头组 |
| `community` | 社区 shortfilm-prompt | 人物和道具识别特征、物件状态、具体同期声、运动方向与结尾回扣 |
| `drama` | Drama Skills | 单集观看承诺、因果递进、每场状态变化、已经成立的退出与交接事实 |
| `dream` | 造梦师 | 场景事实、因果痕迹、摄影机的观察位置、身体与物件行为、场景光源 |
| `leos` | Leos 六部门导演组 | 共享场次重点与空间、刺激、身体调整、带目标的动作和余波 |
| `visual` | Visual Skills | 戏剧变化、镜头职责、信息释放顺序、环境与身体细节、切点与视觉或声音线索 |

方法中没有小说名、角色名或指定道具。具体表达从当前章生成，不把此前六份示例稿变成固定剧情套路。

查看全部方法及字段：

```bash
.venv/bin/python scripts/plan_chapter_thin.py --list-methods
```

单集试跑，使用独立产物目录：

```bash
NOVEL_CLIP_SECONDS_MAX=15 .venv/bin/python scripts/plan_chapter_thin.py \
  inputs/盛唐天工.txt --novel-id method-shanyin --episode-index 1 \
  --bible outputs/shengtang/story_bible.json \
  --output-root outputs/experiments/story-methods \
  --story-method shanyin --style 3d --frame 16:9
```

只看请求时加 `--dry-run`；会输出 `request_dry_run.json` 及含实际分场请求、字段和创作预算的 `method_request_dry_run.json`，不请求模型。

规划完成后沿用原打包命令：

```bash
NOVEL_CLIP_SECONDS_MAX=15 .venv/bin/python scripts/build_clip_plan_thin.py \
  --episode-dir outputs/experiments/story-methods/method-shanyin/method-shanyin_1 \
  --bible outputs/experiments/story-methods/method-shanyin/story_bible.json
```

批量使用时，在已有小说 `profile.json` 中设置 `"story_method": "leos"`。生产和准备流程调用现有规划器时自动继承，无需新增进程或另一套任务队列。单集 `--story-method` 优先于小说设置；`--story-method default` 使用原有规划方式。

未选择新方法时，默认仍是原来的 coverage/story 两遍规划。选择方法后，分场写作与导演拆镜取代原来的提纲与整稿两遍生成，`--outline-mode` 不控制这条新路径。

## 实际执行和交接（场景流程 v2）

```text
完整原文与人物资料
  → 分场剧本（连续时空、正文动作、原句对白、声音、入口和出口）
  → 对照原文复核分场稿；最多一轮正文修订，仍有问题则停止
  → 所选方法的导演设计与逐镜表达
  → 对照已确定分场稿复核实际镜头；最多一轮相关场景的导演修订
  → 现有身份解析与片段打包
  → 现有 H3 六段英文模板
```

山音、Drama 使用各自的场景写作要求；社区、造梦师、Leos、Visual 使用共同小说改编步骤，然后分别接管导演、表演、摄影或剪辑。四者不被当作自带完整小说编剧方法的工具。上游方法参考并非完整照搬，其人工逐轮确认由当前批量任务自动交接。

1. 保留完整章节。原文区段只是引用坐标，场景可以跨区段引用，一个区段可以被多个场景使用；没有每区段 1–2 拍的创作配额。
2. 首遍生成分场正文。每场写明确地点、故事时间、场景作用、入口状态、按顺序发生的动作/对白/声音及出口状态；回忆、返回现实和跳时分别标注。每个正文单元引用完整原文中的连续短句，可跨相邻区段边界；程序记录实际覆盖的每个区段。原文不存在与区段编号定位不符分别报告，引用数量不能代替事实核对。
3. 首场 opening、场景/正文单元/台词编号由程序确定。长台词在导演之前按现有规则拆为保留字词、标点及说话人的片段，导演收到每条台词的估时，以及同一正文单元内可容纳的台词分组。预算与验收共用同一计算，导演据此安排镜头，避免后段才因超长重新猜归属。
4. 分场稿先对照完整原文复核，未解决的问题停在编剧阶段。导演拿已确定的完整分场稿，不再收到原文和引用，以免重新改编故事；按所选方法完成整集设计及各场实际镜头。镜头引用正文单元和台词编号；台词文字、说话人及发声方式由程序复制。一个单元可以拆镜，但原句台词只能按顺序分配一次，所有场景和正文单元必须有承载。
5. 编剧复核对照原文，导演复核对照已经确定的正文和绑定后的实际镜头，分别定位因果、跨时空合镜、重复动作、知识越界、入镜及摄影矛盾。模型返回已有材料编号，程序取出实际原文/正文和草稿作为证据，不让模型重打引文；纠正错误编号不能撤掉同稿已经有依据的其他问题。只报告影响理解或拍摄的问题，不评分、不按镜数判好坏。
6. 编剧和导演各最多一轮内容修订：分场稿本身错则先修分场稿再拆镜；分场修订只替换点名场景，允许把一场拆为多场；导演稿的对白预算或交接出错也只重写对应场景，其他场景原样保留。涉及全片缺失或全片硬时长上限时，仍保留整稿修订。复核仍有问题的稿子不按成功规划发布。每次模型输出不完整或交接字段有问题，可做一次有界纠正；现有 CLI 外层整稿重试预算继续有效。
7. 沿用 `chapter_script.json`、`episode_plan.json`、`clip_plan.json`。分场、导演及复核结果嵌入 `chapter_script.json.story_blueprint`，不新增数据库或队列。`scene_script.md` 展示分场正文，`chapter_script.md` 展示导演镜头；完整请求和响应保留在 `method_attempt_NN/`，不保存认证头。
8. 同地点的不同场景也保持边界，打包和短片段吸收都不能将它们合成一次连续拍摄。保留导演计划镜长、场景时间、源镜号、具体声音和动画媒介；台词最低估时超过计划镜长时，程序在单镜上限内延长并记录原值，超过单镜上限仍须拆镜；不再用拼接 actor/action/target 的句子覆盖导演的完整事件句。
9. H3 保留六段英文结构与原有对白绑定。新场景稿的具体声源、声响开始/停止会进入英文翻译，中文仅能出现在对白标签内；统一 3D/2D 媒介显式写入请求，地点参考不再强迫所有场次沿用参考图的白天光线。原有无场景元数据的剧本与请求路径维持原行为。

首次试跑建议使用足够的文本预算，防止把省略清单或导演表截断：

```bash
NOVEL_CLIP_SECONDS_MAX=15 .venv/bin/python scripts/plan_chapter_thin.py \
  inputs/盛唐天工.txt --novel-id method-shanyin --episode-index 1 \
  --bible outputs/shengtang/story_bible.json --output-root outputs/experiments/story-methods \
  --story-method shanyin --style 3d --frame 16:9 \
  --outline-tokens 12000 --max-tokens 16000 --max-redo 0
```

`--outline-tokens` 在新方法中控制分场稿的输出预算，默认 8192；`--max-tokens` 控制导演稿预算。普通规划仍是原提纲含义，默认 4096。数组容量上限用于限制模型无止境输出，不能当成正常镜数或每个原文区段的戏份配额。

`--max-seconds` 可明确指定全片上限。新方法未指定时只有约 90 秒的参考目标，不沿用旧规划的 105 秒硬上限；用户提供的 100 秒样稿预算不作为限制。指定上限时，导演按真实镜长与台词预算调整，程序不等比压缩台词或删掉整场。普通默认规划未指定此参数时保持原预算。

规划通过后沿用 `build_clip_plan_thin.py` 和 `build_h3_prompts.py`。两者完成后只是获得计划和请求，参考素材仍由现有资产阶段准备；本步骤不自动生图、生成视频、发布成片或恢复暂停任务。

## 验证边界

- 引用、场景/台词交接和纯编译有确定性检查；内容复核仍是模型判断，不能证明短剧好看或视频实现了设计。
- 当前估时只是文本和导演估计；跨片声音桥仍需要后期执行，不能把一个音效字段当作已经混好了音。
- 新稿的未解决复核意见会阻止规划通过；历史 v1 提纲只为既有产物回放保留，不再用于新方法生成。
- 本次独立实验保存首次失败稿和后续修正，使用真实文本调用；不调用真实图片或视频生成。

回归覆盖场景边界与短片段吸收、来源归属、长台词拆分、动作与动物/物件目标、空镜入镜控制、局部修订范围、中文/英文声音与对白交接、回放产物和默认路径兼容。字段检查通过不能代替对最终故事的通读。

## 维护位置

- `planning/methods/{shanyin,community,drama,dream,leos,visual}.py`：每种方法的编剧/导演要求与专属字段；方法文字只维护一处。
- `planning/methods/scenes.py`：创作输入、分场正文、引用和台词编号。
- `planning/methods/direction.py`：导演请求结构、分场到镜头的交接、内容复核契约。
- `application/planning/method_pipeline.py`：模型调用、有限修订与证据记录。
- `application/planning/flow.py`：命令与批量流程落盘；保持现有入口。
- `story/compilation.py`、`application/packing/service.py`：场景边界、导演时长与元数据。
- `application/rendering/h3.py`、`story/h3.py`：原 H3 编译及新场景稿的声音/动画媒介交接。

## 方法来源

以下是创作方法参考及作者署名。仓库中只保留本地新写的实现与这些来源说明，没有打包上游 Skill 正文或执行脚本。

| 来源 | 作者 | 本次参考版本 |
|---|---|---|
| [山音编剧](https://github.com/Shanyin-ai/shanyin-screenwriting-master) | @山音 | `ca20ce2`，包内编剧方法与剧集资料 |
| [山音导演](https://github.com/Shanyin-ai/shanyin-director-master) | @山音 | `30f0dae`，节奏、镜头设计与九列表规范 |
| [社区 shortfilm-prompt](https://github.com/jnMetaCode/ai-shortfilm-prompts) | jnMetaCode；上游注明 Mx-Shell | `80d6fc2`，多镜头叙事与一致性方法 |
| [Drama Skills](https://github.com/zenstory-ai/drama-skills) | zenstory-ai | `fcaba9c`，单集写作与分镜 |
| [造梦师](https://github.com/popopo-99/zy-cinematic-realism) | ZY / popopo-99 | `e79c53d`，场景事实、观察位置与因果细节 |
| [Leos](https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1) | MasterLeos | 本项目此前保存的 v1 快照，场次、表演与连续性资料 |
| [Visual Skills](https://github.com/smixs/visual-skills) | Serge Shima | `92be33a`，dramaturgy 与剪辑方法 |

上游的人工逐轮确认、特定模型语法、固定画风、量化审稿比例等没有直接移植。它们与本项目的批量运行、现有角色规则或 H3 英文编译可能冲突；本地版本明确以项目输入与现有字段为准。
