# ASR合成交接修复（2026-10-08）

首拍报告的selected可能只保留video/passed等元数据。Agent收尾直接把它传给合成器时，真实字幕路径 `story_segments → subtitle_events` 访问 `analysis['chunks']`，因缺少完整分析而报KeyError；同take目录里的ASR缓存实际完整。这属于宿主数据交接问题，不需要重拍或让Agent重写对白。

## 改动

`EpisodeTools._assemble` 保留既有请求匹配检查，在合成前对缺少有效chunks数组的selected调用现有 `runner.analyse_clip(current_clip, selected_video)`。缓存以选中视频的父目录为准，不能按缓存内部旧video路径去另一个take取数据；现有analyser负责更新参考对白/旧语音规则，缓存缺失时走原ASR分析。

补齐结果与原selected元数据合并，真实语音通过/失败同步更新gate_failed_clips。已有完整chunks直接复用，无对白的合法空数组不会再次分析。恢复后的证据先写回报告，再进入编码，编码失败不会丢掉补齐的数据。没有把字幕读取改成默认空数组，没有更改语音容差、拍摄权限或拍摄额度。

## 实际恢复结果

通过现有 `_finish_existing` 收尾，在原run_dir、现有章节锁和原模型配置下执行；没有启动Agent创作或重试整个任务池。验证期间禁止调用视频生成和runner.run，并对原始take文件大小/修改时间及真实拍摄计数做前后核对。

| 章节 | 补齐selected分析数 | 实际收尾耗时 | 正式验收 | 新增拍摄 |
|---|---:|---:|---|---:|
| 521 | 7 | 70.024秒 | 通过 | 0 |
| 606 | 13 | 96.651秒 | 通过 | 0 |
| 623 | 15 | 93.278秒 | 通过 | 0 |
| 747 | 10 | 80.307秒 | 通过 | 0 |

共45份分析补齐，四章均无缺失chunks；原始视频文件和拍摄计数保持不变。通过发生在原隔离工作区，尚未镜像到主发布目录，所以没有将主目录625改写为629。137集重试批保持停止。

原89集优先清单已包含这四章；另准备 `.runtime/guangming-retry-20261008/retry-after-asr-85-commands.txt` 排除已完成四章。原137/89/48清单保留。后续启动前仍须明确文字时间恢复方式，不能把 `--retry-issues` 当作原剩余时间续接。

## 验证与边界

- 五个新增用例先在真实字幕chunks访问处失败；修复后相关existing/assembly/CLI共99项通过。覆盖当前take、缓存旧路径、过期参考、缓存缺失、无对白空数组、完整分析不重复处理，以及重新读出的真实语音失败继续阻止验收。
- 一次独立最终复核没有Critical/Important或确定的Minor问题。编码中断后证据保留的顺序已复核，未另做编码中断实验。
- 全量2847项中2841通过、6失败、0错误/跳过；6项是并行人物/素材修改的循环依赖、跨测试导入及4项资产行为，失败路径不涉及本次 `_assemble`。不能声称整个工作树通过回归，也未改动那些并行工作。具体清单已单列在任务工作区 `other-work-failures.json`。

实际结果位于 `/mnt/disk1/zengzhitao/novel-manga-video/.runtime/guangming-asr-handoff-20261008/summary.json`。源修改在feature工作树，未提交、推送或更新旧V15快照，没有重启模型服务或批量生产。
