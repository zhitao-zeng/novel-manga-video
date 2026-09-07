# Seedance 2.5 / 2.0 / Fast 速度探针（2026-09-07）

本轮没有观察到切换普通版 2.0 的提速收益。两组匹配样本中，2.5 都略快；样本太少，不能据此认定稳定差距，也不能外推到生产的 30 请求并发。保留当前生产模型，Fast 待取得对应模型授权后再测。

## 匹配结果

同一 PhanRouter 服务地址，15 秒、480p 档、16:9、原生音频。每组使用完全相同的提示词、冻结参考图及参考音色，每个型号并发 1，同组同时提交。返回耗时从提交开始计到轮询首次发现成功，不含后续下载和 ASR；轮询间隔为 5 秒。

| 场景 | 2.5 返回耗时 | 2.0 返回耗时 | 2.5 台词缺失比例 | 2.0 台词缺失比例 |
|---|---:|---:|---:|---:|
| 双人对白（第 54 章 clip_08） | 334.7 秒 | 348.1 秒 | 8.3% | 5.6% |
| 四阶段（第 48 章 clip_06） | 177.6 秒 | 202.7 秒 | 15.0% | 10.0% |

四条均通过媒体完整性、时长及语音覆盖检查。语音覆盖沿用项目的读音归一与最长公共子序列，未听到的剧本字符不超过 50%；识别使用现有 SenseVoice 命令，对整个片段统一识别。这个门不惩罚所有即兴加词，也不构成完整视觉审片或生产准入。

每型号仅两个匹配样本；两者对应的返回耗时中位数分别为 256.2 秒和 275.4 秒。当前可用凭据不同，可能对应不同服务端路由或分组，无法把模型计算与路由、排队影响完全分开。生产任务在测试期间继续运行，未调整其模型或并发。

## Fast API 与权限

[当前通道的公开型号列表](https://cloud.phanthy.com/phanrouter/api/pricing)列有 `sd2.0-fast`，也列有 `dreamina-seedance-2-0-fast-260128`。

实际使用的短型号调用协议与现有视频接口相同：

```text
POST /api/v3/contents/generations/tasks
model: sd2.0-fast
GET  /api/v3/contents/generations/tasks/{task_id}
```

当前生产视频令牌对 `sd2.0` 和 `sd2.0-fast` 返回 403。项目另一枚现有令牌可调用 `sd2.0`，但对压缩后的 Fast 请求仍返回 403“该令牌无权访问模型”。因此 Fast 没有创建成功的生成任务，不能报告其速度。

## 接口探针与评价修正

最初单人对白场景作为接口探针：2.5 返回 344.0 秒，2.0 返回 206.1 秒。两条使用原尺寸参考图，且因发现凭据权限而错时提交，未计入上面的匹配统计。Fast 首轮原图请求还返回过 413，请求体包含约 18 MB 的 base64 素材；后两轮统一沿用生产链的图片预处理，冻结为 720×1280、JPEG quality 82，再用于所有型号，音色 WAV 不变。

两个型号都收到 `resolution=480p`，但 2.5 原生返回 854×480，2.0 返回 864×496，均为 24 fps、H.264/AAC。最初评价误将精确 480 像素高度作为额外硬门，随后修正为单独的 `requested_resolution_exact` 诊断字段。所有六条视频已使用相同的最终评价程序复算；原始评价和修正理由保存在 `evaluation-notes.json`。生产合成仍需统一缩放与帧率，不能直接把原生片段当成已准入的成片。

## 用量与证据

共成功生成并下载六条 15 秒视频：两条接口探针、四条匹配样本。2.5 三条共返回 433,536 tokens，2.0 三条共返回 453,234 tokens，合计 886,770 tokens。服务未返回可验证的币种金额，公开模型倍率也不足以还原实际账单，未将用量直接换算成人民币。

实验目录：`.codex/research-loop/seedance-speed-20260907/`（不入 Git）。

- `manifest.json`：冻结的初始计划；`manifest-v2.json`：凭据调整记录；`manifest-v3.json`：最终执行配置。
- `api-discovery.json`、`provider-pricing.json`、`credential-capabilities.json`：型号与授权证据，不含令牌值。
- `runs/<case>/<model>/`：请求摘要、任务状态、原生视频、ASR 与媒体信息。
- `summary.json`、`report.md`：最终机器可读摘要和带样片链接的报告。
- `evaluation-runtime.json`：ASR 程序、模型路径和词表配置，不含 API 凭据。
- `runner_snapshot.py`：实验执行器最终源码快照；`tests-final.log`：216 项测试通过。

本轮研究状态核对没有发现问题。全量历史索引另有 202 条旧阶段的文件变更、缺失或元数据提示，分类记录在 `state-audit-summary.json`，完整记录在 `state-doctor-final.json`。

执行器为 `scripts/benchmark_seedance_speed.py`。它记录已有任务 ID 后可继续查询，无法确定提交结果时不会再次提交；明确无权限的型号会跳过后续场景。更新评价时使用 `--evaluate-only`，只读取已有视频，不发送生成请求。

```bash
PYTHONPATH=src:scripts .venv/bin/python scripts/benchmark_seedance_speed.py \
  --experiment-dir .codex/research-loop/seedance-speed-20260907 \
  --manifest manifest-v3.json --evaluate-only
```

复算需要配置记录中的 `NOVEL_ASR_COMMAND`、`NOVEL_SENSEVOICE_MODEL_DIR` 等 ASR 环境变量；模型生成凭据仅在生成/轮询时使用，不写入实验文件。
