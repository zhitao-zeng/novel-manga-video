# 产线全景图(2026-10-08 实查)

新人/新会话入口。细节见各层文档;带日期的文档是时点记录。

## 主流水线

```
原文 → ①准入 → ②读书/身份 → ③规划(分镜) → ④建卡 → ⑤渲染 → ⑥审查/修复 → 成片
```

### ① 新书准入
唯一入口 `scripts/setup_book_thin.py`(不要单跑 build_bible_thin.py——它不出 reading_cast.json,身份绑定校验会全线拦停)。四步:切章 → 逐章精读(本地 Qwen 并行)→ 沙箱归并演员表(Flash-Next)→ 按名单重建圣经 + **台账播种对账**。端点预检先查后跑。

### ② 读书与身份(三本账)
- `reading_cast.json`(归并名单)、`story_bible.json`(定妆)、`entity/`(逐章在场证据台账)
- same_as 合并:两审+终审+**变身证据硬护栏**(变身/乔装证据→转人工,不自动合并)
- 对账:`scripts/reconcile_cast_ledger.py --novel-dir X [--fix]`;setup 第 5 步播种
- 扫描并行、台账落账按章序串行

### ③ 规划(conductor)
- `configs/pipeline.json` 是唯一事实来源;ranges 带 `plan_mode`(15/30 秒档)+ `min_seconds`(软下限,floor waiver 是设计)
- 块调度 63 章/块;读书领先 20 章才发块;槽位:local Qwen 3 + Flash-Next 3 + DeepSeek 8
- 每章:分析→提纲→剧本→补丁(分批/180s 预算)→校验;长章 >6000 字自动拆集
- 生产档键位(tier 已拆):`resolution`/`expressions`/`two_views`/`episode_shape`,见 `docs/profile-keys-20260930.md`

### ④ 建卡
- 画风包(`configs/styles/`)= 纯美学;模型方言层(`configs/image_models/`)= 该模型听得懂的话
- **道具走本地 Qwen-Image(免费,`NOVEL_LOCAL_QWEN_IMAGE_URL` 开关),人物/场景走 API(保脸)**
- 卡审查抓写实人脸/设定不符自动重画;穿戴物必须阶段卡(phases.json wears),改了必须重打包

### ⑤ 渲染
- sd25 付费(30s 档;**480p 已实测服从,720p 会被静默升档 1080p 计费**)/ h3pool 本地免费(15s 档,544P)/ sd20
- 参考图:资产库 → 托管 URL → base64 内联
- 声线库 `scripts/build_voices_thin.py` 从已出集 ASR 自举(≥8 秒/人);新书前几集裸跑后建库
- ASR 校验(SenseVoice)+ 心声 postmix

### ⑥ 审查/修复/发布
- 薄 QC(ffmpeg 客观)→ 一致性审查 → 逐帧 verify(fine/subtle)→ `publish_if_ready` 全绿转正
- 每段最多 3 take;超限要 `repair_budget_grants.json` 人工 grant;**付费重拍必须人批**
- 流程三分:production(往前推)/ prepare / repair(修复总控)

## 瓶颈 / 并行 / 人工闸门

- 串行:台账落账、本地生图(单线程)、H3 每实例单任务
- 并行:规划槽位(3+3+8)、渲染车道(sd25 最多 56 在飞)、建卡、审查
- 人工闸门:变身类合并、泛称合并、付费重拍、不确定提交放行、超预算 grant

## 运维

- 看板:`scripts/status_server.py` → 18900(/published/ 服务资产发布目录)
- 日志:`/mnt/disk1/zengzhitao/tmp/conductor-<书>/conductor.log`
- 配额停机:sd25 配额耗尽自动停,cron 每日定时再启(configs/schedules/)
- 探活:episode 工具的 doctor 动作(在 episode-agent 分支,待合 main)

## 本周新增(09-28~10-08)

setup_book 唯一入口+台账对账播种、tier 拆四独立键、长章自动拆集、血迹门拆档(血迹 report-only/伤口硬拦)、归并别名规则、模型方言层与按类型路由、same_as 变身护栏、H3 默认 544P、穿戴证据视觉核对、配额停机、时长压缩(compression.py)、gate 误杀埋点、episode-agent 整集制作线(未合 main)。

## 多会话协作

见 AGENTS.md「多会话协作」+ `docs/active-sessions.md`(在途工作板)。
