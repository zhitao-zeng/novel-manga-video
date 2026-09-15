# 雾月上传与星海 H3 修复启动

2026-09-15。用户授权上传雾月，并开始星海的 H3 修复；随后明确星海本轮也采用“语音只记录、不阻挡”的口径。本轮代码未提交或推送。

## 雾月 ModelScope 上传

目标为公开数据集：[Flame4pd/wuyue-midian-episodes](https://modelscope.cn/datasets/Flame4pd/wuyue-midian-episodes)，沿用已有诸天和星海数据集的账号与发布方式。

- 本次目标是 2,043 个已发布成片、2,043 张封面，加 README、逐集索引和质量口径，共 **4,089 个文件**。
- 视频总大小 **135,391,008,588 字节（135.39 GB）**，总时长约 **71.64 小时**。
- 上传前逐集确认当前成片技术状态为 done，且没有待发布候选。只收集最终电影和封面，不包括小说原文、工作缓存、模型、源码或凭据。
- 快照在 `outputs/wuyue/modelscope_export_20260915/`，媒体使用硬链接保留当前版本，避免另复制约 135 GB。SDK 的上传缓存不属于发布内容，上传允许列表和最终核验也排除了它。
- 使用现有 `/mnt/disk1/zengzhitao/ms_venv` 和 `modelscope_hub` SDK，凭据由现有安全存储读取。没有安装环境或复制令牌。上传命令独立直连，不使用会话开发代理传大文件。
- 上传并发为 32；已存在的文件或内容块复用，按 SDK 的可续传流程执行。完成后逐文件比对远端路径和字节大小；未核验完不会标记 complete。
- 已传输内容块与已提交到数据集的文件分别计数：后者是成批更新，因此在某批等待较慢文件时，已传输数可能明显领先。
- 常驻续传监督进程在工作进程异常退出后至多自动续传 3 次；认证错误会保留需要处理的状态，不无限重试。它不会发送外部消息。

运行资料在 `outputs/wuyue/repair_manager/modelscope-upload-20260915/`：

- `manifest.json`：冻结的发布范围。
- `upload.py`、`status.json`、`upload.log`：上传、实时计数、日志。
- `supervise.py`、`supervisor.json`：断点续传监督。
- 完成后生成 `remote-files.json` 并将 `status.json` 标为 complete。

`outputs/wuyue/modelscope_upload.json` 指向当前上传状态；看板会持续显示上传进度。数据量较大，启动和部分文件已提交都不等于整本上传完成。

## 星海 H3 修复

已完成的精判覆盖 **3,836 个 H3 片段、376 集**，其中初始问题为 **501 段、243 集**。来源按当前视频的 `.task.json` 中实际模型核对。

修复继续使用 `scripts/manage_repair_thin.py`，唯一状态在 `outputs/xinghai/repair_manager/state.json`：

- `scope.episodes` 固定为这 376 集，刷新、派单和进度统计都限制在该范围；不扫描或修改其余 SD 章节。
- 7 集还缺少 11 个从未生成的片段。当前星海配置使用 H3 产线，这些片段随所属 H3 集补齐；不是把既有 SD 视频换成 H3。范围中的计划片段初始共 3,847 段。
- 初始 243 个问题集进入原文/请求核对，再按当前画面决定保留旧片、改分镜或重拍。已审结果复用，不重新启动全书扫描。
- 保持单集独占、最多 24 集修复在途、12 集补渲生成及原有阶段容量，共用 H3 资源池。仅调用现有实例，没有改动 GPU 服务部署。
- 对已有且已审的片段保留精确的参考绑定和缓存；只在修复准备或新缺失素材中整理为主卡，避免因为批量移除旧表情图而重渲本来合格的片段。新片段不新增表情卡。
- 用户确认后，`scope.speech_gate=observe`。语音缺失、多说和静音比例/长静音只观察；黑屏、冻结、损坏等其余门仍有效。该覆盖仅适用于范围内章节，SD 章节仍按原设置执行。
- 看板标注“H3 修复范围”，不会把 376 集冒充星海全书；语音口径也显示为只观察。

`repair_launch_20260915.json` 保存起始范围、任务名单和初始统计；`non-h3-baseline.json` 保存范围外计划、剧本、审查和媒体报告的修改时间与大小。启动及口径调整后已核对，范围外文件无变化。

接续命令（项目目录内）：

```bash
.venv/bin/python scripts/manage_repair_thin.py status --novel-dir outputs/xinghai
.venv/bin/python scripts/manage_repair_thin.py pause --novel-dir outputs/xinghai
.venv/bin/python scripts/manage_repair_thin.py resume --novel-dir outputs/xinghai
# 仅当总控进程已退出时启动；已有子任务由保存的状态接管
.venv/bin/python scripts/manage_repair_thin.py run --novel-dir outputs/xinghai --legacy-dir /mnt/disk1/zengzhitao/tmp/xinghai-repair
```

## 验证

相关测试覆盖范围之外不读写、不派单；只整理目标片段的主卡；已审旧片缓存保留；H3 语音/静音观察不改变 SD 的门；黑屏与冻结仍阻挡；原有段级记录、复审、拆段和发布流程。测试结果保存在 `outputs/xinghai/repair_manager/scoped-repair-tests-final.xml`。

实际运行已看到星海任务进入准备、生成和复审，部分集整集通过；其余任务仍在后台。雾月也已有视频文件在远端提交，后续以上传状态与逐文件核验结果为准。

20:48 交接快照：雾月已传输 251 集 / 14.71 GB，其中远端已提交 131 集 / 7.81 GB；上传及续传监督进程均存活，尚未完成。星海 H3 范围当前可交付 183 / 376，确认问题 322 段，25 个运行或待运行步骤，另 116 集初始目标仍待领取。范围外文件变动为 0。相关回归 152 项通过；浏览器验证了上传区块、H3 范围和语音观察提示，页面无脚本错误。
