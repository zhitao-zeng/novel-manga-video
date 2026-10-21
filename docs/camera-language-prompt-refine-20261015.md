# 复杂运镜 prompt 续测（2026-10-15）

本轮继续验证提示词的改善空间，**不把未完成的镜头直接归为模型能力上限**。9项复杂手法加推近停机对照，共36条8秒H3请求、288秒请求时长：29条首批、2条修正本次输入错误、1条只改summary、4条另一种子的原/改配对。全部原片与退化结果保留。

有局部改善，但没有新增一组满足所有造型、裁切、路径和停止要求的可靠模板。演员是否站住、观察面是否绕过头、脸/全身是否在画内、放大的节奏及末段保持，都能被prompt改写改变；纯光学变焦、眩晕变焦、精确角度和停机仍未充分验证。

## 先看对照

- [1分52秒三栏无声对照（含输入纠错和另一种子）](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/prompt-refinement-comparison-silent.mp4)
- [逐项播放器与36份完整prompt](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/index.html)
- [中文说明与全部镜头正文](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/prompts.md)
- [36份完整六段请求文本](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/all-prompts.json)

## 每项改善与未完成部分

| 编号与手法 | 本轮判断 | 改善或观察 | 仍未满足 |
|---|---|---|---|
| M03 横移 Truck | 演员站住有改善 | A/B不再让甲自行迈步；手部与脚底固定关系更清楚。 | 仍出现弧绕，A还裁头；未验证为纯直线横移。 |
| M08 弧移 Arc | 侧面终点更接近 | 2101的A/B未再绕到背面；2102的A也到指定鼻朝右侧面。 | 2102对照自身也不再像2101那样大幅过绕；A仍裁切过紧、后半段微动，不能称准时停稳。 |
| O05 光学变焦 Zoom | 取景/保持局部改善 | B从较近景别开始，后段尺度较稳，且没有上次改写新增的人物副本。 | 景物未随人物同等比例放大，固定视点光学变焦未证实；目标倍率较原任务缩小。 |
| O06 急变焦 Crash Zoom | 节奏改善不稳定 | 2101的修正B2有先保持、约2.75–3.0秒短促收紧、后段保持；脸较完整。 | 2102对照也能快速收紧，新写法没有同样明确的节奏优势；触发时间不固定，仍有急推/变焦歧义。 |
| O07 眩晕变焦 Dolly Zoom | 本轮仍未验证成功 | 分别试原后拉/拉近焦距、相反的前进/拉广，以及只改summary。 | A头变小、B头变大；C头较稳却仍背景换面。均不足以确认所需主体保形与背景相对伸缩。 |
| X02 多角色接力展示 | 没有进一步改善 | B保留三人和甲→乙→女性顺序。 | A新增两名背景人，比当前对照差；B仍未停住，女性造型改变。 |
| X06 机身滚转 Roll | 新写法退化 | 当前对照仍是较接近90度侧置的版本。 | A/B越过侧置继续倒转，A还出现异常上下背景/绕行；不采用新稿。 |
| X07 主体中心随身 Snorricam | 胸像与背景运动改善 | A恢复脸、双肩、上胸且地面明显流动；B恢复胸像。 | A有垂直漂移、帽顶裁切和末段微动；B运动偏弱。完整随身同步效果仍未确认。 |
| X08 子弹时间 Bullet Time | 动作取景改善 | A/B都让帽到靴和地面留在画内，跳起、悬空、落地可核对。 | 仍绕过头、姿态/披风在变，时间冻结和90度终点/停机没有完整满足。 |
| M01 推近 Dolly In | 输入纠错与末段保持改善 | 把裸头乙的错误hat edge改成hairline，额外帽消失；6–7.75秒未再明显放大。 | 仍有低头和细微取景变化，不声称停机帧严格是6.000秒。 |

## 对照怎样做

每组重新提交上轮实际使用的原prompt，不把过去的视频直接充当本轮同环境对照。10条2101对照的8时点抽帧拼图均与各自归档拼图字节相同；这只证明这些采样画面的复现，不声称所有帧和原声音轨逐样本相同。

A采用较简短的摄影语言，B采用画面结果与阶段顺序；首批A/B只改detailed_description镜头正文，其余五段保持原样。同对照保持参考JPEG内容及顺序、服务端点、种子2101、8秒、544P/16:9、请求9个sigma点、shift12/3，自身并发2，经原有全局/实例占位。实际输出为960×544、24fps、H.264/AAC。

部分目标作了明示调整：O05将全身到脸的大倍率缩为腰上到脸肩；O07的B改试前进＋拉广，背景应相对变小，**不算原后拉方向已达标**；X08改为全身取景以看清离地和落地。部分保持段提前开始。不能隐去这些变化后宣称修好了原始全部约束。

随后只改O07当前对照的summary，镜头正文、定义和其余内容原样保持，检查前段总述对结果的影响。它改变了画面，但没有给出可信的眩晕变焦完成证据。O05/X07另两条summary草案未启动，记录为未测，不计入36条。

M08和O06在种子2102各做原/改配对，提示词先冻结再提交。M08的A仍更接近指定侧面，但该种子的原prompt本身也没有2101那样大幅绕到背后；O06的原prompt也会产生快速收紧，所以不能把2101的节奏优势泛化为改写稳定更好。这是同一组素材的有限种子复核，没有新场景留出测试。

## 本次发现并修正的输入错误

本次新写的M01_A和O06_B错误提到裸头乙的“hat edge”。M01_A实际多生成了帽/屋檐状头饰。责任在提示词的造型假设；这些含错误的版本不用于宣称能力不足或整镜达标。

分别只将hat edge替换为hairline，同参考、同种子、同采样重测为M01_A2和O06_B2。M01_A2的额外帽消失，直接显示一个不符合人物参考的词能造成视觉错误。O06_B2保留了短促收紧/后段保持，且不再带错误造型词。原误稿没有删除。

原计划最多3条summary诊断，发现此输入错误后将其中2条预算改用于纠错，实际仍为36条。[执行清单与调整原因](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/execution.json)保留全部已执行请求与未启动草案的区别。

## 判断依据与限制

逐条看0.5、1.5…7.5秒画面，疑点另加密：急速收紧在局部24fps/12fps序列核对，M01_A2最后两秒每秒4帧，X07_A每秒2帧。所有36条完成全视频/音频解码；解码成功不等于内容达标。对照片仅缩放、排版及加标签，无调速、补帧或定格来伪装停机。未做声音内容听验。

“短促收紧”与“纯光学急变焦”分开判断。主体变大而背景未同等放大，仍可能是急推或混合重构图；仅出现大脸不能证明固定机位的变焦。眩晕变焦还需同时核对主体保形、同一背景的相对伸缩与观察方向，不能把普通拉远、推近或背景换面当作达标。

这些结果能证实本轮具体prompt组合改变了输出，不证明是哪一个短语普遍生效，也不能排除参考图构图、文字与参考的组合、种子及其他尚未分离因素。当前记录是能力探测，不是模型上限或成功率估计。

## 各版本原片与观察

### M03 横移 Truck

- [M03_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M03_control/raw.mp4)：原prompt复跑仍让甲行走，相机伴随，人物头部完整。 演员静止与直线横移未完整执行。
- [M03_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M03_A/raw.mp4)：双脚维持原地、手停在腰带；相机与环境明显变化，人物不再自行迈步。 头部被裁切，机位实际较大弧绕、并有尺度变化，未成为纯直线横移/终点固定。
- [M03_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M03_B/raw.mp4)：甲保持站姿，手固定在腰前；前景石与远景有相对位移，人物头部回到画内。 机位仍偏弧绕，后段仍转，取景偏全身且脚被石头挡住，不能确认直线横移。

### M08 弧移 Arc

- [M08_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M08_control/raw.mp4)：仍绕过侧面到背面，再回另一侧，和上轮主要缺陷相同。 90度侧面终点、后半段保持未执行。
- [M08_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M08_A/raw.mp4)：从双眼正面逐渐到单眼侧面，整段未继续拍到背面；背景有视差。 裁成脸部近景而非胸上，后半段仍有运动，没有在半程完全停住；身体朝向在紧裁切中不能直接核对。
- [M08_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M08_B/raw.mp4)：也由正面转为单眼鼻线侧面，未再绕到背面。 比A更紧，头帽裁切明显，后半段仍有视点变化，未维持固定侧面四秒。
- [M08_control_s2102 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M08_control_s2102/raw.mp4)：新种子的对照本身没有2101那样绕满背面，向另一侧转成较偏后的侧面。
- [M08_A_s2102 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M08_A_s2102/raw.mp4)：A仍从正面到指定鼻朝右的侧面，未明显绕到背后；但景别更紧，后段仍动。

另一种子结论：两种子都能得到更接近侧面的A结果；对照错误也随种子变化，不能把全部差异或精确停机称为已稳定修复。

### O05 光学变焦 Zoom

- [O05_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O05_control/raw.mp4)：由全身连续放大到脸部，和归档缺陷相同。 仍像推近，未见全部景物同比例放大的固定视点效果；结尾未停。
- [O05_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O05_A/raw.mp4)：仍从全身推进到脸部；人物副本未见。 起点没有按腰上裁切，背景/人物放大比例不同，未证实光学变焦，末段仍缓慢推进。
- [O05_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O05_B/raw.mp4)：开场较接近腰/胸上，放大幅度较小，后段脸部尺寸较稳定。 背景未与人物一起同比放大，仍不足以确认固定视点变焦；放大范围比原任务小。

### O06 急变焦 Crash Zoom

- [O06_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_control/raw.mp4)：开头已渐进放大，约3秒转更近，末段大脸保持。 未先固定3秒再以独立半秒突发放大。
- [O06_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_A/raw.mp4)：从中景逐渐推到近脸。 急变焦退化为较平滑推进，未形成独立短促段。
- [O06_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_B/raw.mp4)：0.5–2.5秒构图近似保持，3.5秒已突然放大到脸，之后尺寸较稳定。 已补看2.75–3.75秒逐帧序列，约2.75–2.90秒快速连续收紧，早于指定3秒；背景并未同步等比放大，仍有急推/变焦歧义。 输入错误：Prompt wrongly mentions hat edge for bareheaded Actor B. Replace with hairline and rerun; this variant cannot establish full adherence.
- [O06_B2 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_B2/raw.mp4)：修正hat edge为hairline后，仍有开场保持、短促放大、后段近脸保持，未生成额外帽。 已看2.75–3.75秒24fps，收紧集中在约2.75–3.0秒，结束稍早于/接近预定触发；背景非同比放大，仍有急推/变焦歧义。
- [O06_control_s2102 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_control_s2102/raw.mp4)：原prompt在此种子也出现先保持再快速收紧，最终过紧裁到眼鼻。
- [O06_B2_s2102 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O06_B2_s2102/raw.mp4)：修正后的B2保住更完整脸部，快速收紧偏晚、最后仍有微动。

另一种子结论：2101的节奏优势未在新种子上形成同样明确的相对优势；仅有取景改善，精确时点与纯光学效果未证实。

### O07 眩晕变焦 Dolly Zoom

- [O07_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O07_control/raw.mp4)：脸部大小较稳定，背景有变化。 背景伴随侧向换面，未证实原定透视压缩。
- [O07_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O07_A/raw.mp4)：整体平滑拉远，末段较稳。 头部显著变小，补偿未执行，退回普通拉远。
- [O07_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O07_B/raw.mp4)：迎面取景逐渐收紧，背景维持近似大小。 前进/拉广反方向方案也未保住头部尺寸，未见所需背景相对缩小。
- [O07_C 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/O07_C/raw.mp4)：只改summary，保持原镜头正文，头部尺度仍较稳定。 背景有明显换面/侧向变化，没有可信的同一背景相对放大压缩；与当前对照同类未确认。

### X02 多角色接力展示

- [X02_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X02_control/raw.mp4)：按甲、乙、女性顺序接力，只见三人。 到女性后仍在滑动，终点保持未执行。
- [X02_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X02_A/raw.mp4)：接力顺序存在。 又新增背景两人，只有空背景/三人的要求被破坏，末段仍动。
- [X02_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X02_B/raw.mp4)：维持三人和接力顺序。 女性变为更华丽的古装而非朴素蓝衣，终段仍滑动。

### X06 机身滚转 Roll

- [X06_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X06_control/raw.mp4)：约90度侧置后保持，但有拉远。 原要求的尺寸/距离与保持仍不完整。
- [X06_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X06_A/raw.mp4)：人物/环境有旋转。 开场背景上下异常，随即额外绕人，越过90度到倒置并继续旋转；不再保持正确角度。
- [X06_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X06_B/raw.mp4)：整体连续滚转。 越过90度转到倒置及另一侧，并伴随拉远。

### X07 主体中心随身 Snorricam

- [X07_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X07_control/raw.mp4)：只有一张大脸，背景少量流动。 只到眼鼻大特写，帽和胸出框，不能确认完整随身镜头。
- [X07_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X07_A/raw.mp4)：恢复脸、双肩、上胸的半身取景，脸部大小较稳定，石地背景随时间明显流动。 补看每秒2帧后，石地流动与胸像稳定可见，但身体/头部仍有垂直漂移，最后两秒仍微动，帽顶无充足余量；不宣称精确步伐同步或准时停住。
- [X07_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X07_B/raw.mp4)：脸、肩、胸恢复，人物只有一份。 背景和构图变化很弱，行走/背景流动不明确，帽顶仍裁切。

### X08 子弹时间 Bullet Time

- [X08_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X08_control/raw.mp4)：跃起慢动作伴随绕行，最后落地。 头部多数时候出框，绕行过大，姿态/披风未保持近冻结。
- [X08_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X08_A/raw.mp4)：人物从帽到靴完整入画，空中停留伴随视点移动，最后落地。 仍绕过背面、接近整圈，空中身体/披风变化，未按90度终点停住。
- [X08_B 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/X08_B/raw.mp4)：完整全身和地面入画，跳起/悬空/落地都可见。 绕行仍越过背面且持续到末段，手脚姿态随镜头变化，冻结和第6秒停机未完成。

### M01 推近 Dolly In

- [M01_control 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M01_control/raw.mp4)：推近到乙脸部。 最后两秒仍在靠近。
- [M01_A 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M01_A/raw.mp4)：推近并在末段趋稳。 我们错误加入帽沿描述，生成了额外帽/屋檐状头饰。 输入错误：Actor B无帽，造型假设错误；这条不用于能力归因，另作A2改词重测。
- [M01_A2 原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/M01_A2/raw.mp4)：只将hat edge改为hairline后，额外帽/屋檐状头饰消失；推近至脸后末段尺度趋稳。 已看6–7.75秒每秒4帧，主体未继续明显放大，但有低头及轻微构图变化；未宣称停止帧严格等于6.000秒。

## 接回制作资料的范围

制作Skill补入本轮有限案例：人物世界中的静止与影像中的位移分开表达；用看得见的观察面与结束画面约束过程；短动作与长保持分段；构图改善不能代替运动完成。保留实际图片优先的既有规则，不把“帽沿”等人物特有部位写成所有角色通用锚点。没有把本轮退化prompt替换成默认生产模板，也没有新增禁动或复杂手法禁用清单。

没有修改生产书目、已有请求/成片、预算计数、模型服务、画图资产或默认路由；没有提交、推送或上传。新资料的加载验证另见[validation.json](/mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-refine-20261015/validation.json)。

## 证据文件

- [逐项完整观察](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/observations.json)
- [原prompt复现核对](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/baseline-repeatability.json)
- [冻结初始设计](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/manifest.json)
- [实际36条执行清单](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-refine-20261015/execution.json)
- [实验续跑状态](/mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-full-20261015/research-state.json)
- 运行与复现入口为 /mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-refine-20261015 下的prepare.py、run.py、run_extra.py；每条保留request.json、task.json、server-final.json、prompt.txt、clip.json、原始MP4和序列图。
