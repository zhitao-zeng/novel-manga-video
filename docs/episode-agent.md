# 独立 Agent 产线

本分支增加按集运行的 Claude Code 沙箱入口。正式批量控制器与看板保留原有操作方式；Agent 入口由操作者显式启动。

每集使用独立工作区与持续会话。Agent 调用 `check`、`render`、`status`、`review`、`apply`、`finish`，自行安排创作与修复。主机执行现有视频供应商、缓存、ASR、合成和独立审查，保留实际生成次数与停止条件。

批量资源调度、同会话工具交接与暂停恢复见[Agent 集级制作与共享调度](episode-agent-scheduling.md)。

## 接入已有产物

```bash
python scripts/episode_agent.py prepare \
  --novel-dir outputs/你的小说 --episode 36 --input-mode script

python scripts/episode_agent.py run \
  --novel-dir outputs/你的小说 --episode 36 --input-mode script
```

`prepare` 只准备输入，不调用模型或生成服务。`script` 模式读取现有 `chapter_script.json`、人物库、读书记录、资产与片段计划，由 Agent 编写最终 H3 请求；现有对白、归属、分组、引用顺序和时长继续作为输入约束。缺少片段计划时使用原有打包能力确定性生成，所需资料缺失则报告原因。

H3 单次请求最多15秒。保存的计划若含超过该限制的片段，需要先按 H3 重新打包，入口会明确报错，保留原计划。

历史小说目录、剧本、素材与视频保留。准备过程写入独立的 Agent 输出目录，复制已有文件并复用该输出树内的资产快照；不会向原资产目录写入修复。

## 从原文创作

```bash
python scripts/episode_agent.py run \
  --novel-dir outputs/你的小说 --episode 36 --input-mode source
```

`source` 模式提供完整原文、读书证据和实际资产，Agent 自主写剧本、分镜和最终请求。它不读取旧版剧本与旧提示词来模仿镜头。`auto` 为默认模式：已有剧本时选择 `script`，否则选择 `source`。

## 按批准备与运行

```bash
python scripts/episode_agent.py prepare \
  --novel-dir outputs/你的小说 --chapters 1-20 --input-mode auto

python scripts/episode_agent.py run \
  --novel-dir outputs/你的小说 --chapters 1-20 --input-mode auto

python scripts/episode_agent.py status \
  --novel-dir outputs/你的小说 --chapters 1-20
```

入口按已有章节编号逐集操作，不创建另一套任务队列。每集会话、预算和缓存独立保存；重复运行续用已完成的素材与历史。

## 配置与独立工作树

模型、凭据变量名、沙箱镜像来自现有 `configs/agent_sandbox.json`；视频资源使用已有运行配置。小说的画幅、风格、语音口径和质检配置保留。凭据通过环境或项目现有配置加载，输入包和日志不包含密钥。

代码位于独立工作树而运行配置仍在原项目时，用 `--project-root /运行项目目录`。技能可从代码工作树加载；用 `--output-root /独立产物目录` 指定产物位置。`status` 只读取状态，不启动沙箱或任务。

## 验收与已知边界

技术通过、语音门通过和内容审查通过分别记录。Agent 不能自行把未解决问题标成通过；实际检查与当前产物决定 `finish` 结果。调用收到不确定提交结果时，继续沿用供应商原有恢复机制。

首次完整实验使用《美漫》第36章原文，自主产出14段、约152秒成片。技术与正式语音检查通过，内容审查13/14；一段胡须差异在三次主视频额度后仍未通过。该样本说明整集 Agent 工作方式可执行，尚不能证明所有章节质量或速度稳定。

Claude Code 是运行器名称，实际模型由沙箱配置决定；该次实验使用现有 Qwen3.8-Flash-Next。
