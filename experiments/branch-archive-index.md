# 独立实验与退休工作树索引

整理于 2026-09-28。这里登记历史和独立方案，不作为生产入口；不是待自动合并的工作列表。

## 继续保留的独立实验

| 实验 | 分支 / 基线 | 本地位置 | 状态与用途 |
|---|---|---|---|
| 六技能、分场与参考图实验 | `exp/agent-skill-writing` / `c393940` | `/mnt/disk1/zengzhitao/novel/worktrees/agent-skill` | 完整 284 文件实验源继续保存；主线精选归档见 [agent-skill-sandbox](agent-skill-sandbox/README.md) |
| 有声漫画与关键镜头混合版 | `codex/comic-hybrid-probe` / `cbd372e` | `/mnt/disk1/zengzhitao/novel/worktrees/comic-hybrid` | 独立样片。首轮被否定；后续结果看该分支复盘，不视为已验证生产方案 |
| 离线 A100 单镜像方案 | `codex/offline-a100` / `164bc79`，含未提交草稿 | `/mnt/disk1/zengzhitao/novel/worktrees/offline-a100` | 保留 Qwen / Wan / VoxCPM 和运行环境草稿，不恢复旧 API 到现有生产 |
| 斗破首章样片 | `exp/doupo-samples` / `473ffea` | Git 分支，无独立工作树 | 两份 3D / 真人样片脚本；重新使用时需适配当前供应商 |
| 早期 VLM 导演与整集账本 | `codex/api-version` / `fffe900`；`codex/scene-master-skill-eval` / `09aca6b` | Git 分支，无独立工作树 | 二者的独立最终代码差异相同；仅保留历史，不计作两套待接入功能 |
| 9 月 19 日未提交快照 | `wip/uncommitted-0919` / `19a2f65` | Git 分支，无独立工作树 | 53 个文件均在主线当前或历史有原样版本，仅作备份 |

以上分支、实验文件和 3 份历史 stash 均未删除。离线工作树的未提交内容没有被本次自动提交或上传；原作者继续拥有这些草稿。

## 已退休的 7 个工作树

已确认退休前没有进程以这些目录及子目录为当前工作目录。全部保留原目录与忽略产物，只取消 Git 工作树登记，删除已完整合入 main 的本地分支名。没有搬动视频、缓存或实验产物路径。

| 原分支 | 基线提交 | 封存目录 | 是否有草稿 |
|---|---|---|---|
| `claude/tag-names-traits` | `6cb9908` | `/mnt/disk1/zengzhitao/novel/worktrees/tagnames-fix` | 无 |
| `codex/final-request-semantic-review` | `1b7c687` | `.codex/worktrees/final-request-semantic-review` | 无 |
| `codex/review-repair-followup` | `8c6ca86` | `.codex/worktrees/review-repair-followup` | 无 |
| `claude/pipeline-gates` | `7b0e911` | `/mnt/disk1/zengzhitao/novel/worktrees/pipeline-gates` | 22 项 |
| `claude/posture-ledger` | `7b0e911` | `/mnt/disk1/zengzhitao/novel/worktrees/posture-ledger` | 27 项 |
| `codex/review-repair-contract` | `427c091` | `.codex/worktrees/review-repair-contract` | 8 项 |
| `exp/h3-prohibitions` | `4f9568d` | `/mnt/disk1/zengzhitao/novel/worktrees/exp-proh` | 2 项 |

相对路径基于本项目根目录。封存目录中有 `ARCHIVED_WORKTREE.md`，原 `.git` 引用保存为 `GIT_WORKTREE_REFERENCE.retired`，不再是可执行生产或直接操作 Git 的工作树。不要在封存目录直接运行 Git，以免误选父仓库。

每份工作树的基线、未提交清单、未跟踪文件清单和分开的 staged / unstaged / HEAD-to-worktree 二进制补丁保存在本机 `.codex/retired-worktrees/20260928/<分支名，将斜线替换为双下划线>/`。这些恢复资料和原产物在本机保留，不上传 GitHub。

恢复时从项目仓库以记录的完整基线提交创建一个新的 `codex/` 工作树；需要恢复草稿时按顺序应用 staged 和 unstaged 补丁，复制清单中的未跟踪文件。也可只用 HEAD-to-worktree 补丁恢复最终内容；两种补丁方式二选一，不能重复应用。产物可继续从原封存位置读取，不应复制大体积模型和视频进 Git。

## 整理后的边界

- 本地分支 15 → 8；已登记工作树 11 → 4（主线 + 三条独立实验）。
- 远端旧分支没有删除；本次只推送主线成果和索引。
- 主工作树的 `configs/pipeline.json` 保持原有未提交状态。
- 没有重启生成、规划、修复任务，没有清空缓存、预算、历史或暂停标记。
