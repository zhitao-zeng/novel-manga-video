# 云端 H3、SD2.0 Fast / Mini

新增三个可选视频资源，既有小说的 `render_keys`、文字模型、本地H3和SD2.5默认配置均保留。

| 资源名 | API模型 | 视频凭据变量 | 入口 |
| --- | --- | --- | --- |
| `h3cloud` | `MiniMax-H3` | `PHANROUTER_H3CLOUD_API_KEY` | 标准生产/审修，以及已有H3单集Agent |
| `sd20-fast` | `sd2.0-fast` | `PHANROUTER_IMAGE_API_KEY` | 标准SD生产与有限Agent审修 |
| `sd20-mini` | `sd2.0-mini` | `PHANROUTER_IMAGE_API_KEY` | 标准SD生产与有限Agent审修 |

三个资源在 `configs/pipeline.json.resources.video_keys` 登记；各小说可显式选择其中的资源名。它们初始共用最多2个云视频在途名额，未给现有小说自动添加资源。单集任务可以在已准备的分镜和素材上使用：

十日1–300在产后，用户要求增加云端工作量：云H3资源的请求与章节并行上限提高到4，沿现有429退让机制运行；Fast/Mini调用仍保留各自2路上限。37个从未提交生成的章节改走云H3，整批分配为92集云端、208集本地；已拍和在途任务保持原路由，原计数、拍摄上限和活动时间不变。工作区的渠道快照、章节索引及生产范围同步采用同一分配。

```bash
.venv/bin/python scripts/render_clips_thin.py \
  --novel-dir /absolute/path/to/book --episode book_1 \
  --video-channel sd20-fast --review
```

将资源名换成 `sd20-mini` 或 `h3cloud` 即可选择对应通道。云H3批量入口沿原H3英文准备；直接调用渲染CLI时应已有合法的H3请求。完整H3单集流程可以使用：

```bash
.venv/bin/python scripts/episode_agent.py run \
  --novel-dir /absolute/path/to/book --episode 1 --input-mode script \
  --video-channel h3cloud
```

该单集执行器保留既有H3请求契约；Fast/Mini使用上面的标准SD入口。文字Agent仍按已有 `--config` 或默认配置选择，视频通道不隐式切换文字供应商。

每个episode保存 `video-channel.json`，仅含资源名、模型、凭据变量名与并发/时长配置，不保存凭据。恢复时复用该记录；不把已有任务或旧take静默换成另一模型。旧任务没有记录时保持原行为，只有显式选择才登记。`--dry-run`校验选路但不写入渠道记录、不提交生成。

项目对三条新通道采用15秒分组上限。超长既有计划会在选路/拍前阻塞并要求正常重新打包，不能靠截断时长省掉对白。Fast/Mini的15秒是项目保守限制，不作为上游完整规格的声明。云H3保留既有768P适配和H3最终请求校验，同时仍按付费通道处理，未启用本地免费补拍策略。

## 参考图与已提供首帧

云H3使用HTTP参考图。配置有效的 `PHANROUTER_ASSET_PUBLIC_BASE` 和对应的 `NOVEL_ASSET_PUBLISH_DIR` 后，现有图片或裁切图会通过原发布工具复制并探测可访问性；不把 `asset://` 或本机路径交给云服务。没有发布地址时，保留已有图片任务恢复托管URL的路径；缺少原任务元数据时会明确报错，不额外付费重画。

Fast/Mini默认复用现有参考素材库，需要正确的 `PHANROUTER_ASSET_GROUP_ID` 及其图片凭据。素材注册沿图片账户执行，同一张图并发只注册一次，等待 `Active` 后才进入视频请求。

已经提供开场图的任务可在profile设置 `sd_input_mode=first-frame`，并在每个video clip提供 `first_frame.path`（相对book目录）。渲染器记录实际使用的开场图并发送 `first_frame` 角色；该模式只发送一张开场图、使用原生语音，不混入多图和音色参考。这里没有引入互动剧的自动逐镜首帧制作流程。

## 共享额度与验证范围

新通道及GPT图片提交复用互动剧实际token隔离的共享额度目录。相同token跨模型/项目互认已确认的额度耗尽；不同token或换KEY不继承旧停机。普通权限错误、限流、超时和模型内容失败不等同额度耗尽；仍有可用points但不足以支付某次请求时，不将全部通道停掉。私有token标识复用原有命名空间，替代每次准入向供应商查询token身份，不进入日志或Git。

已受理任务继续按原task ID轮询和下载；未知提交保留原不重复POST规则。额度记录不提供账户金额推断，也不刷新原时间、修订或拍摄次数。

扩容实跑中出现了HTTP500但无任务ID的提交。未知回执现在同时保留HTTP状态、平台请求ID和脱敏后的错误正文，便于按原请求查因；凭据和URL不会写入这段诊断信息。该记录不改变未知判定、不自动重发，也不新增次数或时间。

本轮以MockHTTP覆盖真实provider提交、回执恢复、参考图发布、素材注册、首帧和共享额度路径；没有发送付费图片/视频请求，没有部署公网素材服务。真实通道余额、当前云生成可用性、价格、速度和整集质量尚未验证。
