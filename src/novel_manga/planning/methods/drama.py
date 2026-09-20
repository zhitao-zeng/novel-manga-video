"""Episode promise, causal scenes and traceable handoffs from Drama Skills."""
from .base import StoryMethod

METHOD = StoryMethod(
    key="drama", name="Drama：单集承诺与因果场景", author="zenstory-ai",
    sources=("https://github.com/zenstory-ai/drama-skills",),
    strategy="从进入状态、人物当下目标、本集兑现和退出状态组织场景。每一拍回答为何行动、行动造成什么结果。"
             "只强化原文实际存在的冲突，不为模板增加倒计时、证据机关、反派或强行反转。",
    episode_fields=(("episode_promise", "本集要让观众看懂并得到什么具体回报"),
                    ("causal_escalation", "原因、选择、结果如何接续，原文中哪些因素真正增加阻力"),
                    ("entry_exit_contract", "进入和离开本集时，知识、关系、持物与后果的变化")),
    beat_fields=(("because", "原文中的什么事实促使这次行动"),
                 ("visible_change", "这拍改变的信息、关系、风险或物理状态"),
                 ("handoff", "已经建立、必须交给下一拍的事实；没有则明确说明")),
    drafting='先落实每场剧本的实际动作、对白与声音，再决定观众此刻知道什么、还不知道什么。每个关键动作由一个主要镜头完成，其余镜头增加细节或反应，不能再做一次。揭示、核对、反应各需什么可见载体就安排什么；按起点、连续动作、终点设计，导演改变表达，不改变已成立的剧情、台词或人物知识。',
    screenwriting='编剧方法采用Drama的观看承诺与场景因果：先判断观众必须知道什么，再选择可见动作、对白、空间或声音来承载。每场回答前因、行动、可见结果及下一场依赖的状态。把选择与后果写在正文，不靠标题宣称。核心剧情信息不能只存在于summary或purpose里。没有原文支持的对手、限时机关或反转，不为结构齐全而添加。',
)
