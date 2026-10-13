# Codex Luna 备用入口

显式使用本机 `codex exec -m gpt-6-luna` 执行一次原 Episode Agent 命令；子步骤的文字与图片模型请求也走相同备用入口。普通 `scripts/episode_agent.py` 不启用它，默认 GLM / Phanthy Luna 路由不变，不自动因内容失败切换账号。

```bash
.venv/bin/python scripts/standby/codex_luna/run.py --help
.venv/bin/python scripts/standby/codex_luna/run.py run --novel-dir outputs/your-book --episode 1
```

实际运行沿用目标任务已有许可与原宿主 check/apply/render/review/finish。不会添加授权、清计数、绕过验收或自动发布。确认目标没有另一制作进程后，使用原任务的实际参数；不在此入口另起重复任务。

使用 PATH 中的 Codex CLI 与当前登录，不复制凭据、不改全局设置。文字/图片请求、实际模型、实际用量和完成情况默认保存在本工作树 `.runtime/codex-luna-standby/`；可用 `NOVEL_CODEX_STANDBY_DIR` 指定本次证据目录。Agent 会话仍保存在原任务 attempts 中，完整旧候选和会话留档，每次使用当前 Skill 与精简上下文。

这不是 Phanthy 的同名模型。CLI 没有在本项目验证过调用方精确控制单次输出 token 的能力；请求上限只作为提示记录，实际消耗另记，不能据此宣称等同 HTTP 硬上限。原宿主操作次数与时间限制继续执行。

来源为已用于光明交付的 `codex_luna_20261012` 私有适配器；本次去掉专用机器/日期/书名路径、过期审核指令，补充普通入口与子进程接线测试。当前版本验证为本地模拟测试和 CLI 帮助读取，未新增真实模型或 H3 调用。原真实运行证据仍在历史 runtime。
