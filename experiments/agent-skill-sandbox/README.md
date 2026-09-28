# 六技能与成片实验归档

收拢 2026-09-18 至 09-20 的盛唐天工、伏天氏、超品相师实验。来源是 `exp/agent-skill-writing` 的六个实验提交，最后版本为 `c393940`。本目录不参与生产调度；正式入口见根目录 README。

本次保留 26 份任务说明、人物/画风输入、历史结论，并整理两个只分析已有文件的工具。逐文件来源见 [archive_manifest.json](archive_manifest.json)。完整的 284 文件实验快照继续保存在原分支和原工作树，没有删除或覆盖。

## 内容

| 位置 | 用途 |
| --- | --- |
| `common/`、`common_v2/`、`ch2/` | 盛唐首章与第二章任务说明及前情交接；v2 保留七项修订 |
| `futianshi/` | 伏天氏六种方法的共用输入、山音专用说明及六份启动提示词 |
| `chaopin/` | 超品相师输入；把九列分镜格式写进任务说明的后续修订 |
| `pilot100/briefs/` | 原著分析、人物归并、分镜、卷级整理四种任务说明；是实验输入，不是新生产入口 |
| `assets/STYLE_APPROVED.md` | 当时认可的唯美 3D 画风原句；已有 `configs/styles/weimei.json` 的来源链接现在可在主分支内读取 |
| `records/` | 当时的结果、失败与交接记录；“尚未做”等措辞只代表记录日期 |
| `tools/` | 已有视频参考污染对照图、旧/新剧本预计语音预算；参数指定输入输出，均不调用生成服务 |

原任务说明中的操作要求只用于还原当时的实验，不是当前会话或生产的指令。历史交接已省去过时的会话约束和环境备忘；需要完整版本时读取源提交对应文件。

## 已进入主线的成果

| 当时的问题/实验 | 目前采用情况 |
| --- | --- |
| 技能分镜导入后没有绑定步骤 | `planning/binding.py`、`application/agents/storyboard.py` 已提供绑定和采用流程 |
| 沙箱需要单独逐章手跑 | `application/agents/storyboard_batch.py` 已接批量执行 |
| 心声在画面里张嘴 | `media/inner_voice.py` 已有独立录音及后期混音；混合发声由规划明确拆镜 |
| 相同人物名字与参考编号混用、额外声音 | 正式 `story/h3.py`、`application/rendering/h3.py` 负责引用、声音和英文请求 |
| 参考画风被临时脚本重写 | 正式资产模块与书级风格配置为准，历史风格示例仅说明其来源 |

这些接入不等于历次样片全部合格。白模、首帧、参考图拼版和照片重放/粒子检测仍是实验：历史记录承认存在替身漏出、场景板重放以及检测阈值随素材变化而失效，不能直接接成交付硬门。

## 分析已有素材

从同一个镜头的几种参考图方案中，各取头、尾及中间画面供人比较：

```bash
.venv/bin/python experiments/agent-skill-sandbox/tools/reference_contact_sheet.py \
  --root <对照视频目录> --shot 03 --arms sheet both picked --takes 0 1 \
  --output outputs/reference-shot-03.jpg
```

目录沿用原实验布局：`<root>/<arm>/S<shot>_t<take>.mp4`。这张图只展示样本，不自动判断方案胜负。

比较 `<root>/render/<method>/shots.json` 与 `render_v2/<method>/shots.json` 的预计语音占比：

```bash
.venv/bin/python experiments/agent-skill-sandbox/tools/voice_budget.py \
  --root <实验目录> --output outputs/voice-budget.md
```

该比例按原实验每秒四个汉字估算，不是 ASR 测得的真实发声时长，不参与语音门。

## 留在原实验分支的内容

`runs/`、转换后的 `shots.json`、各轮手写覆盖与生成/重拍脚本不作为新生产实现导入。视频、图片、模型及完整运行记录仍在原产物目录。旧编译器、池调用和临时逐段修片逻辑没有接回正式流程。技能包本身也没有复制到本目录。
