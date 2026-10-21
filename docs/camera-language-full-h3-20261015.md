# 50项镜头语言全覆盖与 prompt 复核（2026-10-15）

后续已对未达标复杂手法继续完成36条prompt实验，见[复杂运镜续测与全部改写](camera-language-prompt-refine-20261015.md)。本文保留上一轮50项覆盖时的结果，后续改善、退化与输入纠错另列，不覆盖原证据。

手册50个条目全部有实际样片与观察记录：43项为H3独立单镜，7项为H3素材的后期剪辑；J-cut和L-cut分别出片。首轮52次H3请求，另8次同种子改写复核，共60次、453秒请求时长。之前18次探测另存，不重复计入本轮覆盖。

**全部测试不等于全部符合要求。** 下表“可见”只表示核心构图、运动、焦点或剪辑关系可辨认；人物、道具、裁切、路径幅度与停住偏差另列，不能据此把整镜判为通过。首轮43个单镜的核心手法观察为：可见33项、部分7项、未见3项。不把一次样片换算为稳定成功率。

## 直接看结果

- [50项完整样片串看（含原声与J/L两版）](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/all-50-preview.mp4)
- [8条原prompt/同种子改写并排对照（无声）](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/eight-prompt-comparisons-silent.mp4)
- [逐项播放器、完整prompt及原片入口](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/index.html)
- [全部60份完整六段prompt](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/all-prompts.json)
- [中文用途与完整镜头正文汇编](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/prompts.md)

## 当前能得出的结论

prompt确实影响结果，但不能把每个失败都归给一句话没写好。此前同种子、同三图、同参数的局部正文改写，已使过肩去掉人物副本、顶拍由斜俯变垂直、拉焦出现清晰对象反转。本轮8组又显示：明确画内局部/空背景可减少副本，可见终点描述可改善滚转角度；同时横移仍让演员行走、90度弧移仍绕过头、光学变焦改写甚至新增副本。它们不是通用必成模板。

本轮拉焦、分割焦点式画面、完整环绕、穿门、遮挡揭示和子弹时间式慢动作配合绕行均有可见例子。眩晕变焦仍缺少充分证据；急变焦改写后更集中，但未严格按触发时刻与半秒运动完成。普通放大、普通拉远不能替代这些特定效果。

**最普遍的偏差是终点与停机。** 写了“第6秒停”并不表示模型准时执行；许多镜头继续推近、拉远或绕行到结尾。加长终点保持段在某些例子改善了结果，在弧移/横移中仍失败。创作继续按剧情选动静，不因此统一改回固定机位。

## 测试条件与证据边界

使用既有内网MiniMax H3 Ref2VA Turbo服务、544P、16:9、固定种子2101、请求9个sigma点、shift 12/3，自身并发2，并经过已有全局/实例占位。实际输出960×544、24fps、H.264/AAC；不是生产成片的25fps/高清验收。服务元数据报告TI2V、DiT bf16/VAE fp32，未读取GPU服务日志验证内部实际执行步数。

所有prompt为本实验人工斟酌的英文镜头设计，使用生产六段绑定器与请求检查。人物卡仅供身份/服装；按每镜可见人数选单人或双人参考，空镜只带地点图；门、镜、柱、钥匙、木桌和钟是明示的原创测试元素。它不证明编剧模型已自主选好手法，也不等于真实章节、不同画风或整集自动生产验证。

每个H3请求保留prompt.txt、clip.json、request.json、task.json、server-final.json与raw.mp4。request.json保留发送值，参考图的base64改为本地冻结文件路径，不重复存大串数据。60条视频均完成全视频/音频解码；8个后期样片亦独立解码。

单镜先按0.5、1.5…秒顺序抽帧，快转场、动作衔接和疑点额外加密。手持另看1–3秒每秒8帧；仍不清楚就记“部分”。没有逐帧测量真实焦距、物理米数或精确停机帧，也没有人工听验钟声音色/对白，未声称这些已通过。全部证据属于选定合成样例的能力探测。

## 50项逐项结果

| 编号 | 手法 | 核心手法 | 看到什么 | 未符合或未确认什么 | 样片 |
|---|---|---|---|---|---|
| F01 | 过肩 OTS | 可见 | 0.5–7.5秒保持甲肩后脑在左、乙面部在右的过肩关系，未见独立甲副本。 | 新增甲用手碰乙下颌的接触动作，超出仅呼吸的要求；不是整段内容通过。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F01/raw.mp4) |
| F02 | 过腰 | 可见 | 近处甲腰手占右侧、远处乙可读，未见第三具身体；过腰关系保持。 | 乙拍成全身而非膝上，前景甲偏正面；未严格复现指定裁切。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F02/raw.mp4) |
| F03 | 腿间框景 | 可见 | 地面低机位从甲双腿之间看见乙；靴底接地，未出现远处完整甲副本。 | 后段取景轻微收紧、前景腿间距变动，未严格保持全程固定。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F03/raw.mp4) |
| F04 | 带前景单人 Dirty Single | 部分 | 乙面部近景和左边棕色前景出现，但前景不止小块衣袖。 | 新增大手遮住乙口鼻，乙头上混入甲的帽子；原定不遮表情的小前景构图未完整成立。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F04/raw.mp4) |
| F05 | 双人/多人同框 | 可见 | 三名独立人物同时同框，甲左、乙中、蓝衣女性右，乙先看甲后看女性；未见人数副本。 | 拍成全身而非膝上，末段略收紧，女性点头不明显。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F05/raw.mp4) |
| F06 | 低机位仰拍 | 部分 | 低处向上观察的透视和天空背景可见。 | 前半段只拍腿腰、脸出框，随后明显上摇才看到脸；未保持要求的腰至帽固定仰拍。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F06/raw.mp4) |
| F07 | 高机位斜俯拍 | 可见 | 明确高位斜俯，甲完整身体、脸和圆石关系可见；与垂直顶拍可区分。 | 相机缓慢环移/取景漂移，未保持指定固定构图；抬头时点较早。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F07/raw.mp4) |
| F08 | 垂直顶拍 | 可见 | 地面与圆石形成真正垂直俯视，帽顶肩膀明显缩短；仅一人，无重复身体。 | 甲站在石头上而非石头左侧，镜头持续水平转向/滚转造成场景旋转，未按要求固定保持。 画面边缘仍有石台外的云/空隙，未满足石地铺满全部边缘。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F08/raw.mp4) |
| F09 | 主观视角 POV | 可见 | 观察者手在右下近处，乙面向观察者并举掌；观察者没有额外全身出镜，主观关系可读。 | 观察者手为露指而非棕手套，抬掌/收手时间有偏差；镜位略有呼吸漂移。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F09/raw.mp4) |
| F10 | 荷兰角 | 部分 | 画面呈现明显倾斜，人物与背景在同一斜向关系中。 | 倾角接近横躺而非约20度，且有持续角度/构图变化，未保持指定小幅荷兰角。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F10/raw.mp4) |
| F11 | 框中框 | 可见 | 厚门框/窗框在前景四边包住后方甲的腰上像，内外两层深度清楚，持续保持。 | 框体形似带台阶的窗/矮门，开口下沿较高；未严格复现完整可通行门口。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F11/raw.mp4) |
| F12 | 反射构图 | 可见 | 真实甲背面位于右侧，镜框内为同一甲正面，抬手触下颌在两侧对应发生；镜像不计新增身体。 | 镜面/真实姿态只按半秒至一秒图像检查，未做逐帧几何反射校准；构图轻微漂移。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F12/raw.mp4) |
| F13 | 留白 | 可见 | 甲位于画面左侧，右边大幅留空，等待关系清楚且没有来人。 | 人物宽度超过指定左五分之一，后段略有推近；精确裁切未复现。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F13/raw.mp4) |
| F14 | 前中后景分层 | 可见 | 近钥匙、中景甲、远景乙三层明确且独立，甲随后低头看钥匙，未见角色副本。 | 前景钥匙比例夸张；镜头小幅漂移，非严格固定。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/F14/raw.mp4) |
| M01 | 推近 Dolly In | 可见 | 乙由较宽中景逐步收紧到脸肩近景，石台与远山相对位置随观察靠近而改变。 | 起点偏宽；6.5到7.5秒仍在靠近，未执行第6秒停住。仅凭视频不验证真实镜头焦距或精确米数。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M01/raw.mp4) |
| M02 | 拉远 Dolly Out | 可见 | 甲脸肩近景连续拉成石台全貌，近圆石与远景的遮挡/相对大小改变，拉远意图明确。 | 拉得远超过五米预期，后段仍微动；人物被安排站在圆石上，未严格执行停止时间。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M02/raw.mp4) |
| M03 | 横移 Truck | 部分 | 近圆石向左移、背景变化较小，有横向位移和视差。 | 甲自行从圆石上走向右边，镜头跟着腿部走，头出框；把静止人物旁的横移变成了行走跟拍。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M03/raw.mp4) |
| M04 | 水平摇摄 Pan | 可见 | 画面从甲平滑向右转向乙，终点乙居中，视线触发可读。 | 乙开头已部分入画，终段仍轻微收紧；单靠这些画面不能证明真实物理位置完全固定。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M04/raw.mp4) |
| M05 | 俯仰摇摄 Tilt | 可见 | 观察方向从甲脸帽连续下移到腰手钥匙，最终头在画外，俯摇结果成立。 | 甲先举高又放低钥匙，新增了原本腰部静持之外的动作；末段仍有微动。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M05/raw.mp4) |
| M06 | 垂直升降 Pedestal | 可见 | 近石沿下降，甲由帽眼被遮逐渐露出胸腰，升高后的越障揭示可读。 | 开场已露大部分头部而非仅帽眼；末段仍在上移/收紧，未严格第6秒停止。仅能判断可见效果，不能验证真实升高米数。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M06/raw.mp4) |
| M07 | 侧面跟拍 Tracking | 可见 | 指定甲向右走，镜头在侧面伴随，近圆石与远山连续错移，最后甲停步。 | 取景为全身而非膝上，步数多于三步；6.5秒仍迈步，停点较晚。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M07/raw.mp4) |
| M08 | 弧移 Arc | 可见 | 相机连续绕过甲正、侧、背面，有环境视差，甲未靠原地自转替代全部运动。 | 实际绕了大半圈，超过指定90度，结尾仍动；侧面终点和第6秒停机未执行。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M08/raw.mp4) |
| M09 | 环绕 Orbit | 可见 | 甲前、侧、背、另一侧、前依次出现；圆石与山体连续有视差，末段回到与开场相近的正面环境关系，完整环绕可见。 | 取景全身而非膝上，回到起点较晚，未确认第7秒已完全停住；半秒至一秒采样不足以验收逐帧闭合。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M09/raw.mp4) |
| M10 | 摇臂 Crane/Jib | 可见 | 从人物近处连续后退并升高到俯看整座石台，明显有高度与距离共同变化。 | 升得远高于六米且持续到结尾，甲站到圆石上，起点头部有裁切；没有执行第6秒停止。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M10/raw.mp4) |
| M11 | 手持 Handheld | 部分 | 已加看1–3秒每秒8帧，面部近景有轻微取景和高低变化。 | 细小不规则手持摆动不够明确，仍难与平滑微动/人物呼吸区分；甲另有张嘴动作。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M11/raw.mp4) |
| M12 | 稳定移动 Steadicam/Gimbal | 可见 | 甲迎面行走，脸身大小基本稳定，后方石块连续错移，画面移动较平稳。 | 腿部不在取景中，不能从这些图确认完整步数；终点仍有少量构图漂移，未逐帧证明与甲同时停止。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M12/raw.mp4) |
| M13 | 固定观察 Static | 可见 | 双人站位和主要构图持续保持，回应由甲乙头部/身体细微动作承担，未见明显主动推拉绕移。 | 拍成全身而非膝上，点头时点不严格；细微漂移不等于测得物理三脚架完全静止。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M13/raw.mp4) |
| O01 | 浅景深 | 可见 | 甲眼睛与面部较清，远山明显柔化，近手/钥匙也较软，浅景深可见。 | 新增前景手与钥匙的操作，原要求悬挂静物；脸略有取景漂移。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O01/raw.mp4) |
| O02 | 深景深 | 部分 | 近钥匙和远两人同框，远景衣服轮廓可读。 | 前景钥匙及近石面明显偏软，远脸很小，尚不满足所要求的近远连续清晰；另有轻微构图变化。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O02/raw.mp4) |
| O03 | 焦点切换 Rack Focus | 可见 | 0.5–1.5秒近钥匙齿边清楚、甲脸虚；3.5秒后甲眼睛清楚、近钥匙柔化，焦点两端反转可见。 | 钥匙被手拿着而非静放石沿，机位/取景轻微漂移；不是所有物件状态与时间均通过。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O03/raw.mp4) |
| O04 | 持续跟焦 | 可见 | 甲从较远全身走成近脸，眼睛在各阶段保持可读，远景后段更软，持续跟焦的结果可见。 | 起点偏全身，结尾帽子裁切；第6秒后继续接近，不能证明镜位和焦距绝对不变。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O04/raw.mp4) |
| O05 | 光学变焦 Zoom | 部分 | 取景由全身连续放大到甲脸部。 | 人物与山体没有呈现一致的等比例放大，背景近远关系同时改变，更像推近或混合重构图；不能确认固定机位光学变焦，第6秒也未停止。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O05/raw.mp4) |
| O06 | 急变焦 Crash Zoom | 未见 | 乙表情变惊讶，画面从约3秒后开始持续收紧。 | 没有约半秒完成的急变焦，直到末段仍在推进/放大，缺少急变焦后保持段。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O06/raw.mp4) |
| O07 | 眩晕变焦 Dolly Zoom | 未见 | 指定乙保持身份，镜头逐渐拉远，后景有相对变化。 | 乙头部明显缩小，未由反向变焦补偿；看不到主体尺寸稳定同时背景压缩的目标效果，不能把普通拉远算作眩晕变焦。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O07/raw.mp4) |
| O08 | 分割焦点 Split Diopter | 可见 | 近左钥匙齿边和远右甲眼睛同时较清晰，中间横向石沿纹理偏软，两个清晰深度区和中间模糊可见。 | 仅确认生成图像的分割焦点式效果，不声称模型使用真实附件；景别/取景有小幅漂移。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O08/raw.mp4) |
| X01 | 背后绕前揭示 | 可见 | 由甲背面经过侧面连续绕到可见双眼的前三分之四，脸逐步揭露，环境同时有视差。 | 后段仍移动，未在第6秒停住，终景偏紧；途中背景新增桥/塔。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X01/raw.mp4) |
| X02 | 多角色接力展示 | 可见 | 相机依次从甲移到乙、再到蓝衣黑发女性，接力的主要顺序可见。 | 背景新增一名浅发女性和一名男子，共五人而非三人；终点仍移动，蓝衣女服装变为现代风格。核心移镜可见但人数/造型未通过。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X02/raw.mp4) |
| X03 | 遮挡后揭示 | 可见 | 开场石柱遮住甲，随近柱向左滑出逐步露出甲的脸和身体，近远视差明显。 | 甲出现轻微转身，终景全身而非腰上，末段仍有绕移；第6秒停住未严格执行。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X03/raw.mp4) |
| X04 | 穿门探入 | 可见 | 从门外连续前进，门框从两边擦过，进入室内后靠近甲，通行路径清楚。 | 最终比腰上景别更近，6秒之后仍推进；原要求的终点保持没有完整实现。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X04/raw.mp4) |
| X05 | 快速甩镜 Whip Pan | 可见 | 后段从甲快速转到乙，转场中背景出现强横向模糊，随后乙保持可读。 | 已补看每秒2帧，约5秒附近才快速横向转场；早于此时是缓慢构图变化。明显晚于指定3秒触发，结尾仍有微动。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X05/raw.mp4) |
| X06 | 机身滚转 Roll | 可见 | 人物和整个背景一起连续滚转，确有绕画面中心的旋转。 | 转到约180度倒置而非指定90度侧置，过程中还拉远；终点角度与止转时点不符合。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X06/raw.mp4) |
| X07 | 主体中心随身 Snorricam | 未见 | 一张巨大甲脸与近处甲背部身体同时出现，背景也有少量运动。 | 形成同一人的两份形象，未得到单一脸/胸相对构图稳定、背景随步伐流动的随身镜头。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X07/raw.mp4) |
| X08 | 子弹时间 Bullet Time | 可见 | 甲跃起后在空中极慢停留，环境和观察面明显绕行，末段落地；极慢动作配合视角移动的效果可见。 | 人物姿态和披风并非冻结，绕行超过90度，头部多数时候出框，停止/落地时点不精确。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X08/raw.mp4) |
| E01 | 正反打 | 可见 | 甲肩后乙脸向左，切乙肩后甲脸向右，前景肩侧与对向视线对应，正反打关系可读。 | 两条独立源片的背景和表演不逐帧连续，甲有额外开口；未验收对话声线。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E01/raw.mp4) |
| E02 | 视线匹配 | 可见 | 甲先低头向右下看，随后切石上钥匙细节，看与被看对象构成可读关系。 | 单独钥匙镜没有人物同框坐标作精确几何校准；是后期视线接法，非跨请求空间记忆。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E02/raw.mp4) |
| E03 | 动作匹配 | 可见 | 在手持钥匙下落中切到另一角度近景，随后触石、松手，仅放下一次；按实际动作阶段选择源片范围。 | 两个原片的触石时刻与原prompt不同，需按实拍范围对齐；手套未生成，钥匙柄细节略变，非帧级动作捕捉匹配。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E03/raw.mp4) |
| E04 | 反应镜 | 可见 | 先见甲放下钥匙并松手，切乙看向下方、眉眼抬起并抬眼的反应，事件到反应的因果可读。 | 反应表演由独立请求生成，不证明同场动作/光照完全连续。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E04/raw.mp4) |
| E05 | 细节插入 | 部分 | 放钥匙后切单独钥匙特写，细节插入的叙事功能可见。 | 独立生成的钥匙柄装饰与全景不同，石面细节也变化；同一道具跨镜一致性未完整符合，仅重复形状文字未锁住设计。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E05/raw.mp4) |
| E06 | Match Cut | 可见 | 圆形石头顶拍切圆木桌顶拍，经过明确裁切缩放对齐后，中心和外轮廓在切点相近。 | 石头轮廓不规则，木桌规则圆形，尺寸并非完全重合；后期完成图形匹配，H3没有原生自动完成接镜。 | [原片](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E06/raw.mp4) |
| E07 | J-cut / L-cut | 可见 | 分别制成J/L两版：J的新声轨先入2秒，强声段出现在3秒画面切换前；L的旧声轨延续到2.5秒画面切换后1秒。解码波形与原H3音轨的指定范围高度相关。 | 声画错位由后期时间线完成，未改变H3跨请求记忆；未人工试听钟声音色或做对白/口型验收。源钟强起声约1.3秒，偏离prompt要求的0.5秒，J接法按实测起声选择。 | [J](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E07/J-cut.mp4) / [L](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/E07/L-cut.mp4) |

## 8组同种子正文改写

每组保持原端点、种子、参考图和采样，只替换detailed_description的单镜正文；六段里的主体定义、summary、retention和声音保持。部分改写把终点提前以留出较长保持段，因而不能声称修好了原来的精确停止时刻。改写前后全部保留，不删失败或退化样本。

| 手法 | 变化 | 复核观察与决定 | 对照 |
|---|---|---|---|
| M03 横移 Truck | 未修正 | 柱子和地面有横向视差，但甲仍自行行走，取景仍偏全身；长保持段未稳定。 保留失败，不采用为“静止人物旁横移”的可靠模板。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M03/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M03_rewrite/raw.mp4) |
| M08 弧移 Arc | 未修正终点 | 仍绕过侧面到背面，再回到另一侧，远超四分之一圈；第4秒侧面终点和长保持仍未执行。 弧移可见，指定角度和停点仍不符合。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M08/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/M08_rewrite/raw.mp4) |
| O05 光学变焦 Zoom | 退化 | 画面放大，同时又在背景生成完整甲副本；仍无固定观察点等比例放大的可信证据。 不采用此改写；保留原样本与退化样本。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O05/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O05_rewrite/raw.mp4) |
| O06 急变焦 Crash Zoom | 局部改善 | 放大更集中，后半段比首轮稳定，但开场已逐步推进，放大与要求的3秒触发/半秒完成不一致。 不能称纯急变焦成功；保留更快的放大与保持这一局部变化。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O06/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O06_rewrite/raw.mp4) |
| O07 眩晕变焦 Dolly Zoom | 局部改善但未证实目标 | 乙头部尺寸比首轮更稳定，背景有明显变化；同时背景侧向换面，无法确认指定后拉与反向变焦造成的压缩。 主体保形改善；眩晕变焦仍未获得充分可见证据。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O07/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/O07_rewrite/raw.mp4) |
| X02 多角色接力展示 | 人数改善 | 甲、乙、蓝衣女性依次成为主体，背景两名多余人物消失，只见原定三人；结尾仍有滑动。 该次证明正文能改善人数，未证明所有多人接力均稳定或能准时停机。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X02/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X02_rewrite/raw.mp4) |
| X06 机身滚转 Roll | 终点角度改善 | 由水平转到约90度侧置后维持，未再转成180度倒置；但伴随明显拉远，保持段仍有取景变化。 可见终点描述在本例改善角度，不能当作整镜通过。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X06/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X06_rewrite/raw.mp4) |
| X07 主体中心随身 Snorricam | 副本消失，目标仍不完整 | 前景背部副本消失，留下较稳定的单一面部与流动背景；实际裁到眼鼻大特写，帽顶/上胸不在画内，行走与第5秒停步不可明确确认。 删除装置比喻改善了人物重影；完整随身效果仍不确证。 | [首轮](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X07/raw.mp4) / [改写](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/X07_rewrite/raw.mp4) |

## 多镜与声音如何验证

正反打检查肩侧与对向视线；视线匹配检查先看后给对象；动作匹配依据实际下落阶段选择切点，接住同一次放下；反应镜与插入分别接事件结果。Match Cut将原顶拍圆石裁切缩放，与圆桌的位置/外轮廓对齐。裁切与源片范围见各E目录edit.json。

J版画面在3秒切到铜钟，新音轨在1秒入，实际强声约在2.3秒出现，先于画面切换；L版2.5秒切到反应，旧钟音轨延续到3.5秒。用解码PCM对照来源区段，J相关系数0.999737、L为0.999998，证明来源和时间偏移；该检查不评价听感。源钟强声比prompt晚，接法根据实测起声选择，未替换成外部钟声。

这7项由后期组装，不能宣称H3一次请求原生完成。当前生产的逐请求声音接口未增加跨请求记忆或自动J/L剪辑功能。E05另保留独立生成钥匙纹样不完全一致的偏差；钥匙仍可辨认，不给设计细节完全一致的结论。

## 已接入的通用写法

共享camera要求与制作Skill均补入：镜头正文从当前可见取景开始；前景人物写实际肩/腰/腿局部与远主体；参考图负责身份与设计，不用全场全身介绍覆盖当前构图。相机与演员运动分开写，焦点写清两端，终点写成可辨认画面并留出保持。手册保留全部手法，不新增禁动清单，也不把本组偶然成功设为硬规则。

改写样例与未通过边界已写回[camera-language.md](/mnt/disk1/zengzhitao/novel/novel-manga-video/skills/episode-production/references/camera-language.md)。后续新生产/重新准备会读取这些文本；既有任务的旧prompt/旧视频没有重写。

## 文件与复现

- [覆盖与冻结输入manifest](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/manifest.json)
- [全部观察和8组复核](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/observations.json)
- [剪辑解码与音轨时序检查](/mnt/disk1/zengzhitao/novel-manga-video/outputs/camera-language-full-20261015/editing-validation.json)
- [实验续跑状态](/mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-full-20261015/research-state.json)
- 复现入口：/mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-full-20261015/prepare.py、run.py、prepare_rechecks.py、run_rechecks.py；后期为build_editing.py与editing-cuts.json。已完成回执会复用，不对状态不明的POST重新提交。重新运行已有完成目录不会获得新种子样本。

本轮没有改动生产书目、镜头计数/预算、模型服务配置；没有提交、推送或上传视频。源代码与Skill验证结果另记[validation.json](/mnt/disk1/zengzhitao/novel-manga-video/.runtime/camera-language-full-20261015/validation.json)。

## 制作流程验证

主项目完整回归：3669项通过，86.43秒；Skill结构有效，资源加载单独28项通过。首轮全量发现手册中的项目报告相对链接会越出安装后的Skill包，已改为项目文档说明并完成上述重新全量。原失败日志保留。

本轮源码/资料改动：src/novel_manga/story/fields.py、skills/episode-production/references/authoring.md、skills/episode-production/references/camera-language.md、tests/fixtures/repair_services_before.json、docs/camera-language-full-h3-20261015.md。在途记录另追加收工说明；继承的其他会话改动未覆盖、未代提交。
