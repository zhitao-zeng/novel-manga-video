"""Shared scene truth and a playable stimulus/response performance score."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="leos", name="Leos：刺激与表演过程", author="MasterLeos",
    sources=("https://github.com/MasterLeos/leos-six-department-directing-team-skill-v1",),
    strategy="用同一份场次事实约束导演、表演、摄影与连续性。先明确谁的场、目标与空间关系，"
             "再让外部刺激引起身体调整和实际动作，最后留出动作余波；这些职责在一次规划内协作，不启动六个代理。",
    episode_fields=(("scene_focus", "本集主要人物的场次目标、阻碍和注意力中心"),
                    ("blocking_and_attention", "人物、动物、道具的空间关系及注意力变化"),
                    ("continuity_and_asset_roles", "需要延续的状态，以及人物、场景、动作参考各自应该控制什么")),
    beat_fields=(("stimulus", "人物首先看见或听见的具体刺激"),
                 ("adjustment", "刺激之后身体、视线或呼吸发生的可见调整"),
                 ("performance_and_aftermath", "带着目标完成的动作，以及结束后仍留在画面里的结果")),
    drafting='先共同确定本场要让观众感到什么及空间关系，再按表演、镜内活动、摄影、连续性分工设计。具体刺激先发生，人物接收后改变呼吸、重心、目光或手部任务，再完成有目标的动作并留余波。每次只选必要表演通道。接触动作写靠近、接触、受力与分离；摄影服务动作。检查上一出口和下一入口，不让几个部门分别重写一遍剧情。',
)
