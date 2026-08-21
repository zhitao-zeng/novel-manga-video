"""Episode 2 -- 战气大陆.

The chapter is mostly world-building prose: three of its eighteen events are
pure taxonomy with no character in them.  Lecturing that to the audience is
exactly the explanatory narration the profile forbids, so those events are
removed and the only rule that matters dramatically -- one year, seven
段, or you are reassigned out of the family -- arrives through the father.
The transmigration secret is the episode's information gap: the audience
learns it, nobody on screen knows.
"""
from build_s1 import (
    B, F, YAN, ZHAN, ShotBuilder, audio, beat, cam, delta, fact, perf, turn,
)

L_MOON = "月如银盘，漫天繁星。"
L_LIE = "山崖之巅，楚焱斜躺在草地之上，嘴中叼着一根青草，微微嚼动，任由那淡淡的苦涩在嘴中弥漫开来。"
L_SIGH = '"唉……"想起下午的测试，楚焱轻叹了一口气，懒懒地抽回手掌，双手枕着脑袋，眼神有些恍惚。'
L_FIFTEEN = '"十五年了呢……"低低的自喃声，忽然毫无边际地从少年嘴中轻吐了出来。'
L_SECRET = "在楚焱的心中，有一个仅有他自己知道的秘密：他并不是这个世界的人。或者说，楚焱的灵魂，并不属于这个世界。他来自一个名叫地球的蔚蓝星球。"
L_CROSS = "不过在生活了一段时间之后，他还是后知后觉地明白了过来：他穿越了！"
L_ROAR = '"呸。"吐出嘴中的草根，楚焱忽然跳起身来，脸庞狰狞，对着夜空失态地咆哮道'
L_ROAR2 = "把老子穿过来当废物玩吗"
L_SOUL = "然而，当来到这片苍澜大陆之后，楚焱却是惊喜地发现，因为两世的经验，他的灵魂，竟然比常人要强上许多！"
L_SOULRULE = "要知道，在苍澜大陆，灵魂是天生的。或许它能随着年龄的增长而稍稍变强，可却从没有什么功法能够单独修炼灵魂。"
L_TALENT = "灵魂的强化，也造就出楚焱的修炼天赋。同样，也造就了他的天才之名。"
L_LOST = "不过，很可惜，在十一岁那年，天才之名，逐渐被突如其来的变故剥夺而去。而天才，也是在一夜间，沦落成了路人口中嘲笑的废物！"
L_CALM = "在咆哮了几嗓子之后，楚焱的情绪也是缓缓地平息了下来，脸庞再次回复了平日的落寞。"
L_WRONGED = "苦涩地摇了摇头，楚焱心中其实有些委屈。毕竟他对自己身体究竟发生了什么事，也是一概不知。"
L_STRONGER = "灵魂，随着年龄的增加，也是越来越强大。而且吸收战之气的速度，比几年前最巅峰的状态还要强盛上几分。"
L_GONE = "可那些进入体内的战之气，却都是无一例外地消失得干干净净。诡异的情形，让得楚焱黯然神伤。"
L_RING = "手指上有一颗黑色戒指，戒指很是古朴，不知是何材料所铸，其上还绘有些模糊的纹路。这是母亲临死前送给他的唯一礼物。"
L_MOTHER = '手指轻轻地抚摸着戒指，楚焱苦笑道："这几年，还真是辜负母亲的期望了……"'
L_FATHER_HERE = '深深地吐了一口气，楚焱忽然回转过头，对着漆黑的树林温暖地笑道："父亲，您来了？"'
L_SENSE = "虽然战之气只有三段，不过楚焱的灵魂感知，却是比一名五星战者都要敏锐许多。"
L_LAUGH = '"呵呵，焱儿，这么晚了，怎么还待在这上面呢？"树林中，在静了片刻后，传出男子的关切笑声。'
L_ZHAN = "他便是楚家现任族长，同时也是楚焱的父亲，五星大战师，楚战！"
L_NOTREST = '"父亲，您不也还没休息么？"望着中年男子，楚焱脸庞上的笑容更浓了一分。'
L_THINKING = '"焱儿，还在想下午测验的事呢？"大步上前，楚战笑道。'
L_EXPECTED = '"呵呵，有什么好想的，意料之中而已。"楚焱少年老成地摇了摇头，笑容却是有些勉强。'
L_FIFTEEN_Q = '"焱儿，你十五岁了吧？"'
L_YES = '"嗯，父亲。"'
L_CEREMONY = '"再有一年，似乎……就该进行成年仪式了……"楚战苦笑道。'
L_ONEYEAR = '"是的，父亲，还有一年！"手掌微微一紧，楚焱平静地回道。'
L_RULE = "只要度过了成年仪式，那么没有修炼潜力的他，便将会被取消进入战气阁寻找战气功法的资格，从而被分配到家族的各处产业之中，为家族打理一些普通事物。"
L_RULE2 = "毕竟，若是在二十五岁之前没有成为一名战者，那将不会被家族所认可！"
L_SORRY = '"对不起了，焱儿。如果在一年后你的战之气达不到七段，那么父亲也只得忍痛把你分配到家族的产业中去。毕竟，这个家族，还并不是父亲一人说了算。那几个老家伙，可随时等着父亲犯错呢……"望着平静的楚焱，楚战有些歉疚地叹道。'
L_ELDERS = L_SORRY
L_PROMISE = '"父亲，我会努力的。一年后，我一定会到达七段战之气的！"楚焱微笑着安慰道。'
L_FOUR = '"一年，四段？呵呵，如果是以前，或许还有可能吧。不过现在……基本没半点机会……"虽然口中在安慰着父亲，不过楚焱心中却是自嘲地苦笑了起来。'
L_GUEST = '"不早了，回去休息吧。明天，家族中有贵客，你可别失了礼。"'
L_WHO = '"贵客？谁啊？"楚焱好奇地问道。'
L_TOMORROW = '"明天就知道了。"对着楚焱挤了挤眼睛，楚战大笑而去，留下无奈的楚焱。'
L_TRY = '"放心吧，父亲，我会尽力的！"抚摸着手指上的古朴戒指，楚焱抬头喃喃道。'
L_GLOW = "在楚焱抬头的那一刹，手指中的黑色古戒，却是忽然亮起了一抹极其微弱的诡异毫光。毫光眨眼便逝，没有引起任何人的察觉。"

LINES = [v for k, v in list(globals().items()) if k.startswith("L_")]
AXIS = "崖顶主轴，银月在画面右上，树林在左后"
DIR = "楚焱恒居画面左侧，楚战自右后方树林进入"


def build(location):
    b = ShotBuilder(location)
    b.axis, b.direction = AXIS, DIR
    A = b.add

    # ---------------------------------------------------- B1 hook：前置最后通牒
    A(beat=B[0], func="establish", strategy="story-keyframe",
      narration="夜色里，父亲的声音落下一句判决。", quote=L_YES, events=["event_013", "event_015"],
      turns=[turn(ZHAN, "如果在一年后你的战之气达不到七段，那么父亲也只得忍痛把你分配到家族的产业中去。",
                  L_SORRY, mode="offscreen_dialogue", derivation="verbatim", emotion="歉疚沉重")],
      visual="山崖之巅夜景，银盘般的月亮悬在漫天繁星之中，崖边一道少年剪影背对镜头站着",
      motion="夜风掠过草叶，少年身影一动不动",
      characters=[], scale="全景", power="族规压过父子情", emo="心口一沉",
      facts=[F[5]], focus="崖顶剪影与那句判决",
      kf=["开场判决与孤绝夜景", "隐藏主角正脸"],
      audio_plan=audio(B[0], [(0.0, "ambience", "夜风与虫鸣", "开场"),
                              (0.4, "duck", "父亲开口压低夜风", "台词进入"),
                              (0.9, "impact", "一记低沉闷响", "七段二字落下")],
                       ambience="夜风与虫鸣", energy=0.55))
    A(beat=B[0], func="withhold", strategy="direct-assets",
      narration="少年没有回头。", quote=L_YES, events=["event_013"],
      turns=[turn(YAN, "还有一年。", L_ONEYEAR, mode="inner_voice", derivation="derived", emotion="平静下的紧绷")],
      visual="少年背影特写，垂在身侧的手掌缓缓握紧", motion="手掌收紧，指节发白",
      characters=[YAN], scale="近景", power="被倒计时逼住", emo="悬念绷起",
      facts=[F[5]], focus="握紧的手")

    # ---------------------------------------------------- B2 question：崖顶秘密
    A(beat=B[1], func="transition", strategy="scene-only",
      narration="时间回到入夜，崖顶。", quote=L_MOON, events=["event_001"],
      turns=[turn("旁白", "入夜。", L_MOON, mode="narration", derivation="derived", emotion="平静")],
      visual="山崖之巅，月如银盘悬于漫天繁星之下，草地在夜风中起伏", motion="云影掠过月面",
      characters=[], scale="全景", power="时间回溯", emo="呼吸放缓", focus="银月与星空")
    A(beat=B[1], func="establish", strategy="direct-assets",
      narration="他斜躺在草地上，嘴里叼着一根青草。", quote=L_LIE, events=["event_001"],
      turns=[turn(YAN, "唉……", L_SIGH, mode="visible_dialogue", derivation="verbatim", emotion="疲惫")],
      visual="楚焱斜躺草地，嘴中叼着青草微微嚼动，一只手挡在眼前透过指缝望月",
      motion="嚼草，抽回手掌，双手枕到脑后",
      characters=[YAN], scale="中景", power="独处时的松弛与疲惫", emo="代入孤独",
      focus="指缝间的月光与少年的眼")
    A(beat=B[1], func="reveal", strategy="direct-assets",
      narration="一句没头没尾的自喃。", quote=L_FIFTEEN, events=["event_001"],
      turns=[turn(YAN, "十五年了呢……", L_FIFTEEN, mode="visible_dialogue", derivation="verbatim", emotion="恍惚")],
      visual="楚焱仰躺望月的面部特写，眼神有些恍惚", motion="嘴唇轻动，目光失焦",
      characters=[YAN], scale="近景", power="一个只有他自己听得懂的数字", emo="疑窦",
      facts=[F[6]], focus="恍惚的眼神与那个数字")
    A(beat=B[1], func="reveal", strategy="story-keyframe",
      narration="这个世界没有人知道他从哪里来。", quote=L_SECRET, events=["event_001"],
      turns=[turn(YAN, "这具身体十五岁。可我记得的，不止十五年。", L_SECRET,
                  mode="inner_voice", derivation="derived", emotion="压得很低的坦白"),
             turn(YAN, "我不是这里的人。", L_CROSS, mode="inner_voice", derivation="derived", emotion="平静而荒诞")],
      visual="星空占满画面上方，少年仰躺其下显得极小，繁星倒映在他漆黑的瞳孔里",
      motion="星空缓缓流转，少年一动不动",
      characters=[YAN], scale="全景", power="观众独享的秘密", emo="信息差建立",
      facts=[F[6]], focus="被星空吞没的少年",
      kf=["秘密揭示的意象构图", "人物与星空的比例关系"],
      audio_plan=audio(B[1], [(0.0, "silence", "环境声抽空", "内心声进入"),
                              (0.5, "music_rise", "极轻的弦乐浮起", "不是这里的人一句"),
                              (0.95, "release", "余韵留半拍", "台词收尾")],
                       ambience="near-silence", energy=0.3))

    # ---------------------------------------------------- B3 pressure：不甘
    A(beat=B[2], func="pressure", strategy="direct-assets",
      narration="他猛地跳起身，对着夜空咆哮。", quote=L_ROAR, events=["event_005"],
      turns=[turn(YAN, "呸。", L_ROAR, mode="visible_dialogue", derivation="verbatim", emotion="烦躁"),
             turn(YAN, "把老子穿过来，就是当废物玩的吗！", L_ROAR2,
                  mode="visible_dialogue", derivation="derived", emotion="失态咆哮")],
      visual="楚焱猛地跳起，对着漫天星空仰头嘶吼，脸庞因用力而狰狞",
      motion="吐掉草根，弹身而起，仰头爆发",
      characters=[YAN], scale="中景", power="向命运叫板", emo="替他出一口气",
      facts=[F[6]], focus="爆发瞬间的脸",
      audio_plan=audio(B[2], [(0.0, "ambience", "夜风", "起身"),
                              (0.3, "impact", "爆发一记", "咆哮出口"),
                              (0.8, "release", "回声散入山谷", "声嘶力竭")],
                       ambience="夜风与回声", sfx=["山谷回声"], energy=0.9))
    A(beat=B[2], func="reveal", strategy="direct-assets",
      narration="两世的记忆，让他的灵魂比常人强得多。", quote=L_SOUL, events=["event_006"],
      turns=[turn(YAN, "灵魂比这里所有人都强。", L_SOUL, mode="inner_voice", derivation="derived", emotion="自嘲的骄傲"),
             turn(YAN, "这本该是天大的便宜。", L_TALENT, mode="inner_voice", derivation="derived", emotion="苦涩")],
      visual="楚焱喘息着立在崖边，胸口起伏，月光勾出侧脸轮廓", motion="喘息渐平，垂下头",
      characters=[YAN], scale="中近景", power="天赋仍在却无处安放", emo="不甘",
      facts=[F[7]], focus="喘息中的侧脸")
    A(beat=B[2], func="reaction", strategy="direct-assets",
      narration="他曾经真的站上过那个位置。", quote=L_LOST, events=["event_007"],
      turns=[turn(YAN, "十一岁那年，全没了。", L_LOST, mode="inner_voice", derivation="derived", emotion="低沉"),
             turn(YAN, "天才两个字，一夜之间变成了废物。", L_LOST, mode="inner_voice", derivation="derived", emotion="自嘲")],
      visual="楚焱背对镜头立于崖边，脚下是深不见底的夜色山谷", motion="肩线松垮下来",
      characters=[YAN], scale="全景", power="从神坛跌落", emo="落寞", facts=[F[7]], focus="崖边的孤影")

    # ---------------------------------------------------- B4 reveal：谜与戒指
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="最想不通的是身体本身。", quote=L_WRONGED, events=["event_008"],
      turns=[turn(YAN, "查过很多次，身体没有一处不对。", L_WRONGED, mode="inner_voice", derivation="derived", emotion="困惑"),
             turn(YAN, "吸收战之气的速度，比巅峰时还快。", L_STRONGER, mode="inner_voice", derivation="derived", emotion="更困惑")],
      visual="楚焱摊开手掌，掌心浮起一缕极淡的白色气流", motion="气流在掌心盘旋",
      characters=[YAN], scale="近景", power="天赋与结果彻底矛盾", emo="谜团加深",
      facts=[F[8]], focus="掌心的白色气流",
      audio_plan=audio(B[3], [(0.0, "ambience", "夜风压低", "摊掌"),
                              (0.5, "sfx", "细微的气流嗡鸣", "气流浮起")],
                       ambience="夜风压低", sfx=["气流嗡鸣"], energy=0.35))
    A(beat=B[3], func="reveal", strategy="story-keyframe",
      narration="可进去多少，就消失多少。", quote=L_GONE, events=["event_008"],
      turns=[turn(YAN, "进去多少，就干干净净地消失多少。", L_GONE, mode="inner_voice", derivation="derived", emotion="黯然"),
             turn(YAN, "到底是什么在吃掉它？", L_GONE, mode="inner_voice", derivation="derived", emotion="悚然")],
      visual="掌心白色气流被无形之物抽走，转瞬消散，只剩空掌与月光",
      motion="气流骤然被抽尽，手指微颤",
      characters=[YAN], scale="近景", power="有东西在暗中夺走他的一切", emo="全集最大悬念",
      facts=[F[8]], focus="气流消失的一瞬",
      kf=["核心谜题的视觉化", "气流消散的关键帧"])
    A(beat=B[3], func="reveal", strategy="story-keyframe",
      narration="他抬起手，指上是母亲留下的黑色古戒。", quote=L_RING, events=["event_009"],
      turns=[turn(YAN, "这几年，还真是辜负母亲的期望了……", L_MOTHER,
                  mode="visible_dialogue", derivation="verbatim", emotion="苦笑")],
      visual="月光下的手指特写，一枚古朴的黑色戒指，其上绘有模糊纹路",
      motion="拇指轻轻摩挲戒面",
      characters=[YAN], scale="近景", power="遗物承载的期望", emo="心softened",
      facts=[F[9]], focus="戒指上的模糊纹路",
      kf=["关键道具首次特写", "全集悬念载体"])
    A(beat=B[3], func="reaction", strategy="direct-assets",
      narration="这枚戒指他戴了十年。", quote=L_RING, events=["event_009"],
      turns=[turn(YAN, "四岁那年戴上，戴了整整十年。", L_RING, mode="inner_voice", derivation="derived", emotion="眷恋"),
             turn(YAN, "母亲留下的，就只有它了。", L_RING, mode="inner_voice", derivation="derived", emotion="低回"),
             turn(YAN, "您要是还在，会怎么说？", L_MOTHER, mode="inner_voice", derivation="derived", emotion="轻声")],
      visual="月光下少年低头看着指上古戒，眼神放软", motion="低头，指腹反复摩挲",
      characters=[YAN], scale="中近景", power="唯一的牵系", emo="柔软一拍",
      facts=[F[9]], focus="反复摩挲的指腹")

    # ---------------------------------------------------- B5 payoff：父子
    A(beat=B[4], func="advance", strategy="direct-assets",
      narration="他忽然回头，对着漆黑的树林笑起来。", quote=L_FATHER_HERE, events=["event_010"],
      turns=[turn(YAN, "父亲，您来了？", L_FATHER_HERE, mode="visible_dialogue", derivation="verbatim", emotion="温暖")],
      visual="楚焱回转过头望向身后漆黑树林，脸上浮起温暖笑意", motion="回头，笑意浮起",
      characters=[YAN], scale="中近景", power="他先察觉了对方", emo="意外的暖",
      facts=[F[7]], focus="回头一笑",
      audio_plan=audio(B[4], [(0.0, "ambience", "夜风", "回头"),
                              (0.35, "sfx", "树林里一声极轻的枝叶摩擦", "察觉动静")],
                       ambience="夜风", sfx=["枝叶摩擦"], energy=0.4))
    A(beat=B[4], func="reveal", strategy="story-keyframe",
      narration="中年人跃出树林，脸上带着笑意。", quote=L_LAUGH, events=["event_011"],
      turns=[turn(ZHAN, "呵呵，焱儿，这么晚了，怎么还待在这上面呢？", L_LAUGH,
                  mode="visible_dialogue", derivation="verbatim", emotion="关切")],
      visual="身着华贵灰色衣衫的中年人自树林跃出，龙行虎步颇有威严，粗眉添几分豪气，凝视着月光下的儿子",
      motion="枝叶摇摆，人影落地，站定",
      characters=[ZHAN, YAN], scale="中景", power="族长的威严与父亲的关切并存", emo="人物登场",
      focus="跃出树林的身影", kf=["重要人物首次登场", "父子同框"])
    A(beat=B[4], func="advance", strategy="direct-assets",
      narration="少年笑意更浓。", quote=L_NOTREST, events=["event_011"],
      turns=[turn(YAN, "父亲，您不也还没休息么？", L_NOTREST, mode="visible_dialogue", derivation="verbatim", emotion="亲近")],
      visual="楚焱望着父亲，脸上笑容更浓一分", motion="笑意加深",
      characters=[YAN], scale="近景", power="在父亲面前才卸下防备", emo="温情", focus="难得放松的笑")
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="父亲大步上前，问起下午的测验。", quote=L_THINKING, events=["event_012"],
      turns=[turn(ZHAN, "焱儿，还在想下午测验的事呢？", L_THINKING, mode="visible_dialogue", derivation="verbatim", emotion="试探关切"),
             turn(YAN, "呵呵，有什么好想的，意料之中而已。", L_EXPECTED,
                  mode="visible_dialogue", derivation="verbatim", emotion="勉强")],
      visual="父子并肩立于崖边，父亲侧头看儿子，儿子望向远处", motion="并肩站定，视线错开",
      characters=[ZHAN, YAN], scale="中景", power="关心与逞强的错位", emo="心疼",
      focus="错开的视线")
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="父亲沉默片刻，忽然问起年龄。", quote=L_FIFTEEN_Q, events=["event_013"],
      turns=[turn(ZHAN, "焱儿，你十五岁了吧？", L_FIFTEEN_Q, mode="visible_dialogue", derivation="verbatim", emotion="欲言又止"),
             turn(YAN, "嗯，父亲。", L_YES, mode="visible_dialogue", derivation="verbatim", emotion="平静")],
      visual="楚战望着儿子稚嫩的清秀脸庞，叹了口气", motion="叹气，沉默",
      characters=[ZHAN, YAN], scale="中近景", power="话题转向不可回避的规则", emo="预感不妙",
      focus="父亲欲言又止的神情")
    A(beat=B[4], func="reveal", strategy="direct-assets",
      narration="再有一年，就是成年仪式。", quote=L_CEREMONY, events=["event_013", "event_014"],
      turns=[turn(ZHAN, "再有一年，似乎……就该进行成年仪式了……", L_CEREMONY,
                  mode="visible_dialogue", derivation="verbatim", emotion="苦笑"),
             turn(YAN, "是的，父亲，还有一年！", L_ONEYEAR, mode="visible_dialogue", derivation="verbatim", emotion="手掌收紧")],
      visual="父子对立，楚焱垂在身侧的手掌微微收紧", motion="手掌收紧，神色不变",
      characters=[ZHAN, YAN], scale="中景", power="家族时钟开始倒数", emo="紧绷",
      facts=[F[5]], focus="收紧的手掌")
    A(beat=B[4], func="reveal", strategy="direct-assets",
      narration="仪式过后没有潜力的人会被分配走。", quote=L_RULE, events=["event_014"],
      turns=[turn(YAN, "过了仪式，没潜力的人就进不了战气阁。", L_RULE, mode="inner_voice", derivation="derived", emotion="清楚得很"),
             turn(YAN, "二十五岁前成不了战者，家族就不认你。", L_RULE2, mode="inner_voice", derivation="derived", emotion="冰冷")],
      visual="楚焱平静的面部特写，眼底却是一片冷", motion="眼神沉下去",
      characters=[YAN], scale="近景", power="族规不因他是族长之子而弯曲", emo="窒息感",
      facts=[F[5]], focus="平静表面下的冷")
    A(beat=B[4], func="payoff", strategy="story-keyframe",
      narration="父亲歉疚地说出那句判决——冷开场在此兑现。", quote=L_SORRY, events=["event_015"],
      turns=[turn(ZHAN, "对不起了，焱儿。如果在一年后你的战之气达不到七段，那么父亲也只得忍痛把你分配到家族的产业中去。",
                  L_SORRY, mode="visible_dialogue", derivation="verbatim", emotion="歉疚沉重")],
      visual="月光下父亲侧身望着儿子，眉宇间是无法回避的歉疚，儿子静静听着",
      motion="父亲抬手又放下，最终只是叹息",
      characters=[ZHAN, YAN], scale="中景", power="族长也保不住自己的儿子", emo="冷开场回收",
      facts=[F[5]], focus="说出判决时父亲的眼神",
      kf=["全集情感高点", "父子关系定格"],
      camera=cam("中景", AXIS, DIR, motivation="情绪转折", trajectory="极缓推近", end="中近景"))
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="他补了一句家族内部的难处。", quote=L_ELDERS, events=["event_015"],
      turns=[turn(ZHAN, "这个家族，还并不是父亲一人说了算。那几个老家伙，可随时等着父亲犯错呢……",
                  L_SORRY, mode="visible_dialogue", derivation="verbatim", emotion="无奈")],
      visual="楚战望向远处族地方向，神色转沉", motion="转头望向山下灯火",
      characters=[ZHAN], scale="中近景", power="族内暗流", emo="埋下伏笔",
      facts=[F[10]], focus="望向山下的眼神")
    A(beat=B[4], func="pressure", strategy="direct-assets",
      narration="他问起族里最顶的那门功法。", quote=L_RULE, events=["event_014"],
      turns=[turn(YAN, "父亲，狂狮怒罡，我这辈子是碰不到了吧。", L_RULE,
                  mode="visible_dialogue", derivation="derived", emotion="试探的自嘲"),
             turn(ZHAN, "那是族长才有资格修炼的。", L_RULE,
                  mode="visible_dialogue", derivation="derived", emotion="沉默一瞬后开口"),
             turn(ZHAN, "可你要先是个战者，才轮得到谈功法。", L_RULE2,
                  mode="visible_dialogue", derivation="derived", emotion="点到要害")],
      visual="父子并肩立于崖边，父亲侧头看向儿子，儿子望着自己的掌心",
      motion="儿子摊掌又收拢，父亲摇头",
      characters=[ZHAN, YAN], scale="中景", power="连门槛都还没够到", emo="更窒息",
      facts=[F[5]], focus="摊开又收拢的掌心")
    A(beat=B[4], func="reveal", strategy="direct-assets",
      narration="父亲问起他每天到底在练什么。", quote=L_STRONGER, events=["event_016"],
      turns=[turn(ZHAN, "这一年，你每天都在练？", L_STRONGER,
                  mode="visible_dialogue", derivation="derived", emotion="心疼"),
             turn(YAN, "一天没停过。吸进去的比从前还快。", L_STRONGER,
                  mode="visible_dialogue", derivation="derived", emotion="平静"),
             turn(YAN, "可留不住，一点都留不住。", L_GONE,
                  mode="visible_dialogue", derivation="derived", emotion="终于说出口"),
             turn(ZHAN, "……那便够了。剩下的，交给天意。", L_STRONGER, mode="visible_dialogue", derivation="derived", emotion="哽了一下")],
      visual="父亲抬手想拍儿子的肩，手悬在半空又轻轻落下", motion="抬手，悬停，落下",
      characters=[ZHAN, YAN], scale="中近景", power="父亲知道再逼也没用", emo="酸楚",
      facts=[F[7]], focus="悬在半空的那只手")
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="少年笑着安慰父亲。", quote=L_PROMISE, events=["event_016"],
      turns=[turn(YAN, "父亲，我会努力的。一年后，我一定会到达七段战之气的！", L_PROMISE,
                  mode="visible_dialogue", derivation="verbatim", emotion="微笑安慰")],
      visual="楚焱转向父亲微笑，笑得很稳", motion="转身，微笑，直视父亲",
      characters=[YAN], scale="近景", power="儿子反过来安慰父亲", emo="心酸的懂事", focus="很稳的笑")
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="心里却是另一句话。", quote=L_FOUR, events=["event_016"],
      turns=[turn(YAN, "一年四段。以前或许还有可能。", L_FOUR, mode="inner_voice", derivation="derived", emotion="自嘲"),
             turn(YAN, "现在，基本没半点机会。", L_FOUR, mode="inner_voice", derivation="derived", emotion="清醒")],
      visual="楚焱笑容不变的面部特写，眼底笑意褪去", motion="笑容维持，眼神冷下来",
      characters=[YAN], scale="近景", power="表里两层", emo="更心酸",
      facts=[F[5]], focus="笑着的嘴与不笑的眼")
    A(beat=B[4], func="transition", strategy="direct-assets",
      narration="父亲拍拍他的脑袋，提起明日的贵客。", quote=L_GUEST, events=["event_017"],
      turns=[turn(ZHAN, "不早了，回去休息吧。明天，家族中有贵客，你可别失了礼。", L_GUEST,
                  mode="visible_dialogue", derivation="verbatim", emotion="轻快转场"),
             turn(YAN, "贵客？谁啊？", L_WHO, mode="visible_dialogue", derivation="verbatim", emotion="好奇")],
      visual="楚战抬手拍了拍儿子的脑袋，转身欲走", motion="拍头，转身",
      characters=[ZHAN, YAN], scale="中景", power="话题被轻轻带过", emo="好奇被勾起",
      facts=[F[11]], focus="拍在头上的手")
    A(beat=B[4], func="withhold", strategy="direct-assets",
      narration="父亲挤了挤眼睛，大笑而去。", quote=L_TOMORROW, events=["event_017"],
      turns=[turn(ZHAN, "明天就知道了。", L_TOMORROW, mode="visible_dialogue", derivation="verbatim", emotion="卖关子的笑")],
      visual="楚战回头挤眼睛，大笑着走入树林，留下无奈的少年", motion="挤眼，大笑，背影没入林中",
      characters=[ZHAN], scale="中景", power="父亲故意留一手", emo="轻松一拍",
      facts=[F[11]], focus="挤眼睛的瞬间")

    # ---------------------------------------------------- B6 cliffhanger
    A(beat=B[5], func="cliffhanger", strategy="direct-assets",
      narration="他抚摸着戒指，抬头喃喃。", quote=L_TRY, events=["event_018"],
      turns=[turn(YAN, "放心吧，父亲，我会尽力的！", L_TRY, mode="visible_dialogue", derivation="verbatim", emotion="低声承诺")],
      visual="楚焱独立崖顶，拇指摩挲着指上黑色古戒，仰头望向星空", motion="摩挲戒指，缓缓抬头",
      characters=[YAN], scale="中近景", power="独自扛下倒计时", emo="沉静",
      facts=[F[9]], focus="摩挲戒指的手与抬起的脸")
    A(beat=B[5], func="cliffhanger", strategy="story-keyframe",
      narration="就在他抬头的那一刹，古戒亮起一抹诡异毫光。", quote=L_GLOW, events=["event_018"],
      turns=[turn("旁白", "毫光眨眼便逝，没有引起任何人的察觉。", L_GLOW,
                  mode="narration", derivation="verbatim", emotion="悬念收束")],
      visual="极近特写：黑色古戒表面纹路间亮起一抹极其微弱的诡异毫光，转瞬熄灭，戒指重归死寂",
      motion="毫光亮起，一闪即逝",
      characters=[YAN], scale="特写", power="真正的主宰藏在遗物里", emo="脊背发凉的期待",
      facts=[F[9]], focus="纹路间那一抹毫光",
      kf=["集尾核心悬念", "道具特写关键帧"],
      camera=cam("戒指特写", AXIS, DIR, motivation="结尾焦点收束", trajectory="极缓推近", end="更近的特写"),
      audio_plan=audio(B[5], [(0.0, "ambience", "夜风渐弱", "抬头"),
                              (0.25, "silence", "全场静音", "毫光亮起"),
                              (0.5, "bass_drop", "一记极低的下沉", "毫光熄灭"),
                              (0.85, "music_rise", "主题旋律进入", "旁白收束")],
                       ambience="near-silence", energy=0.45))
    return b.shots


SHOWRUNNER = {
    "planning_mode": "planner",
    "retention": {
        "target_duration_seconds": 150.0, "max_attention_gap_ratio": 0.25,
        "beats": [
            beat(B[0], "hook", 0.0, 0.05, "一年之内达不到七段会怎样？", "倒计时已经开始",
                 [F[5]], ["event_013", "event_015"], "心口一沉", [1, 2], L_SORRY),
            beat(B[1], "question", 0.05, 0.26, "十五年了——他在数什么？", "一个全场无人知道的秘密",
                 [F[6]], ["event_001"], "疑窦转震动", [3, 4, 5, 6], L_FIFTEEN),
            beat(B[2], "pressure", 0.26, 0.46, "灵魂比谁都强，为何还是废物？", "天赋仍在，出口却被堵死",
                 [F[7]], ["event_005", "event_006", "event_007"], "不甘转落寞", [7, 8, 9], L_SOUL),
            beat(B[3], "escalation", 0.46, 0.64, "战之气进去多少就消失多少——是什么在吃掉它？",
                 "谜底藏在母亲的遗物上", [F[8], F[9]], ["event_008", "event_009"],
                 "困惑转悚然", [10, 11, 12], L_GONE),
            beat(B[4], "payoff", 0.64, 0.86, "父亲究竟要对他说什么？", "冷开场兑现：一年，七段，否则出局",
                 [F[5], F[10], F[11]],
                 ["event_010", "event_011", "event_012", "event_013", "event_014", "event_015",
                  "event_016", "event_017"],
                 "温情转窒息", [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27], L_SORRY),
            beat(B[5], "cliffhanger", 0.86, 1.0, "戒指为什么会亮？", "夺走他战之气的东西一直戴在手上",
                 [F[9]], ["event_018"], "沉静转悚然期待", [28, 29], L_GLOW),
        ],
        "ending_open_loop": "母亲留下的古戒为何发光？它和消失的战之气有关吗？明日的贵客又是谁？",
    },
    "information_states": [
        fact(F[5], "楚焱必须在一年内达到七段战之气，否则将被分配出核心修炼圈", "confirmed", "knows",
             "simultaneous_reveal",
             [(YAN, "knows", "清楚族规不可更改"), (ZHAN, "knows", "身为族长也无法豁免")],
             ["event_013", "event_014", "event_015"], L_SORRY, B[4]),
        fact(F[6], "楚焱的灵魂来自另一个世界，他记得的远不止十五年", "confirmed", "knows", "viewer_leads",
             [(YAN, "knows", "只有他自己知道"), (ZHAN, "unaware", "只当儿子早熟")],
             ["event_001"], L_SECRET, B[1]),
        fact(F[7], "楚焱的灵魂远强于常人，天赋从未减弱", "confirmed", "knows", "viewer_leads",
             [(YAN, "knows", "自知天赋仍在"), (ZHAN, "unaware", "只知儿子修炼停滞")],
             ["event_006"], L_SOUL, B[2]),
        fact(F[8], "进入楚焱体内的战之气会无一例外地干净消失，原因无人知晓", "confirmed", "knows",
             "simultaneous_reveal",
             [(YAN, "knows", "查不出任何异常"), (ZHAN, "knows", "同样一无所知")],
             ["event_008"], L_GONE, B[3]),
        fact(F[9], "母亲遗留的黑色古戒会自行发出微弱毫光", "potential", "knows", "viewer_leads",
             [(YAN, "unaware", "只当是母亲的普通遗物")],
             ["event_009", "event_018"], L_GLOW, B[5]),
        fact(F[10], "族中长老正等着族长犯错，楚战的地位并不稳固", "confirmed", "knows", "withheld",
             [(ZHAN, "knows", "深知处境"), (YAN, "suspects", "第一次听父亲说破")],
             ["event_015"], L_ELDERS, B[4]),
        fact(F[11], "明日家族将有贵客到访，身份未明", "confirmed", "knows", "withheld",
             [(ZHAN, "knows", "故意卖关子"), (YAN, "unaware", "被勾起好奇")],
             ["event_017"], L_GUEST, B[4]),
    ],
    "character_state_deltas": [
        delta(YAN, ["event_001"], {"emotional_state": "白日受辱后的疲惫"},
              {"emotional_state": "独处时坦露的荒诞与孤独"}, L_FIFTEEN,
              "独自躺在崖顶望月", "动作松弛，语速放慢"),
        delta(YAN, ["event_005"], {"confidence_state": "白日强压的平静"},
              {"confidence_state": "对着夜空爆发的不甘"}, L_ROAR,
              "跳起身仰头嘶吼", "从静到爆的落差"),
        delta(YAN, ["event_006"], {"power_level": "外人眼中的三段废物"},
              {"power_level": "灵魂远强于常人却无处施展"}, L_SOUL,
              "喘息中眼神转亮又暗", "自嘲的骄傲"),
        delta(YAN, ["event_007"], {"social_status": "昔日名动一方的天才"},
              {"social_status": "路人口中嘲笑的废物"}, L_LOST,
              "崖边肩线松垮", "垂头，声音低下去"),
        delta(YAN, ["event_008"], {"power_level": "只知战之气流失"},
              {"power_level": "确认吸收更快却消失更彻底"}, L_GONE,
              "掌心气流被抽空", "凝视手掌时的僵住"),
        delta(YAN, ["event_009"], {"emotional_state": "对命运的愤懑"},
              {"emotional_state": "面对母亲遗物时的愧疚"}, L_MOTHER,
              "拇指摩挲戒面", "语气放软，苦笑"),
        delta(YAN, ["event_010", "event_011"], {"relationship_state": "独自消化白日的羞辱"},
              {"relationship_state": "在父亲面前卸下防备"}, L_FATHER_HERE,
              "回头时笑意先到", "笑容比白日真实"),
        delta(ZHAN, ["event_011", "event_012"], {"emotional_state": "族长的威严从容"},
              {"emotional_state": "面对儿子时的关切与欲言又止"}, L_LAUGH,
              "跃出树林时笑意先到", "威严身形配上柔和语气"),
        delta(YAN, ["event_013", "event_014"], {"social_status": "族长之子，尚在核心修炼圈"},
              {"social_status": "一年为限，随时可能被分配去打理家族杂务"}, L_ONEYEAR,
              "听到期限时手掌收紧", "表面平静，指节发白"),
        delta(ZHAN, ["event_015"], {"relationship_state": "百般宠爱、落魄后有增无减"},
              {"relationship_state": "被迫亲口说出族规判决"}, L_SORRY,
              "抬手又放下最终只是叹息", "语速放缓，视线躲闪"),
        delta(YAN, ["event_016", "event_017"], {"relationship_state": "被父亲宠爱与保护"},
              {"relationship_state": "反过来安慰父亲的懂事"}, L_PROMISE,
              "转向父亲露出很稳的笑", "笑着的嘴与不笑的眼"),
        delta(YAN, ["event_018"], {"confidence_state": "自知一年四段几无可能"},
              {"confidence_state": "仍独自扛下这份承诺"}, L_TRY,
              "摩挲戒指仰头望星空", "声音低而稳"),
    ],
}

DRAMATURGY = {
    "genre_engine": "status-power-mystery",
    "dramatic_question": "一年，四段战之气——这个连他自己都不信的承诺，他要怎么兑现？",
    "cold_open": "夜色崖顶，父亲的声音落下判决：一年内达不到七段，就被分配出核心修炼圈。",
    "cold_open_source_quote": L_YES,
    "status_before": "楚焱在白日的测验中再次被判三段低级，夜里独自上崖排解。",
    "status_after": "倒计时被父亲亲口坐实，而母亲遗留的古戒在他抬头那一刻悄然发光。",
    "conflict_beats": ["崖顶独白揭出穿越秘密", "灵魂强大却做不成战者的荒谬",
                       "战之气进去多少消失多少的谜团", "父亲被迫说出一年七段的判决",
                       "少年反过来安慰父亲却自知无望"],
    "reveal_order": ["一年七段的判决", "十五年的自喃", "他不是这个世界的人", "灵魂强于常人",
                     "战之气凭空消失", "母亲的古戒", "判决完整兑现", "古戒发光"],
    "cliffhanger": "夺走他战之气的东西，可能一直戴在他自己手上。",
    "narration_budget_ratio": 0.2,
}

TITLE_TEXT = "战气大陆"
HOOK = "一年之内练到七段，否则滚去打理家族杂务——说这话的，是他自己的父亲。"
SUMMARY = "夜里楚焱独上山崖，吐露只有他自己知道的秘密：他的灵魂来自另一个世界，记得的远不止十五年。灵魂强于常人，吸收战之气比巅峰时还快，可进去多少就消失多少。父亲楚战寻来，亲口定下一年七段的期限，否则将被分配出核心修炼圈。少年笑着承诺，抬头的一刹，母亲留下的黑色古戒无声亮起一抹毫光。"
PREVIEW = "父亲口中的贵客究竟是谁？那枚古戒又藏着什么？"
EXTERNALIZED = {"event_006", "event_007", "event_008", "event_014", "event_016"}
REMOVED = {"event_002", "event_003", "event_004"}
