# 测试分工与运行

在项目环境运行 `.venv/bin/python -m pytest`。普通测试禁止未模拟的 HTTP 模型请求；媒体合成测试使用固定短素材和真实 FFmpeg。本地看板 HTTP 测试只访问它自己创建的临时服务。

| 领域 | 主要回归 |
|---|---|
| 字段、身份和编译 | test_scene_*、test_dialogue_binding、test_action_scene_references、test_planner_contract |
| 规划和模型传输 | test_planning_decisions、test_planner_*、test_outline_completion、test_model_client、test_bible_transport |
| 本地创作方法 | test_scene_method_pipeline、test_story_methods：六方法分场→导演→打包→H3，来源/对白交接、动物/物件、场景边界、局部修订、预算与历史回放 |
| 资产、供应商和音频 | test_asset_services、test_provider_services、test_explicit_media_frame、test_speech_evaluation |
| 渲染和缓存 | test_media_flow、test_media_postprocessing、test_clip_retry_decisions、test_generation_recovery |
| 修复和拆分 | test_repair_*、test_managed_repair、test_split_continuity、test_split_repair_and_assets |
| 生命周期与配置 | test_batch_control、test_manager_lifecycle、test_episode_options、test_runtime_configuration、test_production_resumption |
| 统计和看板 | test_usage_reporting、test_status_server、test_dashboard_assets、test_dashboard_charts、test_pipeline_dashboard |
| 包和职责 | test_application_imports、test_shared_model_schema、各领域的依赖边界检查 |

共享夹具在 support；测试不从其他 test_ 文件导入夹具。原按 review3/review4/review5 命名的综合回归分别归到 production_resumption、generation_recovery、split_continuity，原断言保留。

`fixtures/pipeline_25_clips.json` 是五个既有章节各五段的冻结片段计划，覆盖盛唐、诸天 2130/2340、雾月 422、星海 1。`pipeline_requests_before.json` 对照 SD/H3 请求、对白绑定、缓存匹配和修复路由；H3 文字基线采用用户确认的调试版本。

规划完整章节的 15/30 秒十组对照和浏览器前后截图等较大/环境相关证据保存在忽略的 `outputs/full-refactor-20260917/`，不复制小说、模型或视频进 Git。目录整理不通过真实生成模型验证。

## 日常验证范围

优先按修改范围选择测试，不以总测试数衡量质量。例如分场和导演交接先运行 `test_scene_method_pipeline.py`、`test_story_methods.py`；改到共用估时或原文定位，再运行规划预算和片段计划回放。全套主要用于跨模块重构或交付前回归，不需要每次调提示词都重跑。

截至本轮修改前，988项是全项目127个文件中843个测试函数加参数组合的累计数，新分场流程占27项；不是988次模型实验。原文/编剧质量必须另外看真实案例，模拟模型返回的单测不能证明创作效果。2026-09-18的局部修复补充跨区段引用、分场替换和超时对白局部修订三个回归，不为增加数量复制测试。
