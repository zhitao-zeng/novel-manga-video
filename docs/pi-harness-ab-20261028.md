# Pi vs Claude Code 输入修复 A/B（第 20 集）

2026-10-28。目的：在同一个真实输入修复任务、同一套宿主门禁下比较 Pi agent kit v2 与 Claude Code 的 Agent loop，不涉及 H3 生成。

## 设置

- 第 20 集运行目录复制两份，互不共享状态；同一 `input/current-task.json`、同一 current-status、同一套 `tools/episode.py` 宿主桥。
- 两臂都禁用 render/review/finish，只允许 check/apply/status；预算/次数/回执仍由宿主代码执行。
- 同一 official DS41 模型（`deepseek-v4.1-flash-1017`）；Pi 配 1M 上下文/128k 输出，Claude Code 用它自己的 200k/32k。
- Pi 首次因 base 少 `/v1` 流解析失败；改为 `https://cloud.phanthy.com/phanrouter/v1` 后正常。宿主 socket 用 `/tmp/piab_<arm>.sock` 避开 108 字节路径限制。

## 结果

| | Pi | Claude Code |
|---|---|---|
| Agent 时间 | **894.6 秒（约 15 分钟）** | 首次 1800 秒超时；原会话续跑约 9 分钟，合计约 41 分钟 |
| 工具调用 | 38 次（16 read + 22 bash） | 57 次（10 read + 47 bash） |
| decisions | 7（6 input + 1 edit） | 7（2 input + 5 edit） |
| apply | `ok:false`，changed 6；clip_03 被“请求范围复核未完成”挡下 | `ok:true`，changed 7（含 clip_03），无 blocked |
| 遗留 | 同一批 6 个非本次 target 的 unresolved clips | 同一批 6 个 |

Pi 改的是 clip_04/05/07/10/11/14；Claude 改的是 clip_03/04/05/07/10/11/14。两边的 6 个 unresolved（clip_02/06/08/09/12/13）是该集原有问题，不在本次 target 内。

## 结论

- **Pi 约快 2.5 倍**，但它的 clip_03 修复没有通过宿主的内容/范围复核；Claude 慢，但 7 个 target 全部 applied 通过。宿主独立门禁确实拦住了“快但少修”的结果。
- 这不是“Pi 不行”：它的 6 个改动合法，速度优势来自 1M/128k、无 600 秒切片和更轻的 harness；差异主要是它读得更少、对 clip_03 的范围复核不足。
- 也不建议直接把 300 集切到 Pi：输入修复这一层可以接，但要保留现有 apply/审查门，并让被 blocked 的 target 进入第二遍（Claude 或独立审修）。下一步是带 H3 的 3–5 段创作子集 A/B。

证据：`.runtime/pi-ab-20261028/`（`pi-result.json`、`claude-result.json`、两臂 `agent-run.json`/`output/repair.json`/session 日志）。
