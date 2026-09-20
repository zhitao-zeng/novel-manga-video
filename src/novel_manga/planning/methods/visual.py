"""Dramatic beats, shot functions and motivated montage, informed by Serge Shima."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="visual", name="Visual：信息释放与剪辑节奏", author="Serge Shima",
    sources=("https://github.com/smixs/visual-skills",),
    strategy="先排戏剧变化，再为每拍确定镜头职责，最后安排剪辑节奏。镜头需要推动行动、改变情绪或增加压力。"
             "观众的视线、声音和反复出现的具体元素帮助连接场景；不为快切而切，不给每镜堆同样的装饰。",
    episode_fields=(("dramatic_spine", "本章人物、阻力、选择与结果构成的叙事主线"),
                    ("information_and_rhythm", "哪些信息先给、哪些在转折时释放，快慢段怎样形成对照"),
                    ("motif_and_final_image", "有原文依据的声音或视觉元素怎样贯穿，末镜落在哪个可见结果")),
    beat_fields=(("shot_job", "推进动作、改变情绪或增加压力中的具体哪一种变化"),
                 ("environment_and_body", "环境怎样影响人物的一个具体身体动作"),
                 ("cut_trigger", "在何种信息或动作变化处切镜，下一镜接住哪处注意力")),
    drafting='先排观众的信息和情绪变化，再给每镜分配实际职责，最后选择切点和快慢。安排注意落点、动作匹配和声音何时进入或退出；母题必须在具体镜头产生作用。每个运镜说明由什么动作或发现触发，关键揭示前后有节奏差异。不为凑细节、快切密度或固定比例增加镜头；末镜留下已经成立的具体结果。',
)
