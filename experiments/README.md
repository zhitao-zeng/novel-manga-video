# 实验目录

这里的入口不参与生产调度。正式生产和看板入口见根目录 README。

- `benchmark_*`：规划、生成速度、修复原因及构图的对照实验。
- `judge_planner_outline.py`：规划提纲实验的独立评审。
- `rebuild_source_book.py`：从原文重建人物库、事实和剧本的小样本试验，尚未接管生产。
- `build_s1.py`、`ep*_data.py`、`build_ceiling_plan_v1.py`、`build_episode1_user_recut_plan.py`：手写样片与剧本实验。
- `run_episode1_local_plan_ab.py`、`report_episode1_plan_ab.py`、`deepseek_local_planner_command.py`、`gen_diagnoses.py`：早期完整规划方法的实验工具。
- `legacy/`：以上实验需要的旧规划器和素材复用研究代码；正式生产不得导入这里。

在仓库根目录运行 `python experiments/<入口>.py --help` 查看该实验参数。实验产物继续写入忽略的 `outputs/`，不迁移历史产物。
