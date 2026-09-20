"""Scene-first observation: independent implementation of general directing principles."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="dream", name="造梦师：场景事实与观察机位", author="ZY / popopo-99（方法参考）",
    sources=("https://github.com/popopo-99/zy-cinematic-realism",),
    strategy="先确定场景内已经发生的事、此刻的具体行为和留下的痕迹，再选择摄影机能站立的位置。"
             "通过身体负担、空间距离和物件使用表现情绪。只借鉴场景和摄影方法，不改用户锁定的动画媒介。",
    episode_fields=(("scene_invariants", "固定的时间、地点、身份、物件及不可提前展示的信息"),
                    ("witness_and_light", "摄影机怎样观察，主光源来自哪里，什么环境痕迹有因果"),
                    ("restraint", "哪些等待、停顿或余波有用，哪些装饰可以省掉")),
    beat_fields=(("causal_trace", "画面里什么细节能说明刚发生了什么"),
                 ("witness_position", "摄影机的实际位置、高度、朝向及该处可见范围"),
                 ("body_and_object", "身体姿态、目光或物件操作如何体现状态变化")),
    drafting='先锁定本场的时间、地点、人物、道具和空间关系，再从真实可站立的位置观察。每镜只改变当前动作和由它导致的状态；手的占用、衣服干湿、道具归属要延续。用实际身体任务和场景痕迹表现情绪，给动作和余波时间，不用新增烟雾逆光或慢镜制造质感。镜头语法不能改动画媒介；该方法负责场景和摄影，不重写上游剧本。',
)
