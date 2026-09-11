# 《将死之人》横屏两版

按用户要求将第二轮漫画版与关键镜头混合版制作成1920×1080、16:9横屏，25fps、H.264/AAC。原来的完全生成版 `outputs/wuyue/wuyue_1/wuyue_1.mp4` 本来就是1920×1080横屏。

两版沿用修复短句音量后的92.68秒配音、逐句时间线和原文溯源。重新构图并采用7张横屏剧情图：先交代病人左侧卧床、莱恩右侧床边与抓腕关系，再切人物反应、环境和手部。人物身份、服装和房间保持同一套设定，字幕按横屏重新定位。旧竖屏成片保留在 `outputs/comic-hybrid-v2/`。

横屏图片使用内置imagegen，7张采用图共8次调用。首次环境插图误加了人物且病人外观不一致，检查时发现后重做为空镜，错误图留在 `inspection/room-rejected.png`。全部初始提示见 `configs/comic-landscape.images.json`，环境图最终修订提示见 `configs/comic-landscape.room-correction.json`。项目图像在输出目录中保存，不依赖默认生成目录作为唯一副本。

混合版的新动态画面为环顾反应和抓腕，共10.84秒。两条横屏视频各请求6秒，模型sd2.5、480p档，随后统一编码到1080p；实际生成用量合计116090 tokens，返回时间约136秒与153秒。使用图片参照生成无对白动作，声音仍与漫画版相同。上一轮被接口拒绝的语言冲击视频没有重新请求，该处继续使用剧情图。请求计费币种及图片token用量未返回，不作换算。

输出目录为 `outputs/comic-hybrid-landscape/`，主要文件：

- `wuyue_1_comic_v2.mp4`、`wuyue_1_hybrid_v2.mp4`。
- `comic_cover.jpeg`、`comic_ending.jpeg`、混合版对应JPEG。
- `timeline.json`、`content_trace.json`、`voice_master.wav`、`subtitles.ass`。
- `validation.json` 和两版各自的媒体报告。

渲染器新增 `--landscape`，视频请求脚本新增 `--ratio 16:9`。横屏要求使用独立输出目录及已构图的横屏图片；不要把竖屏成片目录当作横屏缓存使用。原竖屏字幕生成结果已做逐字节对比，默认行为保持一致。

```bash
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python scripts/comic_expression_v2.py comic --output outputs/comic-hybrid-landscape --landscape
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python scripts/comic_expression_v2.py hybrid --output outputs/comic-hybrid-landscape --landscape --reuse-segments
```

本轮检查侧重横屏构图、字幕、解码、分辨率、帧数、声轨与原竖版的一致性；语音内容与原文对应关系沿用已验证的同一声轨，通过文件和最终AAC数据比对确认。本轮没有重新生成配音，也没有以自动检查代替用户观感评价。

分支仍为 `codex/comic-hybrid-probe`，本轮没有提交或推送，没有更改原生产工作区或队列。
