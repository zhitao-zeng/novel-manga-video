"""Action-led writing and two-track pacing, informed by Shanyin's public methods."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="shanyin", name="山音：戏剧动作与双轨节奏", author="@山音",
    sources=("https://github.com/Shanyin-ai/shanyin-screenwriting-master",
             "https://github.com/Shanyin-ai/shanyin-director-master"),
    strategy="先确定本章人物争取什么、受到什么阻碍、最终改变什么。分别安排事件推进与情绪消化的速度，"
             "把潜台词落在可见行为上；高潮与余波分清，不把整集写成同一强度。只使用原文已有前史与弧光。",
    episode_fields=(("dramatic_action", "本集核心戏剧动作、阻力和原文给出的结果"),
                    ("outer_inner_rhythm", "事件速度与人物反应如何错开，哪里需要停留"),
                    ("opening_and_payoff", "开头建立的具体问题，以及结尾怎样回应")),
    beat_fields=(("subtext_action", "人物没有说出口的意图由哪个动作体现"),
                 ("rhythm_job", "本拍是推进、加压还是消化结果，为什么"),
                 ("shot_group", "与前后哪一拍共同完成一个叙事任务，切镜依据是什么")),
    drafting='先定观看视点，再按场景安排事件推进和情绪消化的快慢。为关键发现设计镜头组，一组共同完成铺垫、看见、核对、反应；不要每句换一个脸部特写。普通动作简洁完成，转折和结尾给完整表演的时间。让声音、物件或构图的呼应落到具体镜头。',
    screenwriting='编剧方法采用山音的戏剧动作与双轨节奏：先根据原文确定这集真正的追求、阻碍与结果；选择进入故事的窗口，保留事件之后消化结果的时间。把核心信息放进动作、物件、停顿与口语对白。连续行为写成一场完整可演的戏，再写下一场。轻重可以错开；不要把全章写成同强度梗概。人物前史只用原文已给的内容；原文结尾崩溃就不能为了弧光擅自改成释然。',
)
