# 《将死之人》两版重制

第二轮样片使用《雾月秘典》第1章，分支 `codex/comic-hybrid-probe`。原成片的3个片段在选章时均有匹配当前文件的 `pass` 审查、无问题标记，且媒体/语音门通过。第一轮固定分格样片已被用户否定，保留在原目录，不再视作质量基线。

本轮按 `comic-hybrid-expression-v2.md` 重写的因果链制作：被追问但不会说异界语言 → 脑内女声赋予语言知识 → 能请求重述 → 病人交代遗产与未来取信烧信条件。新稿370字符，22段声音，恢复“现在，你有了”以及1853年9月5日的条件；删去不影响这条主线的世界观术语，没有补出烧信已经发生或主角接受交易的情节。

## 呈现

- 7张新生成的竖屏剧情图，同一卧室、同一人物、相同服装与光线；单画面顺序切镜。病人在左、莱恩在右，与原片01/03方向相同，替代设计稿初拟的相反方向。床与抓腕关系先在两人镜头中建立，再切局部。
- 四种声音分别为旁白、房间对白、莱恩心声、脑内女声；脑内声以轻微空间效果和标签区分，不画出房间里的女性角色。莱恩获得语言之前只有心声。
- 两版共用完整92.68秒声轨与字幕；时长由自然配音、回应停顿和结尾反应决定。字幕通常一行，日期在同一事件内分两行显示。没有按照旧版82.32秒补静音。
- 漫画版使用剧情图和轻微推近。混合版只将环顾反应、抓腕细节替换为无对白视频，片中不对新配音套用旧口型。
- 输出均为1080×1920、25fps、H.264/AAC。JPEG封面及尾帧同时保存。

## 动态片段边界

视频接口对“语言知识涌入”的3D插图返回 `InputImageSensitiveContentDetected.PrivacyInformation`，声称可能包含真人。没有为绕过此判断重试或修改图像；该处保留原计划的漫画反应画面。可用的新动态镜头为环顾及抓腕，本集为对话场景，不为凑20–30秒强行延长或循环动作。

旧图片通道凭据首次及运行环境核对后均返回401；可用的视频主通道 `PHANROUTER_API_KEY` 返回成功，模型为sd2.5。失败请求与已接受任务分别保留记录。没有更改生产环境凭据或正在运行的任务。

## 文件与复现

- 剧本与原文引用：`configs/comic-expression-v2.wuyue1.json`。
- 图片提示与参考关系：`configs/comic-expression-v2.images.json`；使用内置imagegen，共7张。新媒体保存在工作树，不依赖默认生成目录作为唯一副本。
- 本地语音：已有IndexTTS2.5安装、模型和参考音色；GPU5，22段原始语音71.09秒，模型加载与推理共62.39秒。后期调整语速和短停顿后声轨92.68秒。
- 脚本：`scripts/comic_pilot_tts.py`（通用配音批处理），`scripts/comic_expression_v2.py`（音轨、字幕、两版合成），`scripts/comic_expression_motion.py`（独立、有限的视频请求）。
- 产物：`outputs/comic-hybrid-v2/`，包含原文/原审查快照、逐句字符区间溯源、图像、声音、动态请求及返回记录、时间线、ASR、媒体检查和两版MP4。

使用项目原Python环境运行本地合成，不安装或更改其他项目环境：

```bash
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python scripts/comic_expression_v2.py prepare --output outputs/comic-hybrid-v2
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python scripts/comic_expression_v2.py comic --output outputs/comic-hybrid-v2
PYTHONPATH=src:scripts /mnt/disk1/zengzhitao/novel-manga-video/.venv/bin/python scripts/comic_expression_v2.py hybrid --output outputs/comic-hybrid-v2
```

`manifest.json`、原始配音和图像是一次实验的固定输入；只改字幕时可以使用 `--reuse-segments`，修改剧情图或镜头后须重新编码片段。缺少视频源会报错，不把静帧伪称为生成视频。

## 验证的实际范围

22段ASR覆盖均通过。21段标准化后无缺字；“这是……穿越了”被识别为同音近似“这时穿越了”。日期、女声回应、语言知识与烧信台词全部识别完整。逐句原文引用均在第一章找到对应字符区间。

成片检查发现 `loudnorm` 对1–2秒短句的处理会将部分语音压得极轻，ASR却仍能识别。已改为完成变速/声场效果之后逐句做峰值归一，目标-3dB；实际22段峰值均为-3dB，平均电平在-25.4至-17.7dB之间。最终声音重新识别，并核对两版AAC声轨一致。不能仅凭ASR通过就认为台词音量合格。

逐镜抽查人物位置、抓腕、脑内声没有引入新人物、语言前后反应和未来信件未被提前画出；字幕与标签位置已通过成片截图检查。媒体检查验证解码、格式、帧数和音轨时长，不代表口语表演自然度或用户观感已经通过。最终两版检查及实际视频用量见产物目录的 `validation.json`。

本轮是单章表达实验。7张图与视频均新生成，但人工设计和审看参与明显，不能用本次局部TTS/合成时间推算自动化日产能。当前没有提交或推送本实验改动，也没有接入生产队列。
