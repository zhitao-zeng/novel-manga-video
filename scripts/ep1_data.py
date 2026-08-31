"""Episode 1 -- 陨落的天才.  Ported from the validated ceiling plan, remapped
onto the 19-event diagnosis of the three-chapter novel."""
from build_s1 import (
    B, F, YAN, MEI, XUN, TESTER, ShotBuilder, audio, beat, cam, delta, fact, perf, turn,
)

# ------------------------------------------------------------ source lines
L_RESULT = '"战之力，三段！"'
L_STONE = "望着测验灵碑上面闪亮得甚至有些刺眼的五个大字，少年面无表情。"
L_ANNOUNCE = '"楚焱，战之力，三段！级别：低级！"'
L_MOCK1 = "\"三段？嘿嘿，果然不出我所料，这个'天才'这一年又是在原地踏步！\""
L_MOCK2 = '"要不是族长是他的父亲，这种废物，早就被驱赶出家族，任其自生自灭了，哪还有机会待在家族中白吃白喝。"'
L_INNER = '"这些人，都如此刻薄势利吗？或许是因为三年前他们曾经在自己面前露出过最谦卑的笑容，所以，如今想要讨还回去吧……"'
L_LONELY = "孤单的身影，与周围的世界，有些格格不入。"
L_NEXT_MEI = '"下一个，楚媚！"'
L_MEI7 = '"楚媚，战之气：七段！级别：高级！"'
L_YE = '"耶！"听着测验员所喊出的成绩，少女脸颊扬起了得意的笑容。'
L_PRAISE = '"啧啧，七段战之气，真了不起。按这进度，恐怕顶多只需要三年时间，她就能称为一名真正的战者了吧……"'
L_MEI_CUT = "皱眉思虑了瞬间，楚媚还是打消了过去的念头。现在的两人，已经不在同一个阶层之上。"
L_MEI_PAST = '"唉……"莫名地轻叹了一口气，楚媚脑中忽然浮现出三年前那意气风发的少年。四岁练气，十岁拥有九段战之气，十一岁突破十段战之气，成功凝聚战之气旋，一跃成为家族百年之内最年轻的战者！'
L_FALL = "然而天才的道路，貌似总是曲折的。三年之前，这名声望达到巅峰的天才少年，却是突兀地接受到了有生以来最残酷的打击。"
L_VANISH = "不仅辛辛苦苦修炼十数载方才凝聚的战之气旋，一夜之间，化为乌有，而且体内的战之气，也是随着时间的流逝，变得诡异地越来越少。"
L_HIGHFALL = "站得越高，摔得越狠。这次的跌落，或许就再也没有爬起的机会。"
L_NEXT_XUN = '"下一个，楚烟儿！"'
L_XUN9 = '"战之气：九段！级别：高级！"'
L_AWE = '"……竟然到九段了，真是恐怖！家族中年轻一辈的第一人，恐怕非烟儿小姐莫属了。"'
L_SECOND = '"烟儿小姐，半年之后，你应该便能凝聚战气之旋。如果你成功的话，那么以十四岁年龄成为一名真正的战者，你是楚家百年内的第二人！"'
L_FIRST = "是的，第二人。那位第一人，便是褪去了天才光环的楚焱。"
L_THANKS = '"谢谢。"'
L_BROTHER = '"楚焱哥哥。"在经过少年身旁时，少女顿下了脚步，对着楚焱恭敬地弯了弯腰'
L_QUALIFY = '"我现在还有资格让你这么叫么？"'
L_TEACH = '"楚焱哥哥，以前你曾经与烟儿说过，要能放下，才能拿起，提放自如，是自在人！"'
L_FREEMAN = '"呵呵，自在人？我也只会说而已。你看我现在的模样，像自在人吗？而且……这世界，本来就不属于我。"'
L_BELIEVE = '"楚焱哥哥，虽然并不知道你究竟是怎么回事，不过，烟儿相信，你会重新站起来，取回属于你的荣耀与尊严……"'
L_CHARM = '"当年的楚焱哥哥，的确很吸引人……"'
L_AWKWARD = '"呵呵……"面对着少女毫不掩饰的坦率话语，少年尴尬地笑了一声，可却未再说什么。'
L_FOLLOW = "站在原地望着少年那恍如与世隔绝的孤独背影，楚烟儿踌躇了一会，然后在身后一干嫉妒的狼嚎声中，快步追了上去，与少年并肩而行。"

LINES = [v for k, v in list(globals().items()) if k.startswith("L_")]
AXIS = "广场主轴，测验灵碑一侧"
DIR = "楚焱恒居画面左侧，灵碑与测验员居右"


def build(location):
    b = ShotBuilder(location)
    b.axis, b.direction = AXIS, DIR
    A = b.add

    # ---------------------------------------------------- B1 hook
    A(beat=B[0], func="establish", strategy="story-keyframe",
      narration="测验灵碑爆出刺眼光芒，映亮仰望的人群。", quote=L_SECOND, events=["event_012", "event_013"],
      turns=[turn(TESTER, "烟儿小姐，半年之后，你应该便能凝聚战气之旋。如果你成功的话，那么以十四岁年龄成为一名真正的战者，你是楚家百年内的第二人！",
                  L_SECOND, mode="offscreen_dialogue", derivation="verbatim", emotion="罕见的赞许")],
      visual="夜色广场，漆黑测验灵碑爆出刺眼光芒直冲天际，人群仰望的背影剪影，不露测碑者正脸",
      motion="光芒渐盛，人群骚动前的静止一瞬",
      characters=[], scale="全景", power="家族权威当众加冕未露面的天才",
      emo="震撼与好奇", focus="灵碑光芒与第二人三个字", facts=[F[3], F[1]],
      kf=["开场权力宣告", "隐藏主角悬念构图"],
      audio_plan=audio(B[0], [(0.0, "ambience", "广场嘈杂", "开场"),
                              (0.35, "duck", "宣告开口人声压底", "测验员开口"),
                              (0.85, "impact", "低频一记落在第二人三字", "宣告落字")],
                       ambience="广场嘈杂", energy=0.6))
    A(beat=B[0], func="withhold", strategy="direct-assets",
      narration="前排少年下意识接话。", quote=L_FIRST, events=["event_013"],
      turns=[turn("少年甲", "第二人？……那第一人是谁？", L_FIRST,
                  mode="offscreen_dialogue", derivation="derived", emotion="疑惑")],
      visual="人群前排剪影侧脸，疑惑回头交换眼神", motion="轻微回头，交换眼神",
      characters=[], scale="中景", power="知情众人对不知情观众", emo="悬念点燃",
      focus="那个没人接的问题", facts=[F[1]])
    A(beat=B[0], func="withhold", strategy="story-keyframe",
      narration="全场视线越过人群，落到队尾低头的身影。", quote=L_FIRST, events=["event_013"],
      turns=[turn("少年乙", "别问了。", L_FIRST,
                  mode="offscreen_dialogue", derivation="derived", emotion="讳莫如深")],
      visual="人群目光汇聚成一条线，尽头是队伍最后一排低头的瘦削少年，不见其正脸",
      motion="众人视线转移，少年始终低头未动",
      characters=[YAN], scale="全景", power="全场注视与一人沉默", emo="悬念压住",
      focus="队尾那道低头的背影", facts=[F[1]],
      camera=cam("人群中景", AXIS, DIR, motivation="全场视线转移带来的视点变化",
                 trajectory="缓慢横摇至队尾", end="队尾背影全景"),
      kf=["多人视线关系", "主角隐藏式亮相"])

    # ---------------------------------------------------- B2 question
    A(beat=B[1], func="transition", strategy="scene-only",
      narration="回到一刻钟前，灵碑前测验进行中。", quote=L_STONE, events=["event_001"],
      turns=[turn("旁白", "一刻钟前。", L_STONE, mode="narration", derivation="derived", emotion="平静")],
      visual="测验灵碑光芒暗下去，广场恢复日常测验的队列与嘈杂", motion="光芒收敛，人流恢复",
      characters=[], scale="全景", power="时间回溯", emo="谜面挂起", focus="灵碑明暗变化")
    A(beat=B[1], func="establish", strategy="story-keyframe",
      narration="灵碑亮出五个大字，测验员漠然公布。", quote=L_ANNOUNCE, events=["event_001", "event_002"],
      turns=[turn(TESTER, "楚焱，战之力，三段！级别：低级！", L_ANNOUNCE,
                  mode="offscreen_dialogue", derivation="verbatim", emotion="漠然")],
      visual="测验灵碑亮起刺眼大字，前景是楚焱僵直的背影，碑旁中年测验员侧影",
      motion="碑文亮起，人群开始骚动",
      characters=[YAN], scale="中景", power="制度对个人的宣判", emo="难堪落差",
      facts=[F[0]], focus="低级二字与楚焱背影",
      kf=["成绩宣判关键信息", "碑文与人物同框"])
    A(beat=B[1], func="reaction", strategy="direct-assets",
      narration="指甲深深刺进掌心。", quote=L_STONE, events=["event_001"],
      turns=[turn(YAN, "又是三段。", L_STONE, mode="inner_voice", derivation="derived", emotion="自嘲隐痛")],
      visual="楚焱面无表情的特写，垂在身侧紧握的拳，指节发白", motion="拳缓缓收紧，呼吸一滞",
      characters=[YAN], scale="近景", power="自我压制", emo="心疼主角",
      facts=[F[0]], focus="握紧的拳与木然的脸",
      audio_plan=audio(B[1], [(0.0, "silence", "环境声被抽离一瞬", "内心声进入"),
                              (0.7, "heartbeat", "低心跳一拍", "指甲刺入掌心")],
                       ambience="近乎无声", energy=0.3))
    A(beat=B[1], func="pressure", strategy="direct-assets",
      narration="嘲讽从人群里炸开。", quote=L_MOCK1, events=["event_002", "event_003"],
      turns=[turn("少年乙", "三段？嘿嘿，果然不出我所料，这个'天才'这一年又是在原地踏步！",
                  L_MOCK1, mode="offscreen_dialogue", derivation="verbatim", emotion="讥笑")],
      visual="楚焱孤立于画面中央，四周人群虚化攒动", motion="人群窃笑攒动，楚焱不动",
      characters=[YAN], scale="中景", power="群体对个体的围剿", emo="愤懑", focus="被笑声包围的楚焱")
    A(beat=B[1], func="pressure", strategy="direct-assets",
      narration="更狠的话跟着落下。", quote=L_MOCK2, events=["event_003"],
      turns=[turn("少年丙", "要不是族长是他的父亲，这种废物，早就被驱赶出家族，任其自生自灭了",
                  L_MOCK2, mode="offscreen_dialogue", derivation="verbatim", emotion="刻薄")],
      visual="楚焱侧脸特写，睫毛低垂，背景笑声中的嘴脸虚化", motion="楚焱眼睫微颤，喉结滚动",
      characters=[YAN], scale="近景", power="踩到父辈关系的羞辱", emo="替他攥拳", focus="楚焱压住情绪的侧脸")
    A(beat=B[1], func="reaction", strategy="direct-assets",
      narration="他缓缓抬头，扫过那些同龄人的脸。", quote=L_INNER, events=["event_004"],
      turns=[turn(YAN, "这些人，都如此刻薄势利吗？", L_INNER, mode="inner_voice", derivation="verbatim", emotion="苦涩"),
             turn(YAN, "或许是因为三年前他们曾经在自己面前露出过最谦卑的笑容", L_INNER,
                  mode="inner_voice", derivation="verbatim", emotion="更苦涩")],
      visual="楚焱抬起头，漆黑眸子木然扫过嘲讽的人群，嘴角自嘲更苦", motion="缓慢抬头，目光横扫，嘴角牵动",
      characters=[YAN], scale="中近景", power="看透而无力", emo="世态炎凉", focus="木然扫视的眼神")
    A(beat=B[1], func="transition", strategy="story-keyframe",
      narration="他转身走向队伍最后一排，人群自动让开。", quote=L_LONELY, events=["event_005"],
      turns=[turn("旁白", "孤单的身影，与周围的世界，有些格格不入。", L_LONELY,
                  mode="narration", derivation="verbatim", emotion="怅然")],
      visual="楚焱背影穿过让开的人群走向队尾，光影把他与人群割开", motion="人群左右分开，背影渐小",
      characters=[YAN], scale="全景", power="被放逐感", emo="孤独", focus="被人群割开的背影",
      kf=["人群与主角的空间关系"])

    # ---------------------------------------------------- B3 pressure
    A(beat=B[2], func="advance", strategy="direct-assets",
      narration="测验继续。", quote=L_NEXT_MEI, events=["event_006"],
      turns=[turn(TESTER, "下一个，楚媚！", L_NEXT_MEI, mode="offscreen_dialogue", derivation="verbatim", emotion="例行公事")],
      visual="人群骚动中让出通道，少女快步跑出", motion="人群让开，少女出列",
      characters=[MEI], scale="中景", power="流程推进", emo="节奏松一拍", focus="让开的人群与出列的少女")
    A(beat=B[2], func="advance", strategy="direct-assets",
      narration="少女触碑，光芒亮起。", quote=L_MEI7, events=["event_007"],
      turns=[turn(TESTER, "楚媚，战之气：七段！级别：高级！", L_MEI7,
                  mode="offscreen_dialogue", derivation="verbatim", emotion="平直")],
      visual="楚媚小手按上漆黑灵碑，光芒再亮", motion="按碑闭眼，光起",
      characters=[MEI], scale="中景", power="新星登场", emo="对比开始", focus="七段高级四个字")
    A(beat=B[2], func="pressure", strategy="direct-assets",
      narration="得意的笑容与羡慕声一起扬起。", quote=L_YE, events=["event_007", "event_008"],
      turns=[turn(MEI, "耶！", L_YE, mode="visible_dialogue", derivation="verbatim", emotion="得意"),
             turn("族人甲", "七段战之气，真了不起", L_PRAISE, mode="offscreen_dialogue", derivation="verbatim", emotion="羡慕")],
      visual="楚媚扬起得意笑容，四周投来火热羡慕目光", motion="踮脚欢呼收势，环视人群",
      characters=[MEI], scale="中近景", power="众星捧月", emo="与楚焱处境对撞", focus="得意的笑")
    A(beat=B[2], func="withhold", strategy="direct-assets",
      narration="她的视线穿过人群停在那道身影上，随即收回。", quote=L_MEI_CUT, events=["event_008"],
      turns=[turn("姐妹", "看谁呢？", L_MEI_CUT, mode="offscreen_dialogue", derivation="derived", emotion="好奇"),
             turn(MEI, "……没什么。", L_MEI_CUT, mode="visible_dialogue", derivation="derived", emotion="别扭掩饰")],
      visual="楚媚笑容微滞，目光越过人群落在队尾背影，又若无其事收回", motion="视线远投，睫毛一颤，转头掩饰",
      characters=[MEI], scale="近景", power="旧identity与新阶层的拉扯", emo="微妙怅然",
      camera=cam("近景", AXIS, DIR, motivation="人物情绪转折", trajectory="极缓推近半步", end="更近的近景"),
      focus="收回视线前的迟疑")

    # ---------------------------------------------------- B4 escalation
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="姐妹顺着她刚才的视线看过去，压低声音。", quote=L_MEI_PAST, events=["event_009"],
      turns=[turn("姐妹", "他以前，真有传的那么厉害？", L_MEI_PAST,
                  mode="offscreen_dialogue", derivation="derived", emotion="好奇压低")],
      visual="两名少女交头低语，远处队尾背影在景深尽头", motion="凑近低语，目光偷瞟队尾",
      characters=[MEI], scale="中近景", power="流言与真相", emo="观众替角色问出问题",
      facts=[F[1]], focus="低声的试探")
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="楚媚沉默了一下，才开口。", quote=L_MEI_PAST, events=["event_009"],
      turns=[turn(MEI, "四岁练气，十岁拥有九段战之气。", L_MEI_PAST, mode="visible_dialogue", derivation="derived", emotion="追忆"),
             turn(MEI, "十一岁凝聚战之气旋，家族百年之内最年轻的战者。", L_MEI_PAST,
                  mode="visible_dialogue", derivation="derived", emotion="轻叹")],
      visual="楚媚望着队尾方向出神，眼里映着三年前的光", motion="出神，指尖无意识绞衣角",
      characters=[MEI], scale="近景", power="辉煌旧事对照眼前落魄", emo="唏嘘",
      facts=[F[1]], focus="说出履历时的出神")
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="那后来呢。", quote=L_FALL, events=["event_010"],
      turns=[turn("姐妹", "那后来呢？", L_FALL, mode="offscreen_dialogue", derivation="derived", emotion="追问"),
             turn(MEI, "三年前，一夜之间，战之气旋化为乌有。", L_VANISH, mode="visible_dialogue", derivation="derived", emotion="低沉")],
      visual="楚媚眼神黯下来，背景人声退远", motion="眼神一黯，摇头",
      characters=[MEI], scale="近景", power="命运翻脸", emo="惋惜", facts=[F[2]], focus="一夜之间四个字")
    A(beat=B[3], func="reveal", strategy="direct-assets",
      narration="为什么。", quote=L_VANISH, events=["event_010"],
      turns=[turn("姐妹", "为什么？", L_VANISH, mode="offscreen_dialogue", derivation="derived", emotion="不解"),
             turn(MEI, "没人知道。", L_VANISH, mode="visible_dialogue", derivation="derived", emotion="讳莫如深")],
      visual="楚媚摇头的近景，两人不约而同望向队尾", motion="摇头，双双望向远处",
      characters=[MEI], scale="近景", power="全员共享的无知", emo="悬念定格",
      facts=[F[2]], focus="没人知道之后的静默",
      audio_plan=audio(B[3], [(0.0, "ambience", "人群远噪", "对话进行"),
                              (0.4, "duck", "问句压低环境", "为什么出口"),
                              (0.75, "silence", "全静一拍", "没人知道落地")],
                       ambience="人群远噪", energy=0.35))
    A(beat=B[3], func="reaction", strategy="direct-assets",
      narration="她轻叹一声收住话头。", quote=L_HIGHFALL, events=["event_010"],
      turns=[turn(MEI, "站得越高，摔得越狠", L_HIGHFALL, mode="visible_dialogue", derivation="verbatim", emotion="怅然")],
      visual="楚媚收回目光望向灵碑方向，侧脸怅然", motion="轻叹，转回头",
      characters=[MEI], scale="中近景", power="旁观者的注脚", emo="命运无常",
      facts=[F[2]], focus="怅然侧脸",
      audio_plan=audio(B[3], [(0.0, "ambience", "人群远噪回升", "话头收住"),
                              (0.8, "sfx", "一声几不可闻的轻叹", "台词收尾")],
                       ambience="人群远噪", energy=0.4))

    # ---------------------------------------------------- B5 payoff
    A(beat=B[4], func="advance", strategy="direct-assets",
      narration="喊声再起，人群忽然安静下来。", quote=L_NEXT_XUN, events=["event_011"],
      turns=[turn(TESTER, "下一个，楚烟儿！", L_NEXT_XUN, mode="offscreen_dialogue", derivation="verbatim", emotion="例行中带一分期待")],
      visual="喧闹广场骤然安静，所有视线转移", motion="全场齐齐转头",
      characters=[], scale="中景", power="名字本身的分量", emo="屏息", focus="骤静的人群",
      audio_plan=audio(B[4], [(0.0, "ambience", "喧闹", "喊名前"), (0.5, "silence", "骤静", "名字出口")],
                       ambience="骤然安静", energy=0.45))
    A(beat=B[4], func="reveal", strategy="story-keyframe",
      narration="紫裙少女轻触灵碑，光芒绽放，全场寂静。", quote=L_XUN9, events=["event_012"],
      turns=[turn(TESTER, "战之气：九段！级别：高级！", L_XUN9, mode="offscreen_dialogue", derivation="verbatim", emotion="罕见提声")],
      visual="紫裙少女雪白皓腕轻触漆黑灵碑，刺眼光芒绽放照亮全场寂静的脸",
      motion="紫袖滑落，光芒暴涨",
      characters=[XUN], scale="中景", power="绝对天赋的碾压", emo="惊艳",
      facts=[F[3]], focus="九段高级与紫裙剪影",
      kf=["高潮成绩揭示", "人物与灵碑光效同框"])
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="敬畏在寂静后炸开。", quote=L_AWE, events=["event_012"],
      turns=[turn("少年甲", "……竟然到九段了，真是恐怖！家族中年轻一辈的第一人，恐怕非烟儿小姐莫属了。",
                  L_AWE, mode="offscreen_dialogue", derivation="verbatim", emotion="敬畏")],
      visual="周围少年咽唾沫的群像，眼神敬畏，楚媚在人群中神色一黯", motion="咽唾沫，交头接耳",
      characters=[MEI], scale="中景", power="新第一人的加冕舆论", emo="声势铺垫",
      facts=[F[3]], focus="敬畏的群像与楚媚的黯然")
    A(beat=B[4], func="payoff", strategy="direct-assets",
      narration="测验员罕见地露出笑意——冷开场在此兑现。", quote=L_SECOND, events=["event_013"],
      turns=[turn(TESTER, "烟儿小姐，半年之后，你应该便能凝聚战气之旋。如果你成功的话，那么以十四岁年龄成为一名真正的战者，你是楚家百年内的第二人！",
                  L_SECOND, mode="offscreen_dialogue", derivation="verbatim", emotion="罕见的赞许")],
      visual="紫裙少女立于灵碑前，碑光映脸，周围众人屏息聆听宣告", motion="少女微微颔首，众人凝神",
      characters=[XUN], scale="中近景", power="权威亲自盖章", emo="冷开场回收的爽点",
      facts=[F[3], F[1]], focus="宣告落下时少女的平静",
      camera=cam("中景", AXIS, DIR, motivation="权力宣告的强调", trajectory="极缓推近", end="中近景"))
    A(beat=B[4], func="reveal", strategy="story-keyframe",
      narration="那个问题再次问出。这次，楚媚顺着自己的目光望向队尾。", quote=L_FIRST, events=["event_013"],
      turns=[turn("少年甲", "那……第一人是谁？", L_FIRST, mode="offscreen_dialogue", derivation="derived", emotion="迟疑"),
             turn(MEI, "……就在那儿。", L_FIRST, mode="visible_dialogue", derivation="derived", emotion="复杂")],
      visual="楚媚望向队尾的视线引导构图，景深尽头是低头的楚焱，人群目光随之汇聚",
      motion="楚媚偏头示意，众人目光汇聚队尾",
      characters=[MEI, YAN], scale="全景", power="悬念由角色亲口收束", emo="谜底揭晓",
      facts=[F[1]], focus="视线尽头的楚焱",
      kf=["多人视线汇聚的关键构图", "第一人身份揭晓"])
    A(beat=B[4], func="advance", strategy="story-keyframe",
      narration="烟儿平淡致谢，转身，在炽热注目中走向队尾。", quote=L_THANKS, events=["event_014"],
      turns=[turn(XUN, "谢谢。", L_THANKS, mode="visible_dialogue", derivation="verbatim", emotion="平淡")],
      visual="紫裙少女平静点头致谢转身，穿过炽热注目走向队伍末尾的颓废少年",
      motion="点头，转身，莲步穿过人群",
      characters=[XUN], scale="全景", power="无视全场期待的走向", emo="意外",
      focus="逆着期待走向队尾的紫裙身影",
      camera=cam("灵碑前中景", AXIS, DIR, motivation="人物明确位移", trajectory="平稳跟移", end="队尾方向全景"),
      kf=["人物位移与人群关系"])
    A(beat=B[4], func="reveal", strategy="story-keyframe",
      narration="她在少年身旁停下，恭敬地弯了弯腰。", quote=L_BROTHER, events=["event_015"],
      turns=[turn(XUN, "楚焱哥哥。", L_BROTHER, mode="visible_dialogue", derivation="verbatim", emotion="清雅恭敬")],
      visual="封面级构图：紫裙少女对颓废少年恭敬弯腰执礼，周围一圈错愕嫉妒的脸",
      motion="顿步，弯腰，抬眼一笑",
      characters=[XUN, YAN], scale="中景", power="全场第一人向弃子执礼", emo="全集最大反差",
      facts=[F[4]], focus="弯腰执礼的瞬间",
      kf=["权力反转关键构图", "封面候选"])
    A(beat=B[4], func="payoff", strategy="direct-assets",
      narration="楚焱苦涩开口。", quote=L_QUALIFY, events=["event_015"],
      turns=[turn(YAN, "我现在还有资格让你这么叫么？", L_QUALIFY, mode="visible_dialogue", derivation="verbatim", emotion="苦涩")],
      visual="楚焱抬起头，苦涩地看着眼前明珠般的少女", motion="抬头，苦笑",
      characters=[YAN], scale="近景", power="自我放逐者面对旧敬意", emo="心酸",
      facts=[F[4]], focus="苦涩的眼神")
    A(beat=B[4], func="payoff", strategy="direct-assets",
      narration="她引用他当年的话回敬。", quote=L_TEACH, events=["event_016"],
      turns=[turn(XUN, "楚焱哥哥，以前你曾经与烟儿说过，要能放下，才能拿起，提放自如，是自在人！",
                  L_TEACH, mode="visible_dialogue", derivation="verbatim", emotion="柔而坚定")],
      visual="烟儿微笑柔声，眼神认真笃定", motion="直视，微笑，语气一字一句",
      characters=[XUN], scale="近景", power="拿他自己的话唤他", emo="暖意",
      facts=[F[4]], focus="笃定的眼神")
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="他自嘲一笑。", quote=L_FREEMAN, events=["event_017"],
      turns=[turn(YAN, "呵呵，自在人？我也只会说而已。你看我现在的模样，像自在人吗？", L_FREEMAN,
                  mode="visible_dialogue", derivation="verbatim", emotion="自嘲"),
             turn(YAN, "而且……这世界，本来就不属于我。", L_FREEMAN,
                  mode="visible_dialogue", derivation="verbatim", emotion="意兴阑珊")],
      visual="楚焱自嘲一笑摊开手，意兴阑珊", motion="摊手，笑意冷下去",
      characters=[YAN], scale="中近景", power="自弃对峙善意", emo="无力感", focus="冷下去的笑")
    A(beat=B[4], func="payoff", strategy="direct-assets",
      narration="她皱了皱眉，认真地说完，俏脸第一次泛红。", quote=L_BELIEVE, events=["event_018"],
      turns=[turn(XUN, "烟儿相信，你会重新站起来，取回属于你的荣耀与尊严……", L_BELIEVE,
                  mode="visible_dialogue", derivation="verbatim", emotion="认真"),
             turn(XUN, "当年的楚焱哥哥，的确很吸引人……", L_CHARM,
                  mode="visible_dialogue", derivation="verbatim", emotion="绯红坦率")],
      visual="烟儿眉头微蹙认真说完，白皙俏脸浮起淡淡绯红", motion="蹙眉，语毕微顿，脸颊泛红偏头",
      characters=[XUN], scale="近景", power="信任的公开告白", emo="心动",
      facts=[F[4]], focus="头一次的绯红",
      audio_plan=audio(B[4], [(0.0, "ambience", "周遭议论压低", "对话继续"),
                              (0.6, "music_rise", "柔和主题第一次浮现", "绯红一句进入")],
                       ambience="周遭议论压低", energy=0.5))
    A(beat=B[4], func="reaction", strategy="direct-assets",
      narration="他尴尬一笑，没再说什么，落寞转身向广场外走去。", quote=L_AWKWARD, events=["event_017", "event_019"],
      turns=[turn(YAN, "呵呵……", L_AWKWARD, mode="visible_dialogue", derivation="verbatim", emotion="尴尬")],
      visual="楚焱尴尬一笑，回转身，对着广场外缓缓行去", motion="笑一声，转身，步子缓慢",
      characters=[YAN], scale="中景", power="逃离善意", emo="怅惘", focus="转身离去的背影")

    # ---------------------------------------------------- B6 cliffhanger
    A(beat=B[5], func="cliffhanger", strategy="story-keyframe",
      narration="她望着那道孤独背影踌躇片刻，身后响起嫉妒的声浪。", quote=L_FOLLOW, events=["event_019"],
      turns=[turn("少年乙", "她怎么追上去了？！", L_FOLLOW, mode="offscreen_dialogue", derivation="derived", emotion="嫉妒错愕")],
      visual="烟儿立在原地望着广场外的孤独背影，身后人群骚动错愕",
      motion="踌躇半步，裙裾一动，快步追出",
      characters=[XUN], scale="全景", power="她当众选择了他", emo="哗然",
      facts=[F[4]], focus="迈出的那一步", kf=["集尾抉择瞬间"],
      audio_plan=audio(B[5], [(0.0, "sfx", "人群嫉妒的怪叫渐起", "烟儿迈步"),
                              (0.7, "duck", "怪叫压低", "画外惊呼")],
                       ambience="骚动", sfx=["嫉妒的狼嚎声浪"], energy=0.55))
    A(beat=B[5], func="cliffhanger", strategy="story-keyframe",
      narration="两道身影并肩而行，渐行渐远。她一句话也没有说。", quote=L_FOLLOW, events=["event_019"],
      turns=[turn("旁白", "她什么都没说，只是追了上去，与他并肩而行。", L_FOLLOW,
                  mode="narration", derivation="derived", emotion="留白")],
      visual="夕照广场，两道一紫一黑的身影并肩渐远，人群的喧嚣被甩在身后",
      motion="并肩步伐渐同频，镜头缓缓后拉",
      characters=[XUN, YAN], scale="全景", power="并肩无言胜过全场喧嚣", emo="怅然与期待",
      facts=[F[4]], focus="两道背影之间的距离",
      camera=cam("双人中景", AXIS, DIR, motivation="结尾空间揭示", trajectory="缓慢后拉", end="广场大全景"),
      kf=["集尾定格构图", "双人精确站位"],
      audio_plan=audio(B[5], [(0.0, "sfx", "狼嚎渐弱", "二人走远"),
                              (0.3, "music_rise", "主题旋律完整进入", "并肩同步"),
                              (0.8, "duck", "旁白最后一句压乐", "旁白进入"),
                              (1.0, "release", "留两秒纯音乐", "旁白结束")],
                       ambience="渐远的骚动", sfx=["渐弱的狼嚎"], energy=0.5))
    return b.shots


SHOWRUNNER = {
    "planning_mode": "planner",
    "retention": {
        "target_duration_seconds": 120.0, "max_attention_gap_ratio": 0.25,
        "beats": [
            beat(B[0], "hook", 0.0, 0.05, "百年第二人？那第一人是谁？", "一个被雪藏的名字",
                 [F[3], F[1]], ["event_012", "event_013"], "震撼转好奇", [1, 2, 3], L_SECOND),
            beat(B[1], "question", 0.05, 0.24, "三段低级的废物怎么会是第一人？", "落差必有来历",
                 [F[0]], ["event_001", "event_002", "event_003", "event_004", "event_005"],
                 "难堪转愤懑", [4, 5, 6, 7, 8, 9, 10], L_ANNOUNCE),
            beat(B[2], "pressure", 0.24, 0.42, "连旧识都划清界限，他还剩什么？", "孤立见底",
                 [], ["event_006", "event_007", "event_008"], "对比之痛", [11, 12, 13, 14], L_MEI7),
            beat(B[3], "escalation", 0.42, 0.62, "天才为何一夜跌落？", "答案是没人知道——更大的谜",
                 [F[1], F[2]], ["event_009", "event_010"], "唏嘘转悬念", [15, 16, 17, 18, 19], L_FALL),
            beat(B[4], "payoff", 0.62, 0.86, "第二人宣布时，第一人在哪里？", "冷开场兑现，且有人始终敬他",
                 [F[3], F[1], F[4]],
                 ["event_011", "event_012", "event_013", "event_014", "event_015", "event_016",
                  "event_017", "event_018"],
                 "惊艳转心动", [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31], L_SECOND),
            beat(B[5], "cliffhanger", 0.86, 1.0, "她为什么？他们究竟什么关系？", "沉默并肩，下集揭",
                 [F[4]], ["event_019"], "哗然转期待", [32, 33], L_FOLLOW),
        ],
        "ending_open_loop": "楚烟儿为何始终敬他？失去的战之气旋究竟怎么回事？",
    },
    "information_states": [
        fact(F[0], "楚焱当前测验结果为战之力三段、级别低级", "confirmed", "knows", "simultaneous_reveal",
             [(YAN, "knows", ""), (MEI, "knows", "")], ["event_001", "event_002"], L_ANNOUNCE, B[1]),
        fact(F[1], "楚家百年内最年轻的战者（第一人）就是楚焱", "confirmed", "suspects", "withheld",
             [(YAN, "knows", ""), (MEI, "knows", "旧日辉煌已成笑谈"), (XUN, "knows", "")],
             ["event_009", "event_013"], L_FIRST, B[4]),
        fact(F[2], "三年前楚焱的战之气旋一夜化为乌有且原因无人知晓", "confirmed", "knows", "simultaneous_reveal",
             [(YAN, "knows", "只知其果不知其因"), (MEI, "knows", "只知其果")],
             ["event_010"], L_VANISH, B[3]),
        fact(F[3], "楚烟儿测得九段高级，半年后有望成为战者", "confirmed", "knows", "simultaneous_reveal",
             [(XUN, "knows", ""), (MEI, "knows", "嫉妒与敬畏并存")],
             ["event_012", "event_013"], L_XUN9, B[4]),
        fact(F[4], "楚烟儿依旧敬重楚焱并当众对他执礼追随", "confirmed", "knows", "misunderstanding",
             [(YAN, "suspects", "不敢相信自己仍配得上"), (MEI, "misled", "视作不可理喻之举"),
              (XUN, "knows", "")],
             ["event_015", "event_019"], L_BROTHER, B[4]),
    ],
    "character_state_deltas": [
        delta(YAN, ["event_001", "event_002"], {"emotional_state": "麻木等待宣判"},
              {"emotional_state": "自嘲刺痛强压"}, L_STONE, "面无表情但指节发白", "站姿内收，动作幅度极小"),
        delta(YAN, ["event_004", "event_005"], {"relationship_state": "与族人表面同列"},
              {"relationship_state": "看清凉薄彻底孤立"}, L_LONELY, "独自站到队伍最后一排", "转身缓慢，背影疏离"),
        delta(MEI, ["event_006", "event_007"], {"social_status": "家族普通少女"},
              {"social_status": "七段高级种子选手"}, L_MEI7, "全场瞩目笑容得意", "举止舒展迎向目光"),
        delta(MEI, ["event_008"], {"relationship_state": "对楚焱旧日倾慕"},
              {"relationship_state": "主动划清界限的怅然"}, L_MEI_CUT, "目光停留又收回", "笑容在瞟向队尾时微滞"),
        delta(MEI, ["event_009"], {"emotional_state": "得意与虚荣"},
              {"emotional_state": "被旧日辉煌勾起的唏嘘"}, L_MEI_PAST, "说起履历时出神", "语速放慢，目光失焦"),
        delta(YAN, ["event_010"], {"power_level": "昔日百年最年轻战者"},
              {"power_level": "战之气持续流失不如常人"}, L_VANISH, "旁人敬畏尽失只剩嘲讽", "被议论时不再抬头"),
        delta(XUN, ["event_011", "event_012"], {"social_status": "备受期待的天才少女"},
              {"social_status": "九段高级当众封神"}, L_XUN9, "全场寂静后敬畏包围", "神情平静不为所动"),
        delta(XUN, ["event_013"], {"social_status": "九段高级当众封神"},
              {"social_status": "获官方盖章的百年第二人"}, L_SECOND, "测验员罕见笑意相贺", "颔首致谢即转身"),
        delta(XUN, ["event_014", "event_015"], {"relationship_state": "私下敬重楚焱"},
              {"relationship_state": "当众执礼公开表态"}, L_BROTHER, "众目睽睽下弯腰执礼", "执礼郑重，笑容清雅"),
        delta(YAN, ["event_015", "event_016"], {"confidence_state": "强撑平静"},
              {"confidence_state": "自认无资格被敬称"}, L_QUALIFY, "苦笑取代平静", "语速放缓，视线回避"),
        delta(YAN, ["event_017", "event_018"], {"emotional_state": "死水般自弃"},
              {"emotional_state": "被触动却仍转身离场"}, L_AWKWARD, "尴尬一笑后离场", "脚步迟而不停"),
        delta(XUN, ["event_019"], {"relationship_state": "当众执礼公开表态"},
              {"relationship_state": "不顾嫉妒追上并肩而行"}, L_FOLLOW, "在狼嚎声中快步追出", "追随果断，一言不发"),
    ],
}

DRAMATURGY = {
    "genre_engine": "status-power-mystery",
    "dramatic_question": "被全场当笑柄的废物，凭什么是楚家百年第一人？",
    "cold_open": "灵碑光芒中，测验员当众宣布楚烟儿是楚家百年内的第二人，全场视线却偷偷落向队尾低头的少年。",
    "cold_open_source_quote": L_FIRST,
    "status_before": "楚焱顶着陨落天才之名在测验队列中等待宣判，全场视他为家族笑柄。",
    "status_after": "第一人身份当众揭开，烟儿执礼相随，两人于嫉妒声浪中并肩离场。",
    "conflict_beats": ["三段低级宣判引爆全场嘲讽", "楚媚七段风光并与旧识划界",
                       "一夜陨落之谜被道出却无人知因", "烟儿九段封神反衬第一人处境",
                       "当众执礼与并肩离场引爆嫉妒"],
    "reveal_order": ["三段低级", "众人凉薄", "七段对比", "昔日第一人履历", "一夜化为乌有",
                     "九段封神", "百年第二人", "执礼与并肩"],
    "cliffhanger": "她什么都没说便追了上去——她知道什么？",
    "narration_budget_ratio": 0.2,
}

TITLE_TEXT = "陨落的天才"
HOOK = "百年一遇的天才宣布诞生时，全场都在偷看队伍最后那个低着头的废物。"
SUMMARY = "测验日，楚焱測出三段低级遭全场嘲讽；楚媚七段风光却与他划清界限，并向姐妹道出他一夜陨落之谜；楚烟儿九段封神、被称百年第二人，而第一人正是楚焱。她当众执礼鼓励，最后在嫉妒声浪中与他并肩而行。"
PREVIEW = "无人知晓的陨落之谜背后，究竟藏着什么？楚烟儿的执念又从何而来？"
EXTERNALIZED = {"event_009", "event_010"}
REMOVED: set[str] = set()
