# 官方：DS41 可选 Agent 路由

用户指定名称：**官方**，模型预设标识 `official-ds41`。

- 模型：`deepseek-v4.1-flash-1017`。
- Agent Messages base：`https://cloud.phanthy.com/phanrouter`。
- Chat Completions base：`https://cloud.phanthy.com/phanrouter/v1`。
- 凭据变量：`PHANROUTER_IMAGE_API_KEY`，复用运行项目已有的私有 `.env`，不另存凭据。
- 独立配置：`configs/agent_official_ds41.json`；首次接入按单并发配置。

## 使用

现有 `scripts/episode_agent.py run` 和 `scripts/run_agent_thin.py` 均支持 `--config`。
在原启动命令中选择以下配置即可使用这条可选路由：

```text
--config /mnt/disk1/zengzhitao/novel/worktrees/official-ds41-agent-20261021/configs/agent_official_ds41.json
```

运行项目仍使用 `/mnt/disk1/zengzhitao/novel-manga-video`；从隔离工作树启动时，
设置 `NOVEL_PROJECT_ROOT=/mnt/disk1/zengzhitao/novel-manga-video`，读取既有凭据与运行资料。

`src/novel_manga/llm/config.py` 中追加了 `official-ds41` 预设，可供当前工作树的
`--endpoint` / `NOVEL_PLANNER_ENDPOINT` 等已有模型选择入口使用。其 Chat 接口使用流式
响应，保留调用方原有输出预算，不设置额外输出下限。

默认 Agent 配置、默认规划端点及小说模型分配均保持当前设置。这份独立 Agent 配置的
H3 翻译和请求检查继续使用当前 `phanthy-ds41`；选择本配置只切换 Agent 本身的路由。

## 实际验证（2026-10-21）

- 使用此 key 请求 `deepseek-v4.1-flash-1017` 的 Chat Completions，HTTP 200，回复 `OK`。
- Native Messages 工具请求与工具结果往返均 HTTP 200。
- 项目 JSON 客户端通过新预设完成真实流式请求，6.35 秒返回
  `{"ok":true,"provider":"official-ds41"}`，默认规划端点仍为 `phanthy-ds41`。
- 使用当前主工作区的真实 `run_session`、现有 `agent-sandbox:0918` 镜像和本配置，
  Agent 调用 `Read` 读取合成状态，再调用 `Write` 写入同一 JSON：3 轮、7 秒、exit 0。
  实际输出 `{"provider":"official-ds41","marker":"OFFICIAL_DS41_AGENT_OK"}`。
- 端点预设、Agent 会话、沙箱与已有 Phanthy 回归：73 项通过。

取证目录：`/mnt/disk1/zengzhitao/novel-manga-video/.runtime/model-providers/official-ds41/`。
测试只读写独立合成文件，没有运行生产视频、图像或媒体修复任务；未验证生产并发或整集质量。
