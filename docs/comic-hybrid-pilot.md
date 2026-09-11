# 关键镜头混合版与有声动态漫画：首轮样片

**状态：用户已否定本轮观感。两版不连贯、难以理解，固定三格没有叙事作用。下文仅保留技术过程和耗时记录，不能作为方案质量成立的证据。修订方向见 `docs/comic-hybrid-expression-v2.md`。**

分支 `codex/comic-hybrid-probe`，基于 `b49a8e4`。原生产工作区的未提交修改保留在原处，没有复制为实验实现。

首轮使用《雾月秘典》第1集《将死之人》：原片82.326秒，3个片段全部审为 `pass`，`flags=[]`，媒体、语音门通过，审查对应当前选用的视频。源码入口会拒绝有标记、未完整审查或审查已过期的来源，符合本轮“选择没有标问题的已有章节”的要求。

## 两版结果

| 版本 | 时长 | 呈现 | 动态视频 |
|---|---:|---|---:|
| 方案2：关键镜头混合版 | 82.32秒 | 分格画面、推拉、角色配音，部分关键镜头替换为视频 | 23.52秒，复用原片 |
| 方案3：有声动态漫画 | 82.32秒 | 分格画面、局部推拉、角色配音 | 0秒 |

两版使用完全相同的14场景脚本、时间轴与新配音。保留原剧本对白；两个无对白动作场景补充了有原文依据的简短旁白，引用记录保存在 `content_trace.json`。镜头时间轴接近原片时长，配音不足的部分留作阅读与停顿，配音较长时不得截断。

输出均为1080×1920、25fps、H.264/AAC；封面、结束画面为JPEG。片段画面置于漫画分格中，图片局部缓慢推拉，字幕及说话人固定。混合版主画格内播放关键段视频，其他部分与漫画版共用。视频口型没有重新匹配新配音，需在观感审查时注意。

## 计时与成本口径

- 冻结素材及抽帧：1.46秒。
- IndexTTS-2.5新配音：48.55秒，包含17.65秒模型载入；复用现有离线模型和项目专用环境，GPU5单进程峰值约5.3GB。
- 两版合成合计：46.24秒。其中漫画片段编码25.75秒，混合版额外片段编码7.79秒，其他为共同音频处理、拼接和输出检查。
- 本轮未请求新的图片或视频生成；画面来自已通过审片的原片抽帧，动态段也复用原片。

这次测的是**素材已备好的呈现原型和增量制作开销**。上述约96秒不能当作“从小说到两份成片”的全流程速度，原图、原视频、剧本与参考音色的取得成本没有重跑。正式产能对照需要另测：剧情图如何生成/复用、关键视频怎样生成、首次与后续章节的资产成本，以及配音与视觉可用率。

## 验证范围

- 原片没有审片问题标记，且审查完整、对应当前素材。
- 两版时长相同，格式检查通过。
- 共用新声轨的14场景全部通过项目语音覆盖门；ASR存在少量同音字差异，记录在 `speech_check.json`。
- 这些检查不构成最终观众评价或生产准入，样片按 `preview` 保存。
- 新增6项回归测试通过。全量检查为218通过、4失败；将4项失败单独运行、不加载新测试后仍可复现：3项旧接口测试的配置替身缺少新增的 `request_timeout` 字段，1项仍要求拒绝目前已支持的 `sd2.0`。这些被测模块与测试均来自基线 `b49a8e4`，本实验没有改动，详见 `tests.log` 和 `baseline-failures.log`。

## 复现

在本独立工作区运行，控制器复用原项目 `.venv`，通过 `PYTHONPATH=src:scripts` 指向本分支代码。TTS复用现有IndexTTS专用环境，不向其安装依赖。

```bash
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python \
  scripts/comic_pilot.py prepare --config configs/comic-pilot.wuyue1.json

CUDA_VISIBLE_DEVICES=5 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=/mnt/disk2/zengzhitao/repos/index-tts OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
/mnt/disk2/zengzhitao/repos/index-tts/.venv-probe/bin/python scripts/comic_pilot_tts.py \
  --manifest outputs/comic-hybrid-pilot/manifest.json \
  --repo /mnt/disk2/zengzhitao/repos/index-tts \
  --model-dir /mnt/disk2/zengzhitao/models/novel-manga-video/IndexTTS-2.5

PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python \
  scripts/comic_pilot.py render --config configs/comic-pilot.wuyue1.json
```

GPU编号为本轮运行位置，重新运行前须确认剩余资源。已有配音会复用，复用记录不能用于推算首次生成耗时；换脚本或音色时应使用新的输出目录。

产物目录 `outputs/comic-hybrid-pilot/` 不入Git：`wuyue_1_hybrid.mp4`、`wuyue_1_comic.mp4`、`pilot_report.json`、`timeline.json`、`tts_report.json`、`speech_check.json`、`content_trace.json` 以及冻结源素材与审查记录。
