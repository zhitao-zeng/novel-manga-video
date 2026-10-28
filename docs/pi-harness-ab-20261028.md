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

## 第二轮：把 apply 结果喂回同一会话

第一轮的 Pi 只改了 6 个，clip_03 被范围复核挡下。把宿主 apply 结果（changed 6、clip_03 blocked）作为续跑提示交回同一个 Pi session，不换模型、不重置预算：

- Pi 第二轮：**166.7 秒、11 次工具**，apply `changed:['clip_03']`、`blocked:{}`；
- Pi 总 Agent 时间：894.6 + 166.7 = **1061.3 秒（约 17.7 分钟）**，7 个 target 全部 applied（6 + 1）；
- Claude：首次 1800 秒超时，原会话续跑 369.5 秒，总 Agent 时间约 **2169.5 秒（约 36.2 分钟）**，7 个 applied。

因此“快但没修全”不是 Pi 的终局：**宿主 apply 挡住 clip_03 后，继续同一 session 就能补上，总时间仍约为 Claude 的一半。** 关键是不能在第一次 apply 后就停；门禁负责发现漏修，续跑负责补齐。

## 结论

- **Pi 在有续跑时约快 2 倍，并能把 7 个 target 修全**；Claude 慢约一倍，但一次 apply 就全过。
- 宿主独立门禁是这套快路径成立的前提：它拦住了 Pi 第一轮的 clip_03，第二轮才补上；没有门禁就会把“快但少修”当成完成。
- 当前仍是单集、输入修复、无 H3、无最终验收的样本；`media.status` 仍是 `clips_failed`，非 target 的地点绑定等问题仍在，不能说整集通过。
- 建议：输入修复可以接 Pi 快路径 + 宿主门禁 + 对 blocked target 自动续跑；Claude/独立审修保留为第二遍和验收。下一步做带 H3 的 3–5 段创作子集 A/B，仍以“通过验收”而不是“写出文件”为终点。

证据：`.runtime/pi-ab-20261028/`（`pi-result.json`、`claude-result.json`、两臂 `agent-run.json`/`output/repair.json`/session 日志）。
