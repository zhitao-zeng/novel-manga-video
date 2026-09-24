# Project instructions

- Never store API keys in source, configuration examples, logs, or generated manifests.
- Keep generated media and mounted models out of Git.
- Preserve the source-to-shot trace in every successful episode.
- A production episode is successful only after the media quality gate passes.
- Follow the selected production profile: 1080x1920 portrait or 1920x1080 landscape, 25 fps, H.264/AAC MP4 and JPEG cover/end screens.
- For planning, assets, H3 prompts, speech or repair changes, read `docs/production-lessons.md` and the relevant linked record. Keep source/input defects, model behavior, technical checks and content acceptance distinct.
- 用户要求生成成片时，默认直接运行完整 pipeline，等待最终结果再汇总。不得边生成边人工逐段看画面、修改剧本/提示词/时长或追加定点重拍；保留 pipeline 自身已有的自动检查与重试。失败如实报告，后续修正通用流程。仅在用户明确要求某项单段实验或资产修改时执行对应局部操作，不扩展为整集人工调试。
