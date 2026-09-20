"""Which bible locations a chapter may be planned in.

The rule has to hold two things at once, and 超品相师 chapter 3 is the case where they collided: the
chapter arrives at a bus station and rides past a building site, both of which the base bible
carries, and both were dropped because neither name appears verbatim in the chapter and neither
episode 1 nor 2 had used them - so all eight shots were planned at the shopping mall those episodes
did use, including 下大巴 and a canteen flashback.
"""
from novel_manga.planning.places import offered_locations

BASE = ["诸葛庐石碑园地中心：开阔的石板广场，中央一通高大石碑。",
        "商场外约定集合点：商场入口外的空地，下午阳光直射。",
        "南昌市长途汽车站出口：玻璃幕墙与钢结构雨棚，白天天光。",
        "南昌大学食堂内部：宽敞明亮的食堂大厅，白天自然光从窗户照入。"]


def never_named(_name: str) -> bool:
    return False


def test_a_base_bible_location_stays_offered_when_the_chapter_does_not_name_it():
    # The whole point of the base bible is that the opening chapters established these places.
    offered = offered_locations(BASE, chapter=3, named_here=never_named,
                                recent={"商场外约定集合点"}, added_at={}, window=3)
    assert offered == BASE


def test_a_place_the_story_has_not_reached_is_not_offered():
    future = BASE + ["天工阁顶层：悬空的木构阁楼，夜里只有檐下灯笼。"]
    added_at = {"天工阁顶层": 40}
    offered = offered_locations(future, chapter=3, named_here=never_named,
                                recent=set(), added_at=added_at, window=3)
    assert "天工阁顶层：悬空的木构阁楼，夜里只有檐下灯笼。" not in offered
    assert offered == BASE


def test_a_location_added_recently_is_offered_even_when_unnamed():
    grown = BASE + ["县城公路：路面平整，两侧是在建楼房骨架，白天天光。"]
    offered = offered_locations(grown, chapter=5, named_here=never_named,
                                recent=set(), added_at={"县城公路": 4}, window=3)
    assert "县城公路：路面平整，两侧是在建楼房骨架，白天天光。" in offered


def test_when_nothing_qualifies_the_most_recent_reached_places_are_offered():
    # Only grown locations, all of them long past the window: the chapter still has to film somewhere.
    grown = ["甲地：描写甲甲甲甲甲甲甲甲", "乙地：描写乙乙乙乙乙乙乙乙"]
    offered = offered_locations(grown, chapter=90, named_here=never_named, recent=set(),
                                added_at={"甲地": 10, "乙地": 20}, window=3)
    assert offered == ["乙地：描写乙乙乙乙乙乙乙乙", "甲地：描写甲甲甲甲甲甲甲甲"]
