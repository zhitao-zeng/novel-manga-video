# 在途工作板(多会话协作的唯一事实来源)

规则见 AGENTS.md「多会话协作」。每个会话开工登记、收工销账。
格式:`| 会话/负责人 | 干什么 | 分支/worktree | 动的文件范围 | 开工日期 | 状态 |`

## 在途

| 会话 | 干什么 | 分支/worktree | 范围 | 开工 | 状态 |
|---|---|---|---|---|---|
| kimi(本会话) | 十日终焉接入+验收(分镜300/300待ch221,渲染2-11待额度) | main 直接改动(历史原因)+ 已过审 | outputs/shiri、configs/pipeline.json 的 shiri 段 | 2026-09-28 | 进行中 |
| kimi(本会话) | 光明纪元资产盘点:声线库已建(91人),圣经分诊表已出,最小档修正因子代理被杀(额度)中断 | main | outputs/guangming-ds-100 | 2026-10-08 | 待续 |
| 未知会话 | 主工作区有未提交改动(pipeline.json/cards.py/bible.py/asset_builder.py 等 10+ 文件),导致 tests/test_production_runtime.py::test_locked_series_asset_survives_prompt_revision_without_image_call 在工作区红(干净 HEAD 上绿) | 未登记 | src/novel_manga/application/{assets,review}/ 等 | 2026-10-08 | ⚠ 未登记在途,认领请补登记 |
| codex(历史会话) | episode-agent 整集制作线 | worktrees/episode-agent + episode-agent-minimal + episode-scheduling | application/agents/episode/ 等 | 2026-10-02 | 在产(光明纪元),未合并 |
| codex(历史会话) | LLM 缓存顺序工作 | episode-agent worktree 内 | scripts/llm_cache_order_*、docs/superpowers/plans/ | 2026-10-08 | 状态未知 |

## 已收工(留档备查)

| 会话 | 干了什么 | 分支 | 结局 |
|---|---|---|---|
| authored-split 会话 | authored 拆镜 keep_authored 修复 | fix/authored-split-keep-authored | 已合并(09-29,c74effc) |
| tier-decouple 会话 | tier 拆四独立键 | refactor/tier-decouple | 已合并(09-30,04be7fe) |
| prompt-chain 会话 | H3 提示词起草统一 | main | 已提交(1f26570) |
| 整合会话 | H3 544P/穿戴核对/时长压缩/配额停机 | main | 已提交(5e934cb) |
