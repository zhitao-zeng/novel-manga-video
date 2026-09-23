"""The scene-exit question-and-answer pair: the question whose answer is the next scene.

Both routes dropped it (the sandbox sheet and the local two-pass): 席勒 asks whether he must ride
the armour, 托尼 answers how they are going, and the NEXT SHOT is the two of them at the bus stop.
handoff_pairs finds such pairs by their tell - a question quote, its answer quote close by, and a
scene-change marker right after the answer.  Dialogue inside a scene (a ten-pair interrogation
chain) is not a hand-off and must not flood the instruction.
"""
from novel_manga.planning.text import handoff_pairs


SOURCE = """
斯塔克一打响指，又一台机甲飞了进来，席勒和他大眼瞪小眼，指着那台机甲说:“你该不会想让我坐这个过去吧？”
“不然呢？你打算怎么过去？”
几分钟后，斯塔克和席勒出现在了地狱厨房破旧的公交站牌下面，斯塔克说:“我真不敢相信我跨时代战衣的首秀就是在一辆冒黑烟的破旧巴士上……”
"""

CHAIN = """
席勒问:“如果你的父亲就要死了，你会后悔吗？”
“如果他死了，你觉得他在临死前会后悔生了你吗？”
“如果他死了，你觉得他会怨恨你吗？”
“如果他怨恨你，你会自责吗？”
两人沉默了片刻，斯塔克抬起头。
"""


def test_the_bus_stop_pair_is_found():
    pairs = handoff_pairs(SOURCE)
    assert pairs == [("你该不会想让我坐这个过去吧？", "不然呢？你打算怎么过去？")]


def test_a_chain_inside_a_scene_is_not_a_handoff():
    """Ten questions answered in a row inside the lab are dialogue, not scene exits: none of them
    is followed by a change of place or time."""
    assert handoff_pairs(CHAIN) == []


def test_a_question_without_a_close_answer_is_skipped():
    assert handoff_pairs("他说:“今天天气怎么样？”\n\n很久以后，另一章里有人提起了往事。") == []
