# Project instructions

- Never store API keys in source, configuration examples, logs, or generated manifests.
- Keep generated media and mounted models out of Git.
- Preserve the source-to-shot trace in every successful episode.
- A production episode is successful only after the media quality gate passes.
- Follow the selected production profile: 1080x1920 portrait or 1920x1080 landscape, 25 fps, H.264/AAC MP4 and JPEG cover/end screens.
- For planning, assets, H3 prompts, speech or repair changes, read `docs/production-lessons.md` and the relevant linked record. Keep source/input defects, model behavior, technical checks and content acceptance distinct.
- 用户要求生成成片时，默认直接运行完整 pipeline，等待最终结果再汇总。不得边生成边人工逐段看画面、修改剧本/提示词/时长或追加定点重拍；保留 pipeline 自身已有的自动检查与重试。失败如实报告，后续修正通用流程。仅在用户明确要求某项单段实验或资产修改时执行对应局部操作，不扩展为整集人工调试。

## 多会话协作（本项目同时有多个 agent 会话，必读）

1. **干活先开 worktree**：在主工作区改 main 的代码会和别的会话相撞。`git worktree add ../worktrees/<任务名> -b <分支名>`，分支名即所有权。
2. **开工登记、收工销账**：`docs/active-sessions.md` 是当前在途工作的唯一事实来源。开工写一行（谁/干什么/在哪个分支/动哪些文件/预计多久），收工写清做到哪、什么没提交、下一步是什么。改共享文件（`configs/pipeline.json`、`src/novel_manga/planning/`、`src/novel_manga/application/` 主干文件）前先看它。
3. **小步快进**：分支活着就每天合一次 main，做完立刻回合。攒着不合的代价已付过（48 处冲突）。
4. **别人的未提交改动 = 别人的工作**：不 reset、不覆盖、不代为提交；挡住你了就在在途板留言或问用户。
5. **生产目录写归属**：`.runtime/` 和 `outputs/` 下新建的运行目录带一个 `owner` 说明（谁起的、在产还是实验）。
