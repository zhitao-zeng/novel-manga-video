# 流程收敛：等待和交付

本轮沿现有 `EpisodeBridge → finish → delivery` 接线，保留原 Agent、审查、预算和生成历史。未修改模型路由或批量恢复额度，未重跑已完成的 800 集。

## 已修改

- 宿主等待使用 `EpisodeBridge.operation_status()` 读取已有 future。只有任务结束才读取一次完整集状态；Agent 主动查询 `status` 的返回和交接语义保持原样。任务失败仍返回原错误。
- `finish` 把当前合成报告中的封面随视频复制到同一个 `final-candidate`，在现有 manifest/工具回执中返回对应路径。不增加内容审核；没有封面的旧收尾不能据此宣称完整远端交付。
- 正常单集入口在正式通过后，可按书目录的 `modelscope_delivery.json` 调用共用上传入口。上传失败保留正式验收和计数，只记录交付待续；重新执行正常入口会复用已经完成的生产结果。持续服务故障仍会报告待续，不宣称已上传。
- 上传只包含本集视频、对应封面和 `all-episodes.json`。保留旧播放地址和其他集条目，合并仍对应当前版本的已验证回执，避免旧索引回写覆盖先前交付。远端视频/封面大小和索引逐条回读通过才写完成回执。相同版本不重传；仅补封面不重传视频。SDK 和原 `util.retry` 处理续传及有限重试，读取索引的传播等待不会重复发送媒体。

目标数据集须已存在，凭据由原 SDK 安全存储读取。按书明确设置目标，不在全局默认上传其他书：

```json
{
  "repository": "owner/book-episodes",
  "python": "/path/to/python-with-modelscope-hub"
}
```

只续交付、不启动模型或生成：

```bash
PYTHONPATH=/path/to/novel-manga-video/src /path/to/python-with-modelscope-hub \
  -m novel_manga.application.production.modelscope_delivery \
  --novel-dir /path/to/book --run-dir /path/to/accepted-run \
  --episode 7 --repository owner/book-episodes
```

上传子进程独立直连，宿主模型及内网服务的代理环境不变。远端操作回执在 `run_dir/delivery/modelscope.json`；书级已验证交付放在 `book/delivery/modelscope/`。公开索引不添加“待审版”等操作说明。

## 验证和限制

新增回归覆盖等待成功/失败/待交接、finish 的对应封面、旧播放路径、重复交付、单补封面、远端缺文件/索引、旧索引、无目的地及上传启动失败。旧 runtime 配置快照只同步主干已有的三条模型说明文字，没有改变实际配置或测试断言。

358、473、648、739 实际已通过的现有缓存用于只读历史查询和本地远端模拟。相同 5 轮等待中历史查询 6→1，实测查询耗时分别为 0.4051→0.0662、0.6034→0.1031、0.2728→0.0454、0.6616→0.1112 秒；这不是整集生产加速比例。原计数逐项相同，新增 LLM/H3/token/积分均为零。旧 manifest 的封面字段只在隔离夹具中从当前 assembly 补入，真实 finish 接线另由集成回归验证。未为此次代码测试重新上传生产数据，不将模拟结果当作真实远端成功。

实际生产的 800 视频/800 封面交付证据沿用此前已验证记录。本批测试通过只证明本批接线；全量复审合并、只对当前依赖失效、媒体裁切及已验证修复分支的选择性整理仍按后续批次执行，不能据此宣称整个优化已完成。

本机证据：`.runtime/pipeline-streamline-20261020/` 的 `cached-replay.json`、测试日志和回归报告。

最终独立分支全量 3587 项通过，叠加当时 main 未提交改动的隔离副本全量 3762 项通过；分别为 `final-full-tests.xml`（79.485 秒）和 `integration-tests.xml`（80.965 秒）。主工作区其他 owner 的改动不在本次源码提交中。
