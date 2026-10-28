# Pi 输入修复 10 集 pilot（2026-10-28）

目的：验证 Pi adapter 在真实输入修复任务上的自动循环、宿主门禁和人工介入次数；全部在隔离副本上跑，不碰生产任务/预算。

## 设置

- 10 集：12、20、21、24、26、32、45、46、103、242；每集复制生产 run dir 到 `/mnt/disk3/zengzhitao/novel/shiri-pi-pilot-20261028/`。
- Pi 1M/128k，同 official DS41；原生 read/write/edit/bash；`episode.py` 宿主桥；render/review/finish 禁用（输入修复范围）。
- 每轮开始按宿主 status 重新生成 `current-task.json`（production 每轮也这样做）；apply blocked 时同一 Pi session 自动续跑，最多 3 轮。

## 结果（重跑 45/46 后）

| 集 | 用时 | 轮次 | 分类 |
|---|---|---|---|
| 103 | 526.4s | 1 | 修复：crop clip_06（重复人物） |
| 242 | 158.7s | 1 | 修复：edit clip_04/06（black_frames 来自请求“全黑”） |
| 32 | 605.7s | 1 | 修复：input 4 条、changed 6 clips |
| 20 | 688.3s | 2 | 修复：input/edit clip_03、clip_14 |
| 21 | 394.7s | 1 | 修复：edit clip_11 |
| 12 | 459.4s | 2 | 正确 stop：clip_08 SubmissionUncertain + 源区间额度用满 |
| 24 | 749.5s | 2 | 正确 stop：clip_10 source-range 额度被占 |
| 26 | 422.1s | 2 | 正确 stop：clip_06/clip_09 源区间额度 |
| 45 | 147.3s | 1 | 无合法 target（宿主 status 判定无未解决/可操作项） |
| 46 | 46.9s | 1 | 无合法 target（需要 review 产出当前 target；本 pilot 禁 review） |

合计约 **4199 秒（70 分钟）**，平均 **7.0 分钟/集**；**0 人工介入**；5 个实际修复、3 个正确 stop、2 个无合法 target。

## 两个关键发现

1. **第 12 集“授权丢了”是误判**：`repair_budget_grants.json` 的 clip_08 条件额度（limit 5、授权 2 次、已激活）确实存在；`source_budget` 显示 limit 5、used 4、pending 1、remaining 0——used 4 是 clip_08 3 + clip_10 1，pending 1 是 clip_08 的 SubmissionUncertain。授权没有被丢，是 **source-family 5 次上限已用满**；`conditional_extra_takes` 显示 limit 3 只是视图公式，真正上限是 `source_limit=5`。真实阻塞是未核销的 SubmissionUncertain。
2. **45/46 的 no-op 是 adapter 的旧任务快照，不是 Pi 不干活**：加每轮 `current-task` 刷新后，45 的宿主 status 明确没有可操作 target；46 需要 review 才能产出当前 target（pilot 禁 review）。两者都没有合法动作，空 apply 是正确行为；adapter 应把它们计入“无合法 target”，不能算成功修复。

## 结论

- Pi adapter 的自动续跑和宿主门禁跑通：5/10 实修、3/10 正确 stop、2/10 无合法 target、0 人工；平均 7 分钟/集。
- 3 个 stop 都是真实资源/未知问题，不是 harness；2 个 no-op 是任务范围/状态问题，不是 Pi 推理失败。
- 输入修复快路径可以继续推进，但正式切换前还需：容器隔离（当前 Pi 在宿主进程跑、key 在 env）、`source_limit/used/pending` 在 status 里显式暴露（避免“授权丢了”式误读）、以及 3 个资源阻塞的授权/核销决定。
- 创作/审看仍保留 Claude；本 pilot 不涉及 H3/render/finish/最终验收。

证据：`/mnt/disk3/zengzhitao/novel/shiri-pi-pilot-20261028/`（每集 pass*.json、pilot-report.json）、`.runtime/pi-ab-20261028/pi_pilot.py`、`host_bridge.py`。
