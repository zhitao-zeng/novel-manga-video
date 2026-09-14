# 终审精判、精判门与账本终裁（2026-09-14）

雾月的教训：一审判官（thin_review 剧情判官）标的 must_fix 段只有一半在画面里确认得到，判过关的段里又有 4% 是观众一眼能看出的错。
于是"要修什么"改由看帧的终审决定，账本的身份判断不再留给人。

## 精判 `scripts/verify_clips_thin.py`
- 每段 5–6 帧 + 至多 3 张角色卡 + 原文 + 事件行 + 账本出场快照，模型先描述帧里每个人，再答五个是非题：同一人出现两次、物种或性别错、动作落在错的人身上、该出场的人缺席、主角换脸。任一成立即 `obvious`；只有对照卡才看得出的差别是 `subtle`；否则 `fine`。画面文字单独记，不影响结论。
- 模式：`candidates`（一审必修段，带一审意见供核实）、`sample N`（随机抽判过关的段估漏网率）、`all`（全书每段）、`replay:<file>[:<mode>]`（换个判官重看同一批，做校准）。
- 记录按（集、段、视频文件）去重，重跑只补新的 take。判官从 `QWEN38_LOCAL_*` 取，每次调用前重新应用（有模块会改写这些变量）。

## 精判门 `scripts/verify_gate_thin.py`
- 对给定集：must_fix 段若没有同一 take 的精判记录就现场精判；`obvious` 留下，否则降为 optional、重拍指令从 `feedback` 挪到 `feedback_cleared`；判官判过关但精判 `obvious` 的段提升为 must_fix（story_issue、feedback 来自证据）。
- 带 `technical`（黑屏）或由精判判官自己写的 must_fix 不会被摘掉。备份 `episode_review.json.bak-gate`。
- 修复链每批：门 → 修段 → 渲 → 复审 → 门 → 两轮重拍（每轮再过门），BATCH RESULT 按精判口径计数。

## 精判当一审：`NOVEL_REVIEW_MODE=verify` 或 profile.json `"review_mode": "verify"`
`thin_review.judge_clip` 改走 `judge_clip_verify`，结果映射回原来的字段（`story_ok`/`story_kind`/`severity`/`feedback`，原始答案在 `verify`），`fix_tier` 和看板不用改。新书直接用它，省掉一审 + 门两道来回。

## 账本终裁（`entity_ledger_thin.settle_pending`）
两位判官意见不一 → 第三位终审必须定，拿不准按"不是同一人"；换身体类判断证据句没点到身体主人 → 专项提问定。全书复判之后没有 pending，不再需要人看。

## 其它
- `qc.py` 检查项可豁免：profile.json `"qc_ignore": ["silence_ratio", "long_silence"]` 或 `NOVEL_QC_IGNORE`，豁免项保留数值、标 `waived`、按通过算（雾月：静音不是交付标准）。
- 打包器 `bodies_for`：账本说某人此刻在别人身体里，段计划引用那具身体的卡、按那个人的外形写，提示词注明。
- 段级修复 `scripts/repair_clips_thin.py`：只重写判错段的阶段并重建该段条目，其它段请求不变、吃缓存。
