"""Concrete subject, object and sound continuity adapted to the existing art profile."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="community", name="社区短片：物件与同期声", author="jnMetaCode；方法来源注明 Mx-Shell",
    sources=("https://github.com/jnMetaCode/ai-shortfilm-prompts",),
    strategy="围绕能看清的身体和物件行为组织叙事，先固定人物、物件、环境的识别特征，再安排镜头。"
             "声音来自真实动作，结尾让已有动作或物件承接结果。保持用户的二维或三维画风，不套写实禁用词。",
    episode_fields=(("subject_and_prop_anchors", "需要跨镜一致的人物与物件，以及已有依据中的识别特征"),
                    ("look_and_sound_lock", "本集继承的色调、光源和具体环境声音"),
                    ("object_payoff", "原文已有物件或行为如何在结尾获得不同含义；不适用则说明")),
    beat_fields=(("physical_detail", "能被看见的材质或使用状态，必须有场景或原文依据"),
                 ("sound_cause", "哪个动作发出什么声音，声音什么时候开始或停止"),
                 ("screen_direction", "主体进出方向、持物归属和与前镜的衔接")),
    drafting='先明确反复出现的人物和道具状态、动画媒介及色调，再安排连续镜头。每镜写身体做什么、物件怎样改变和由此发出的声音；用动作落点、方向或同期声接镜。跨日只改变有依据的状态，不重设计角色、道具和画风。不套用真人质感、固定手持或人为瑕疵。',
)
