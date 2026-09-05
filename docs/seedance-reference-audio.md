# Seedance 参考音频（实测记录，2026-09-05）

结论：`sd2.5` 的视频任务接口**接受参考音频**，用来克隆音色，不是播放参考内容。此前"接口不收音频"的判断是错的——那是照着我们客户端的代码下的结论，没有去试接口。

## 请求格式

`POST {base}/api/v3/contents/generations/tasks`，在 `content` 数组里追加：

```json
{"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,..."}, "role": "reference_audio"}
```

- `role` 必填。漏了会返回 `reference media mode requires audio role to be reference_audio`。
- 和参考图一样支持 data URI（base64 WAV，16 kHz 单声道即可），不需要外部可访问的地址。
- 可以和 `role: reference_image` 同时出现，互不影响。
- 其他写法都不认：`type: input_audio`、`type: reference_audio` 返回 `unknown type`；顶层的 `audio_url` / `reference_audio` / `audio` 会被静默忽略（任务照常提交并计费）。

## 实测效果

同一条提示词、同一句台词，只改参考音频。相似度用 CAM++ 声纹（`scripts/voice_consistency_thin.py` 用的同一个模型），对比对象是那段参考音色本身，0.55 以上一般判为同一人。

| 提交内容 | 与目标音色相似度 |
|---|---|
| 不带参考音频 | 0.44 |
| 2.1 秒的语气词「嗯」 | 0.44（等于没用） |
| 2.7 秒真实台词 | 0.60 |
| 12 秒真实台词 | 0.695 |
| 12 秒真实台词 + 角色卡 | 0.706 |

- **参考音频的长度和内容质量决定效果**：两秒的语气词毫无作用，十秒以上的连续台词能把音色拉到判定线以上。
- **克隆的是音色不是内容**：四条成片的语音识别结果都是剧本台词「我说过了这幅画不卖」，参考音频里的原话（「城南小树林我等你……」）没有出现。
- 参考音频可以直接从我们自己已出片的集里剪：把某个角色在同一集里的单说话人语音块拼到 10 秒以上即可。

## 顺带发现

任务查询返回里有 `usage.total_tokens`（5 秒 480p 一条是 48437），这是接口侧真正的计费单位。我们的 `.task.json` 边车目前没有记录它；记下来的话成本台账可以从"秒数"升级到"token 数"。
