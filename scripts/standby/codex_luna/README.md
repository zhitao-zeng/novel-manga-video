# Codex Luna 备用入口

显式使用本机 `codex exec -m gpt-6-luna` 执行一次原 Episode Agent 命令；子步骤的文字与图片模型请求也走相同备用入口。普通 `scripts/episode_agent.py` 不启用它，默认 GLM / Phanthy Luna 路由不变，不自动因内容失败切换账号。

```bash
.venv/bin/python scripts/standby/codex_luna/run.py --help
.venv/bin/python scripts/standby/codex_luna/run.py run --novel-dir outputs/your-book --episode 1
```

实际运行沿用目标任务已有许可与原宿主 check/apply/render/review/finish。不会添加授权、清计数、绕过验收或自动发布。确认目标没有另一制作进程后，使用原任务的实际参数；不在此入口另起重复任务。

使用 PATH 中的 Codex CLI 与当前登录，不复制凭据、不改全局设置。文字/图片请求、实际模型、实际用量和完成情况默认保存在本工作树 `.runtime/codex-luna-standby/`；可用 `NOVEL_CODEX_STANDBY_DIR` 指定本次证据目录。Agent 会话仍保存在原任务 attempts 中，完整旧候选和会话留档，每次使用当前 Skill 与精简上下文。

这不是 Phanthy 的同名模型。CLI 没有在本项目验证过调用方精确控制单次输出 token 的能力；请求上限只作为提示记录，实际消耗另记，不能据此宣称等同 HTTP 硬上限。原宿主操作次数与时间限制继续执行。

子进程保留此入口的 `PYTHONPATH`，规划、建卡审查和宿主模型调用继承同一显式备用。普通 JSON 请求会使用原有字段约束；不支持的可选字段或位置数组保留原协议与宿主校验。

来源为光明交付使用的 `codex_luna_20261012`；已去掉专用机器/日期/书名路径和过期审核指令。《傲世九重天》首集已有真实文字、图片理解和 H3 宿主接续证据；接通或生成不等于成片验收通过，具体结果以任务 manifest 为准。
