"""Episode 3 -- 客人.

The richest dialogue of the three chapters, and the first time the ring is
shown taking the 战之气 while the audience watches.  The social humiliation
(no seat, in front of guests) sets up 烟儿's rescue, and her closing tease
flips the relationship: the girl the whole family reveres has been keeping
his secret for years, and she is about to make him admit it.
"""
from build_s1 import (
    B, F, YAN, MEI, XUN, ZHAN, ELDER, YOUTH, MAIDEN, BUTLER,
    ShotBuilder, audio, beat, cam, delta, fact, perf, turn,
)

L_SIT = "床榻之上，少年闭目盘腿而坐，双手在身前摆出奇异的手印。"
L_BREATH = "胸膛轻微起伏，一呼一吸间，形成完美的循环。而在气息循环间，有着淡淡的白色气流顺着口鼻，钻入了体内，温养着骨骼与肉体。"
L_RINGGLOW = "在少年闭目修炼之时，手指上那古朴的黑色戒指，再次诡异地微微发光，旋即沉寂。"
L_EXHALE = '"呼……"缓缓地吐出一口浊气，少年双眼乍然睁开，一抹淡淡的白芒在漆黑的眼中闪过。'
L_UNREFINED = "那是刚刚被吸收，而又未被完全炼化的战之气。"
L_ANGRY = '"好不容易修炼而来的战之气，又在消失……我，我操！"沉神感应了一下体内，少年脸庞猛然地愤怒了起来'
L_FIST = "拳头死死地捏在了一起。半晌后，少年苦笑着摇了摇头，身心疲惫地爬下了床"
L_TIRED = "仅仅拥有三段战之气的他，可没有能力无视各种疲累。"
L_CALL = '房间外传来苍老的声音："三少爷，族长请你去大厅！"'
L_THIRD = "三少爷。楚焱在家中排行老三，上面还有两位哥哥。"
L_GO = '"哦。"随口地应了下来，换了一身衣衫，楚焱走出房间，对着房外的一名青衫老者微笑道："走吧，墨管家。"'
L_PITY = "望着少年稚嫩的脸庞，青衫老者和善地点了点头。转身的霎那，浑浊的老眼，掠过一抹不易察觉的惋惜。"
L_HALL = "跟着老管家从后院穿过，最后在肃穆的迎客大厅外停了下来。"
L_ELDERS3 = "坐于最上方的几位，便是楚战与三位脸色淡漠的老者。他们是族中的长老，权力不比族长小。"
L_GUESTS = "另外一边，坐着三位陌生人。想必他们便是昨夜楚战口中所说的贵客。"
L_MOON7 = "在老者的衣袍胸口处，赫然绘有一弯银色浅月，在浅月周围，还有点缀着七颗金光闪闪的星辰。"
L_SEVEN = '"七星大战师！这老人竟然是一位七星大战师？真是人不可貌相！"楚焱心中大感惊异。'
L_HIGHER = "这老者的实力，竟然比自己的父亲，还要高出两星。"
L_YOUTH5 = "当然，最重要的，还是其胸口处所绘的五颗金星。这代表着青年的实力：五星战者！"
L_MAIDEN3 = "另外，在少女那已经开始发育的玲珑小胸脯旁，绘有三颗金星。"
L_GENIUS = '"三星战者。这女孩……如果没有靠外物激发的话，那便是一个绝顶天才！"心头轻轻地吸了一口凉气，楚焱的目光却只是在少女冷艳的小脸上停留了瞬间便是移了开去。'
L_LOOKAWAY = "楚焱的目光却只是在少女冷艳的小脸上停留了瞬间便是移了开去。"
L_SALUTE = '"父亲，三位长老！"快步上前，对着上位的楚战四人恭敬地行了一礼。'
L_SITDOWN = '"呵呵，焱儿，来了啊，快坐下吧。"望着楚焱的到来，楚战止住了与客人的笑谈，冲着他点了点头'
L_NOSEAT = "回头在厅中扫了扫，却是愕然发现，竟然没自己的位置。"
L_SELFMOCK = '"唉，自己在这家族中的地位，看来还真是越来越低啊。往日倒好，现在竟然是当着客人的面给我难堪。这三个老不死的啊……"心头自嘲地一笑，楚焱暗自摇头。'
L_LAUGHAT = "望着站在原地不动的楚焱，周围的族中年轻人，都是忍不住地发出讥笑之声，显然很是喜欢看他出丑的模样。"
L_ANGRY2 = '此时，上面的楚战也是发现了楚焱的尴尬，脸庞上闪过一抹怒气，对着身旁的老者皱眉道："二长老，你……"'
L_APOLOGY = '"咳，实在抱歉，竟然把三少爷搞忘记了。呵呵，我马上叫人准备！"被楚战瞪住的黄袍老者，淡淡地笑了笑，"自责"地拍了拍额头。'
L_MOCKEYE = "只是其眼中的那抹讥讽，却并未有多少遮掩。"
L_INVITE = '"楚焱哥哥，坐这里吧！"少女淡淡的笑声，忽然地在大厅中响了起来。'
L_SILENCE = "三位长老微愣，目光移向角落中安静的楚烟儿，嘴巴蠕了蠕，竟然是都没有敢再说话。"
L_BOOK = "在大厅的角落处，楚烟儿微笑着合拢了手中厚厚的书籍，气质淡雅从容，对着楚焱可爱地眨了眨眼睛。"
L_JEALOUS = "然后在众多少年那嫉妒的目光中，走了过去，挨着她坐了下去。"
L_RESCUE = '"你又帮我解围了。"嗅着身旁少女的淡淡体香，楚焱低笑道。'
L_DIMPLE = "楚烟儿浅浅一笑，小脸上露出可爱的小酒窝。纤细的指尖再次翻开手中那本古朴的书籍。"
L_THREEYEARS = '眨动着修长的睫毛在书中徘徊了片刻，忽然有些幽幽地道："楚焱哥哥有三年没和烟儿单独坐一起了吧？"'
L_DODGE = '"呃……现在烟儿可是家族中的天才了，想要朋友还不简单么？"瞧得少女有些幽怨的光洁侧脸，楚焱干笑道。'
L_SECRET = '"在烟儿四岁到六岁的时候，每天晚上都有人溜进我的房间，然后用一种很是笨拙的手法以及并不雄厚的战之气，温养我的骨骼与经脉。每次都要弄得自己大汗淋漓后，方才疲惫离开。楚焱哥哥，你说，他会是谁？"烟儿沉默了半晌，忽然地偏过头，对着楚焱嫣然一笑。'
L_SECRET1 = L_SECRET
L_SECRET2 = L_SECRET
L_DENY = '"咳……我，我怎么知道？那么小，我们都还在地上爬呢，我哪知道。"心头猛地一跳，楚焱讪笑了两声'
L_TEASE = '目光转移到书籍之上，口中似乎是自喃般地淡淡道："虽然知道他是好意，可烟儿不管怎么说也是女孩子吧？哪有偷偷摸女孩子身体的道理。若是烟儿寻出了那人，哼……"'
L_GUILTY = "嘴角裂了裂，楚焱心头有些心虚，眼观鼻，鼻观心，不言不语。"

LINES = [v for k, v in list(globals().items()) if k.startswith("L_")]
AXIS = "大厅主轴，上位席在画面右，角落席在画面左"
DIR = "楚焱自厅门进入居中，烟儿恒居画面左侧角落"


def build(location):
    b = ShotBuilder(location)
    b.axis, b.direction = AXIS, DIR
    A = b.add

    # ---------------------------------------------------- B1 hook：解围前置
    A(beat=B[0], func="establish", strategy="story-keyframe",
      narration="满堂寂静里，一个少女的声音响起。", quote=L_INVITE, events=["event_011"],
      turns=[turn(XUN, "楚焱哥哥，坐这里吧！", L_INVITE,
                  mode="offscreen_dialogue", derivation="verbatim", emotion="淡淡的笑")],
      visual="肃穆的迎客大厅，满堂宾客与长辈端坐，画面中央一个少年孤零零站着，无人给他让座",
      motion="满堂视线聚在中央那道站着的身影上",
      characters=[YAN], scale="全景", power="一句话就压住了满堂长老", emo="疑问与好奇",
      facts=[F[3]], focus="被所有人注视却无处可坐的少年",
      kf=["开场社交困境全景", "悬念式人物关系"],
      audio_plan=audio(B[0], [(0.0, "ambience", "大厅低语", "开场"),
                              (0.4, "silence", "低语骤停", "少女出声"),
                              (0.85, "impact", "一记轻响", "满堂噤声")],
                       ambience="大厅低语", energy=0.5))
    A(beat=B[0], func="withhold", strategy="direct-assets",
      narration="三位长老愣住，竟没人敢接话。", quote=L_SILENCE, events=["event_011"],
      turns=[turn("二长老", "这……", L_SILENCE, mode="offscreen_dialogue", derivation="derived", emotion="语塞")],
      visual="三位脸色淡漠的老者微愣，目光齐齐移向大厅角落，嘴巴蠕动却没出声",
      motion="三人同时侧头，欲言又止",
      characters=[], scale="中景", power="少女的分量压过长老", emo="悬念加深",
      facts=[F[3]], focus="长老们噤声的一瞬")

    # ---------------------------------------------------- B2 question：修炼失败
    A(beat=B[1], func="transition", strategy="scene-only",
      narration="时间回到清晨，楚焱房中。", quote=L_SIT, events=["event_001"],
      turns=[turn("旁白", "同日清晨。", L_SIT, mode="narration", derivation="derived", emotion="平静")],
      visual="清晨微光的房间，床榻、窗棂与几缕浮尘", motion="晨光缓缓移过床沿",
      characters=[], scale="全景", power="时间回溯", emo="呼吸放缓", focus="安静的房间",
      location="楚焱房间")
    A(beat=B[1], func="establish", strategy="story-keyframe",
      narration="少年盘腿闭目，白色气流顺着口鼻钻入体内。", quote=L_BREATH, events=["event_001"],
      turns=[turn(YAN, "今天一定要留住。", L_BREATH, mode="inner_voice", derivation="derived", emotion="专注")],
      visual="少年闭目盘腿坐于床榻，双手结着奇异手印，淡淡的白色气流顺着口鼻钻入体内",
      motion="胸膛轻微起伏，气流缓缓旋入",
      characters=[YAN], scale="中景", power="他仍在每天照做", emo="共情努力",
      facts=[F[4]], focus="旋入口鼻的白色气流",
      kf=["修炼过程可视化", "气流入体关键帧"], location="楚焱房间")
    A(beat=B[1], func="reveal", strategy="story-keyframe",
      narration="他闭着眼，没看见指上的戒指又亮了一下。", quote=L_RINGGLOW, events=["event_001"],
      turns=[turn("旁白", "戒指再次诡异地微微发光，旋即沉寂。", L_RINGGLOW,
                  mode="narration", derivation="verbatim", emotion="冷冷点破")],
      visual="极近特写：少年结印的手指上，黑色古戒纹路间亮起微光，随即熄灭；少年双目紧闭毫无察觉",
      motion="毫光亮起又灭，睫毛纹丝不动",
      characters=[YAN], scale="特写", power="真凶就在他手上而他不知道", emo="观众比角色先知道",
      facts=[F[5]], focus="发光的戒指与紧闭的眼",
      kf=["核心悬念的直给", "观众领先信息差"],
      audio_plan=audio(B[1], [(0.0, "silence", "呼吸声之外全静", "戒指入画"),
                              (0.45, "bass_drop", "一记极低下沉", "毫光亮起"),
                              (0.8, "release", "余韵散去", "毫光熄灭")],
                       ambience="near-silence", energy=0.4), location="楚焱房间")
    A(beat=B[1], func="reaction", strategy="direct-assets",
      narration="他睁开眼，眸中一抹白芒闪过。", quote=L_EXHALE, events=["event_001", "event_002"],
      turns=[turn(YAN, "呼……", L_EXHALE, mode="visible_dialogue", derivation="verbatim", emotion="吐纳收功")],
      visual="少年双眼乍然睁开，漆黑眼中一抹淡淡白芒闪过", motion="睁眼，白芒一闪即逝",
      characters=[YAN], scale="近景", power="收功的一瞬", emo="悬着的心", focus="眼中的白芒",
      location="楚焱房间")
    A(beat=B[1], func="pressure", strategy="direct-assets",
      narration="沉神一感应，脸色猛然变了。", quote=L_ANGRY, events=["event_002"],
      turns=[turn(YAN, "好不容易修炼而来的战之气，又在消失……", L_ANGRY,
                  mode="visible_dialogue", derivation="verbatim", emotion="愤怒尖锐")],
      visual="少年脸庞猛然愤怒，拳头死死捏在一起，指节发白", motion="猛然攥拳，肩背绷紧",
      characters=[YAN], scale="中近景", power="努力被无声抹去", emo="憋屈",
      facts=[F[4]], focus="死死捏紧的拳",
      audio_plan=audio(B[1], [(0.0, "ambience", "清晨鸟鸣", "感应体内"),
                              (0.4, "impact", "一记闷响", "察觉消失"),
                              (0.75, "duck", "鸟鸣被压掉", "愤怒爆发")],
                       ambience="清晨鸟鸣", energy=0.7), location="楚焱房间")
    A(beat=B[1], func="reaction", strategy="direct-assets",
      narration="半晌后，他苦笑着摇头下床。", quote=L_FIST, events=["event_002"],
      turns=[turn(YAN, "三段的身子，连这点累都扛不住。", L_TIRED, mode="inner_voice", derivation="derived", emotion="疲惫自嘲"),
             turn(YAN, "练了一年，还是这副样子。", L_FIST, mode="inner_voice", derivation="derived", emotion="灰心")],
      visual="少年身心疲惫地爬下床，舒展有些发麻的脚腕与大腿", motion="下床，活动手脚，肩线松垮",
      characters=[YAN], scale="中景", power="连身体都在提醒他的处境", emo="无力",
      facts=[F[4]], focus="发麻的手脚与松垮的肩", location="楚焱房间")

    # ---------------------------------------------------- B3 pressure：贵客与压迫
    A(beat=B[2], func="advance", strategy="direct-assets",
      narration="门外传来苍老的声音。", quote=L_CALL, events=["event_003"],
      turns=[turn(BUTLER, "三少爷，族长请你去大厅！", L_CALL,
                  mode="offscreen_dialogue", derivation="verbatim", emotion="恭谨")],
      visual="房门外走廊，青衫老者立于门前躬身通传", motion="叩门，躬身",
      characters=[BUTLER], scale="中景", power="族长传唤", emo="节奏推进", focus="门外的青衫身影",
      location="楚焱房间")
    A(beat=B[2], func="reaction", strategy="direct-assets",
      narration="换过衣衫，他笑着招呼老管家。", quote=L_GO, events=["event_003", "event_004"],
      turns=[turn(YAN, "走吧，墨管家。", L_GO, mode="visible_dialogue", derivation="verbatim", emotion="温和")],
      visual="楚焱换过衣衫走出房间，对着青衫老者微笑", motion="推门而出，微笑颔首",
      characters=[YAN, BUTLER], scale="中景", power="他对下人始终客气", emo="人物底色", focus="少年的笑")
    A(beat=B[2], func="reveal", strategy="direct-assets",
      narration="老管家转身的一霎，眼里掠过惋惜。", quote=L_PITY, events=["event_004"],
      turns=[turn(BUTLER, "以三少爷当年的天赋……", L_PITY, mode="inner_voice", derivation="derived", emotion="不易察觉的惋惜"),
             turn(BUTLER, "唉，可惜了。", L_PITY, mode="inner_voice", derivation="derived", emotion="叹息")],
      visual="青衫老者转身的侧脸，浑浊老眼中掠过一抹不易察觉的惋惜", motion="转身，眼神一黯",
      characters=[BUTLER], scale="近景", power="连下人都在替他惋惜", emo="更酸",
      facts=[F[6]], focus="老眼中的那抹惋惜")
    A(beat=B[2], func="establish", strategy="story-keyframe",
      narration="肃穆的迎客大厅，宾主满堂。", quote=L_ELDERS3, events=["event_005"],
      turns=[turn(YAN, "父亲，三位长老！", L_SALUTE, mode="visible_dialogue", derivation="verbatim", emotion="恭敬")],
      visual="肃穆宽敞的迎客大厅，上位坐着楚战与三位脸色淡漠的老者，两侧坐满族中长辈与年轻一辈",
      motion="少年快步上前，躬身行礼",
      characters=[YAN, ZHAN], scale="全景", power="家族权力结构一览无余", emo="场面压迫",
      focus="上位四人与行礼的少年", kf=["家族权力关系全景", "多人精确站位"])
    A(beat=B[2], func="reveal", strategy="story-keyframe",
      narration="另一边坐着三位月白袍的陌生人。", quote=L_MOON7, events=["event_006"],
      turns=[turn(YAN, "七星大战师？", L_SEVEN, mode="inner_voice", derivation="verbatim", emotion="心头一凛"),
             turn(YAN, "比父亲还高出两星。", L_HIGHER, mode="inner_voice", derivation="derived", emotion="惊异")],
      visual="月白衣袍老者胸口特写：一弯银色浅月，浅月周围点缀七颗金光闪闪的星辰",
      motion="镜头随少年视线下移，停在胸口纹章",
      characters=[], scale="近景", power="来客实力远超族长", emo="局势变量出现",
      facts=[F[7]], focus="七颗金星的纹章",
      kf=["关键信息道具特写", "实力等级视觉化"])
    A(beat=B[2], func="reveal", strategy="direct-assets",
      narration="老者身旁的一对年轻男女，同样不简单。", quote=L_YOUTH5, events=["event_007"],
      turns=[turn(YAN, "五星战者，二十岁上下。", L_YOUTH5, mode="inner_voice", derivation="derived", emotion="估量"),
             turn(YAN, "三星战者。这女孩，是个绝顶天才。", L_GENIUS, mode="inner_voice", derivation="derived", emotion="轻吸凉气")],
      visual="月白青年英俊挺拔胸绘五颗金星，身旁冷艳少女胸口三颗金星，耳垂绿色玉坠微微摇动",
      motion="玉坠轻晃，少女目光淡淡扫来",
      characters=[], scale="中景", power="外来的强者梯队", emo="压力叠加",
      facts=[F[7]], focus="两人胸口的金星与摇动的玉坠")
    A(beat=B[2], func="reaction", strategy="direct-assets",
      narration="他只看了一眼便移开目光。", quote=L_LOOKAWAY, events=["event_007"],
      turns=[turn("少年甲", "他连看都不多看一眼？", L_LOOKAWAY, mode="offscreen_dialogue", derivation="derived", emotion="意外")],
      visual="冷艳少女微微一怔，目光在移开视线的楚焱背影上停了一瞬", motion="少女眼神一动，随即恢复冷淡",
      characters=[], scale="近景", power="被无视反而引起注意", emo="埋下伏笔",
      focus="少女那一瞬的诧异")

    # ---------------------------------------------------- B4 escalation：无座
    A(beat=B[3], func="advance", strategy="direct-assets",
      narration="父亲招呼他坐下。", quote=L_SITDOWN, events=["event_008"],
      turns=[turn(ZHAN, "呵呵，焱儿，来了啊，快坐下吧。", L_SITDOWN,
                  mode="visible_dialogue", derivation="verbatim", emotion="慈和")],
      visual="楚战止住与客人的笑谈，冲儿子点头挥手", motion="点头，挥手示意",
      characters=[ZHAN], scale="中近景", power="父亲的善意", emo="短暂放松", focus="挥手的动作")
    A(beat=B[3], func="pressure", strategy="story-keyframe",
      narration="他回头一扫，愕然发现没有自己的位置。", quote=L_NOSEAT, events=["event_008", "event_009"],
      turns=[turn(YAN, "……没有我的位置。", L_NOSEAT, mode="inner_voice", derivation="derived", emotion="愕然")],
      visual="大厅两侧座席满满当当，唯独没有一个空位，少年立在厅中央，四周是坐着的人",
      motion="少年环视一圈，脚步顿住",
      characters=[YAN], scale="全景", power="用座位公开羞辱", emo="尴尬难堪",
      facts=[F[8]], focus="满座之中唯一站着的人",
      kf=["社交羞辱的空间构图", "多人精确站位"])
    A(beat=B[3], func="reaction", strategy="direct-assets",
      narration="他心头自嘲一笑。", quote=L_SELFMOCK, events=["event_009"],
      turns=[turn(YAN, "当着客人的面给我难堪。", L_SELFMOCK, mode="inner_voice", derivation="derived", emotion="自嘲"),
             turn(YAN, "这三个老不死的啊……", L_SELFMOCK, mode="inner_voice", derivation="verbatim", emotion="压着火")],
      visual="楚焱面上不动声色，眼底冷了一分", motion="面色不变，眼神沉下",
      characters=[YAN], scale="近景", power="他看穿了这是故意的", emo="替他窝火",
      facts=[F[8]], focus="不动声色的脸")
    A(beat=B[3], func="pressure", strategy="direct-assets",
      narration="周围的年轻人发出讥笑。", quote=L_LAUGHAT, events=["event_009"],
      turns=[turn("少年甲", "站着挺好，显眼。", L_LAUGHAT, mode="offscreen_dialogue", derivation="derived", emotion="讥笑"),
             turn("少年乙", "嘘——小声点。", L_LAUGHAT, mode="offscreen_dialogue", derivation="derived", emotion="幸灾乐祸")],
      visual="族中年轻一辈交头接耳发出讥笑，很享受看他出丑", motion="窃笑，交换眼神",
      characters=[], scale="中景", power="同辈落井下石", emo="难堪加倍", focus="讥笑的群像")
    A(beat=B[3], func="pressure", strategy="direct-assets",
      narration="连贵客那边都投来目光。", quote=L_LAUGHAT, events=["event_009"],
      turns=[turn("少年丙", "族长的儿子，连个座都没有。", L_LAUGHAT,
                  mode="offscreen_dialogue", derivation="derived", emotion="压低的嘲弄"),
             turn("少年甲", "客人还看着呢。", L_LAUGHAT, mode="offscreen_dialogue", derivation="derived", emotion="看热闹"),
             turn(YAN, "越是这种时候，越不能动。", L_SELFMOCK, mode="inner_voice", derivation="derived", emotion="强自镇定"),
             turn(YAN, "他们等的就是我当众失态。", L_SELFMOCK, mode="inner_voice", derivation="derived", emotion="看穿")],
      visual="楚焱立在厅中，两侧座席上的目光从四面八方投来，月白袍的客人也侧首看来",
      motion="四周视线聚拢，少年纹丝不动",
      characters=[YAN], scale="全景", power="羞辱被放到最大", emo="窒息",
      facts=[F[8]], focus="纹丝不动的少年与四面投来的目光")
    A(beat=B[3], func="pressure", strategy="direct-assets",
      narration="父亲脸上闪过怒气。", quote=L_ANGRY2, events=["event_010"],
      turns=[turn(ZHAN, "二长老，你……", L_ANGRY2, mode="visible_dialogue", derivation="verbatim", emotion="压着的怒")],
      visual="楚战脸庞闪过一抹怒气，皱眉转向身旁的黄袍老者", motion="皱眉，转头瞪视",
      characters=[ZHAN], scale="中近景", power="族长当众发作却只能点到为止", emo="紧张",
      facts=[F[9]], focus="压着怒气的皱眉")
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="二长老假意道歉，眼里的讥讽却没遮掩。", quote=L_APOLOGY, events=["event_010"],
      turns=[turn("二长老", "咳，实在抱歉，竟然把三少爷搞忘记了。呵呵，我马上叫人准备！",
                  L_APOLOGY, mode="offscreen_dialogue", derivation="verbatim", emotion="皮笑肉不笑")],
      visual="黄袍老者淡淡笑着拍了拍额头做自责状，眼中讥讽并未遮掩",
      motion="拍额头，笑意不达眼底",
      characters=[], scale="中近景", power="长老敢当众落族长的面子", emo="更压抑",
      facts=[F[9]], focus="眼中未加掩饰的讥讽")

    # ---------------------------------------------------- B5 payoff：解围兑现
    A(beat=B[4], func="payoff", strategy="story-keyframe",
      narration="角落里的少女出声——冷开场在此兑现。", quote=L_INVITE, events=["event_011"],
      turns=[turn(XUN, "楚焱哥哥，坐这里吧！", L_INVITE, mode="visible_dialogue", derivation="verbatim", emotion="淡淡的笑")],
      visual="大厅角落，楚烟儿微笑着合拢手中厚厚的书籍，气质淡雅从容，对着楚焱眨了眨眼睛",
      motion="合书，抬眼，眨眼",
      characters=[XUN], scale="中近景", power="她一句话就压过三位长老", emo="解气",
      facts=[F[3]], focus="合上书抬眼的瞬间",
      kf=["解围时刻关键构图", "人物气质定格"])
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="三位长老嘴巴蠕了蠕，都没敢再说话。", quote=L_SILENCE, events=["event_011"],
      turns=[turn(YAN, "长老们竟然一个字都不敢说。", L_SILENCE, mode="inner_voice", derivation="derived", emotion="意外")],
      visual="三位长老愣住，嘴巴蠕动却没出声，满厅寂静", motion="三人同时噤声",
      characters=[], scale="中景", power="少女的分量已高过长老", emo="局势翻转",
      facts=[F[3]], focus="噤声的三张老脸")
    A(beat=B[4], func="advance", strategy="story-keyframe",
      narration="他在众多嫉妒的目光中走过去坐下。", quote=L_JEALOUS, events=["event_012"],
      turns=[turn(YAN, "你又帮我解围了。", L_RESCUE, mode="visible_dialogue", derivation="verbatim", emotion="低笑")],
      visual="楚焱穿过一片嫉妒的目光走向角落，挨着紫裙少女坐下",
      motion="穿行，落座，侧头低语",
      characters=[YAN, XUN], scale="中景", power="她公开站在他这边", emo="暖意",
      facts=[F[3]], focus="并肩落座的两人",
      camera=cam("厅中中景", AXIS, DIR, motivation="人物明确位移", trajectory="平稳跟移", end="角落双人中景"),
      kf=["人物位移与人群关系", "双人精确站位"])
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="少女浅浅一笑，露出小酒窝。", quote=L_DIMPLE, events=["event_012"],
      turns=[turn(XUN, "坐都坐了，还谢什么。", L_DIMPLE, mode="visible_dialogue", derivation="derived", emotion="浅笑")],
      visual="楚烟儿浅浅一笑，小脸上露出可爱的小酒窝，纤指再次翻开古朴书籍",
      motion="笑，翻书页",
      characters=[XUN], scale="近景", power="举重若轻", emo="心动", focus="小酒窝与翻书的指尖")
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="她忽然有些幽幽地开口。", quote=L_THREEYEARS, events=["event_013"],
      turns=[turn(XUN, "楚焱哥哥有三年没和烟儿单独坐一起了吧？", L_THREEYEARS,
                  mode="visible_dialogue", derivation="verbatim", emotion="幽幽")],
      visual="楚烟儿睫毛垂在书页上，侧脸有些幽怨", motion="睫毛微动，声音放低",
      characters=[XUN], scale="近景", power="三年的疏远被她点破", emo="心里一紧",
      facts=[F[10]], focus="幽怨的侧脸")
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="他干笑着躲开话头。", quote=L_DODGE, events=["event_014"],
      turns=[turn(YAN, "现在烟儿可是家族中的天才了，想要朋友还不简单么？", L_DODGE,
                  mode="visible_dialogue", derivation="verbatim", emotion="干笑")],
      visual="楚焱摸着鼻子干笑，视线飘向别处", motion="摸鼻子，视线躲闪",
      characters=[YAN], scale="近景", power="躲避亲近", emo="替他着急", focus="躲闪的视线")
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="她没有接他的话。", quote=L_DIMPLE, events=["event_013", "event_014"],
      turns=[turn(XUN, "朋友是不难。", L_DIMPLE, mode="visible_dialogue", derivation="derived", emotion="淡淡"),
             turn(XUN, "可烟儿要的又不是朋友。", L_THREEYEARS, mode="visible_dialogue", derivation="derived", emotion="轻轻一句"),
             turn(YAN, "……那你要什么？", L_DODGE, mode="visible_dialogue", derivation="derived", emotion="脱口而出"),
             turn(XUN, "楚焱哥哥自己想。", L_DIMPLE, mode="visible_dialogue", derivation="derived", emotion="不肯直说")],
      visual="楚烟儿翻着书页头也不抬，楚焱张了张嘴又闭上", motion="翻页，张嘴，闭上",
      characters=[XUN, YAN], scale="中近景", power="她句句都堵在他退路上", emo="心里发紧",
      facts=[F[10]], focus="翻书的指尖与张了张的嘴")

    # ---------------------------------------------------- B6 cliffhanger：秘密
    A(beat=B[5], func="reveal", strategy="story-keyframe",
      narration="她沉默半晌，忽然偏过头。", quote=L_SECRET1, events=["event_015"],
      turns=[turn(XUN, "在烟儿四岁到六岁的时候，每天晚上都有人溜进我的房间", L_SECRET1,
                  mode="visible_dialogue", derivation="verbatim", emotion="不动声色"),
             turn(XUN, "用一种很是笨拙的手法以及并不雄厚的战之气，温养我的骨骼与经脉。",
                  L_SECRET1, mode="visible_dialogue", derivation="verbatim", emotion="一字一句")],
      visual="楚烟儿偏过头对着楚焱嫣然一笑，少女独有的风情让周围少年眼睛发亮",
      motion="偏头，嫣然一笑，目光锁住他",
      characters=[XUN, YAN], scale="中近景", power="她一直知道，只是等到今天才说", emo="全集最大反转",
      facts=[F[10], F[11]], focus="嫣然一笑背后的了然",
      kf=["关系反转关键构图", "封面候选"])
    A(beat=B[5], func="pressure", strategy="direct-assets",
      narration="她把问题递到他面前。", quote=L_SECRET2, events=["event_015"],
      turns=[turn(XUN, "每次都要弄得自己大汗淋漓后，方才疲惫离开。", L_SECRET2,
                  mode="visible_dialogue", derivation="verbatim", emotion="慢条斯理"),
             turn(XUN, "楚焱哥哥，你说，他会是谁？", L_SECRET2,
                  mode="visible_dialogue", derivation="verbatim", emotion="明知故问")],
      visual="楚烟儿含笑注视着楚焱，眼神清亮，等着他回答", motion="注视，等待",
      characters=[XUN], scale="近景", power="她在给他一个自己承认的机会", emo="屏息",
      facts=[F[11]], focus="等待答案的眼神",
      audio_plan=audio(B[5], [(0.0, "ambience", "大厅人声退远", "问题出口"),
                              (0.55, "duck", "环境压到最低", "你说他会是谁"),
                              (0.9, "silence", "留一拍空白", "等待回答")],
                       ambience="大厅人声退远", energy=0.4))
    A(beat=B[5], func="reaction", strategy="direct-assets",
      narration="他心头猛地一跳，讪笑着否认。", quote=L_DENY, events=["event_016"],
      turns=[turn(YAN, "咳……我，我怎么知道？", L_DENY, mode="visible_dialogue", derivation="verbatim", emotion="心虚"),
             turn(YAN, "那么小，我们都还在地上爬呢，我哪知道。", L_DENY,
                  mode="visible_dialogue", derivation="verbatim", emotion="讪笑")],
      visual="楚焱讪笑两声，心虚地把目光转向大厅内", motion="讪笑，目光急转",
      characters=[YAN], scale="近景", power="被拆穿却嘴硬", emo="好笑又心疼", focus="心虚的眼神")
    A(beat=B[5], func="cliffhanger", strategy="story-keyframe",
      narration="她笑意柔和，目光落回书上。", quote=L_TEASE, events=["event_016", "event_017"],
      turns=[turn(XUN, "虽然知道他是好意，可烟儿不管怎么说也是女孩子吧？", L_TEASE,
                  mode="visible_dialogue", derivation="verbatim", emotion="柔和"),
             turn(XUN, "若是烟儿寻出了那人，哼……", L_TEASE,
                  mode="visible_dialogue", derivation="verbatim", emotion="意味深长")],
      visual="楚烟儿小嘴泛起柔和笑意，目光转回书页，侧脸从容；身旁的楚焱嘴角裂了裂",
      motion="笑意浮起，垂眸看书，尾音拖长",
      characters=[XUN, YAN], scale="中近景", power="主动权彻底在她手里", emo="悬念与暧昧并起",
      facts=[F[11]], focus="垂眸看书时那抹笑",
      kf=["集尾定格构图", "双人关系反转"],
      camera=cam("双人中近景", AXIS, DIR, motivation="结尾焦点收束", trajectory="极缓推近", end="双人近景"),
      audio_plan=audio(B[5], [(0.0, "ambience", "大厅低语回升", "她垂眸"),
                              (0.35, "music_rise", "主题旋律进入", "哼字出口"),
                              (0.9, "release", "留两秒纯音乐", "画面定格")],
                       ambience="大厅低语", energy=0.5))
    return b.shots


SHOWRUNNER = {
    "planning_mode": "planner",
    "retention": {
        "target_duration_seconds": 140.0, "max_attention_gap_ratio": 0.25,
        "beats": [
            beat(B[0], "hook", 0.0, 0.05, "满堂宾客为何没有他的座位？她又是谁？", "一句话压住三位长老",
                 [F[3]], ["event_011"], "疑问转好奇", [1, 2], L_INVITE),
            beat(B[1], "question", 0.05, 0.28, "他明明在练，战之气为什么还是留不住？",
                 "观众亲眼看见凶手就在他手上", [F[4], F[5]], ["event_001", "event_002"],
                 "共情转悚然", [3, 4, 5, 6, 7, 8], L_RINGGLOW),
            beat(B[2], "pressure", 0.28, 0.5, "父亲口中的贵客究竟什么来头？", "外来强者远超族长",
                 [F[6], F[7]], ["event_003", "event_004", "event_005", "event_006", "event_007"],
                 "好奇转压迫", [9, 10, 11, 12, 13, 14, 15], L_MOON7),
            beat(B[3], "escalation", 0.5, 0.7, "当着贵客的面不给座位，他要怎么下台？", "羞辱做到明面上",
                 [F[8], F[9]], ["event_008", "event_009", "event_010"],
                 "尴尬转窝火", [16, 17, 18, 19, 20, 21], L_NOSEAT),
            beat(B[4], "payoff", 0.7, 0.87, "谁会替他解围？", "冷开场兑现：她一句话让长老噤声",
                 [F[3], F[10]], ["event_011", "event_012", "event_013", "event_014"],
                 "解气转暖", [22, 23, 24, 25, 26, 27, 28], L_INVITE),
            beat(B[5], "cliffhanger", 0.87, 1.0, "她到底知道多少？", "她一直知道，而且要他自己承认",
                 [F[10], F[11]], ["event_015", "event_016", "event_017"],
                 "反转转暧昧悬念", [29, 30, 31, 32], L_TEASE),
        ],
        "ending_open_loop": "楚烟儿早就知道那个人是谁——她打算怎么让他承认？贵客此行又为何而来？",
    },
    "information_states": [
        fact(F[3], "楚烟儿在家族中的分量已高到三位长老都不敢反驳", "confirmed", "knows", "simultaneous_reveal",
             [(XUN, "knows", "从容使用这份分量"), (YAN, "suspects", "亲眼见到才确信"),
              (ZHAN, "knows", "乐见其成")],
             ["event_011"], L_SILENCE, B[4]),
        fact(F[4], "楚焱每日修炼吸入的战之气仍在无声消失", "confirmed", "knows", "simultaneous_reveal",
             [(YAN, "knows", "愤怒却查不出原因")], ["event_001", "event_002"], L_ANGRY, B[1]),
        fact(F[5], "在楚焱闭目修炼时，指上黑色古戒会发光", "confirmed", "knows", "viewer_leads",
             [(YAN, "unaware", "闭着眼毫无察觉")], ["event_001"], L_RINGGLOW, B[1]),
        fact(F[6], "连老管家都为楚焱衰退的天赋惋惜", "confirmed", "knows", "character_leads",
             [(BUTLER, "knows", "不忍表露"), (YAN, "unaware", "未看见对方转身后的神情")],
             ["event_004"], L_PITY, B[2]),
        fact(F[7], "来访的月白老者是七星大战师，实力高出族长两星", "confirmed", "knows", "simultaneous_reveal",
             [(YAN, "knows", "看纹章认出"), (ELDER, "knows", ""), (ZHAN, "knows", "")],
             ["event_006", "event_007"], L_MOON7, B[2]),
        fact(F[8], "大厅座席被刻意安排成没有楚焱的位置", "confirmed", "knows", "misunderstanding",
             [(YAN, "knows", "看穿是故意的"), (ZHAN, "knows", "当场发作")],
             ["event_008", "event_009"], L_NOSEAT, B[3]),
        fact(F[9], "二长老敢当着贵客的面给族长之子难堪", "confirmed", "knows", "simultaneous_reveal",
             [(ZHAN, "knows", "怒而不能发作")], ["event_010"], L_MOCKEYE, B[3]),
        fact(F[10], "楚焱与楚烟儿已有三年没有单独相处", "confirmed", "knows", "character_leads",
             [(XUN, "knows", "记得很清楚"), (YAN, "knows", "刻意回避")],
             ["event_013"], L_THREEYEARS, B[4]),
        fact(F[11], "四岁到六岁间每夜替楚烟儿温养经脉的人就是楚焱，而她早已知道", "confirmed", "knows",
             "character_leads",
             [(XUN, "knows", "一直知道，只等他承认"), (YAN, "knows", "以为无人知晓")],
             ["event_015", "event_016", "event_017"], L_SECRET1, B[5]),
    ],
    "character_state_deltas": [
        delta(YAN, ["event_001", "event_002"], {"confidence_state": "仍抱一线希望地每日修炼"},
              {"confidence_state": "亲身确认努力被彻底抹去"}, L_ANGRY,
              "收功后猛然攥拳", "从专注到暴怒的落差"),
        delta(YAN, ["event_003"], {"emotional_state": "独处时的愤懑"},
              {"emotional_state": "被传唤后收敛情绪"}, L_CALL,
              "换过衣衫出门", "神色迅速平复"),
        delta(BUTLER, ["event_004"], {"emotional_state": "对三少爷的恭谨"},
              {"emotional_state": "转身后掩不住的惋惜"}, L_PITY,
              "转身时老眼一黯", "点头和善，转身叹息"),
        delta(YAN, ["event_005", "event_006"], {"power_level": "自认三段之身无足轻重"},
              {"power_level": "亲眼看见七星大战师的差距"}, L_MOON7,
              "视线停在纹章上心头一凛", "呼吸一滞"),
        delta(YAN, ["event_007"], {"emotional_state": "对来客的好奇"},
              {"emotional_state": "对绝顶天赋的暗自惊异"}, L_GENIUS,
              "轻吸一口凉气随即移开目光", "克制，不多看一眼"),
        delta(YAN, ["event_008", "event_009"], {"social_status": "族长之子，尚有席位"},
              {"social_status": "当着贵客的面被抹去座位"}, L_NOSEAT,
              "满座之中唯一站着的人", "面色不变而眼底转冷"),
        delta(ZHAN, ["event_010"], {"emotional_state": "与贵客笑谈的从容"},
              {"emotional_state": "为儿子受辱而当众动怒"}, L_ANGRY2,
              "脸庞闪过怒气并皱眉瞪视", "压着火气，点到为止"),
        delta(XUN, ["event_011"], {"social_status": "九段天才少女"},
              {"social_status": "分量足以让三位长老当众噤声"}, L_SILENCE,
              "合书抬眼便压住满堂", "从容不动，语气平淡"),
        delta(YAN, ["event_012"], {"relationship_state": "刻意与烟儿保持距离"},
              {"relationship_state": "当众接受她的解围并坐到她身旁"}, L_JEALOUS,
              "穿过嫉妒目光落座角落", "落座时侧头低语"),
        delta(XUN, ["event_013", "event_014"], {"relationship_state": "被他刻意疏远三年"},
              {"relationship_state": "当面点破这三年的疏远"}, L_THREEYEARS,
              "睫毛垂在书页上侧脸幽怨", "声音放低，语气幽幽"),
        delta(XUN, ["event_015", "event_017"], {"relationship_state": "当面点破这三年的疏远"},
              {"relationship_state": "挑明旧事并握住主动权"}, L_SECRET,
              "偏头嫣然一笑锁住他", "慢条斯理，一字一句"),
        delta(YAN, ["event_016"], {"emotional_state": "自嘲而封闭"},
              {"emotional_state": "秘密被点破后的心虚慌乱"}, L_DENY,
              "讪笑着把目光转开", "语速变快，视线躲闪"),
    ],
}

DRAMATURGY = {
    "genre_engine": "status-power-mystery",
    "dramatic_question": "满堂宾客都不给他一个座位，谁还愿意站到他这一边？",
    "cold_open": "肃穆大厅里少年孤零零站着无处可坐，角落忽然响起少女的一句：楚焱哥哥，坐这里吧。",
    "cold_open_source_quote": L_INVITE,
    "status_before": "楚焱晨起修炼再次失败，被传唤到迎客大厅面见贵客。",
    "status_after": "他当众失去座位又被烟儿解围，而烟儿点破了他藏了十年的旧事。",
    "conflict_beats": ["修炼的战之气再次凭空消失", "戒指在他闭眼时发光而他毫无察觉",
                       "月白三人的实力远超族长", "二长老当着贵客抹去他的座位",
                       "烟儿一句话压住长老并点破旧事"],
    "reveal_order": ["战之气再次消失", "戒指发光", "七星大战师", "没有座位", "长老的讥讽",
                     "烟儿解围", "三年未曾单独相处", "温养经脉的人是谁"],
    "cliffhanger": "她早就知道那个人是谁，只是一直等着他自己承认。",
    "narration_budget_ratio": 0.2,
}

TITLE_TEXT = "客人"
HOOK = "满堂宾客没给他留一个座位，开口解围的，是全族最不该管他的那个人。"
SUMMARY = "楚焱晨起修炼，吸入的战之气再次消失，而他闭目时指上古戒正无声发光。族长传他去迎客大厅面见贵客——一位七星大战师带着两名年轻强者。二长老却刻意抹去他的座位，当众令他难堪。角落里的楚烟儿一句话让三位长老噤声，请他坐到自己身旁，随后不动声色地点破了他四岁到六岁间每夜替她温养经脉的旧事。"
PREVIEW = "烟儿会怎么让他承认？月白三人此行又究竟为何而来？"
EXTERNALIZED = {"event_004", "event_007", "event_009", "event_014"}
REMOVED: set[str] = set()
