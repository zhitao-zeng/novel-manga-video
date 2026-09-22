"""A stage that says 席勒 does something to 托尼·斯塔克 has 托尼·斯塔克 in the picture.

in_frame is believed rather than topped up from the prose, because scanning for names invented
ghosts.  But an action is not prose: fields.py asks in_frame to hold "说话的人和这一阶段与他有动作
往来的人", so `actions=[(席勒 → 托尼·斯塔克)]` with `in_frame=[席勒]` is the model contradicting its
own two fields.  Believing in_frame there costs 托尼 his reference card - he is still described in
the shot, the H3 naming table has no tag for him, and the translation writes "Stark", a person the
renderer must invent a face for.  Chapter 12's first part came back with ten such names.
"""
from __future__ import annotations

from novel_manga.planning.context import PlannerContext
from novel_manga.planning.normalization import cast_and_actions

NAMES = ["席勒", "托尼·斯塔克", "佩珀"]
CTX = PlannerContext()
LOCATIONS = {"诊所": "诊所"}


def run(shot):
    errors, warnings = [], []
    characters, extras, actions, motion, location = cast_and_actions(
        {"location": "诊所", **shot}, NAMES, tuple(NAMES), LOCATIONS, "stage 1", CTX, errors, warnings)
    return characters, extras, errors, warnings


def test_the_target_of_an_action_joins_the_cast_even_when_in_frame_was_given():
    cast, _, errors, warnings = run({"characters": ["席勒"], "in_frame_given": True,
                                     "actions": [{"actor": "席勒", "action": "看着", "target": "托尼·斯塔克"}]})
    assert cast == ["席勒", "托尼·斯塔克"]
    assert errors == [] and any("托尼·斯塔克" in w for w in warnings)


def test_the_actor_of_an_action_joins_it_too():
    cast, _, _, _ = run({"characters": ["席勒"], "in_frame_given": True,
                         "actions": [{"actor": "托尼·斯塔克", "action": "指着", "target": "席勒"}]})
    assert set(cast) == {"席勒", "托尼·斯塔克"}


def test_a_name_the_shot_writes_as_elsewhere_is_the_cost_of_the_open_scan():
    """托尼·斯塔克在楼下 is not a presence, and the scan takes him anyway.

    That is the ghost the in_frame guard was closed to stop, and reopening it (2026-09-22, the
    user's call) accepts this case back.  The test states the cost rather than pretending it away:
    what limits it is complete_characters' cap, and the warning naming whoever was added.
    """
    cast, _, _, warnings = run({"characters": ["席勒"], "in_frame_given": True,
                                "visual_prompt": "席勒坐在桌前，托尼·斯塔克在楼下等着",
                                "actions": [{"actor": "席勒", "action": "喝咖啡", "target": ""}]})
    assert cast == ["席勒", "托尼·斯塔克"]
    assert any("托尼·斯塔克" in w for w in warnings)   # auditable, so a bad add can be found afterwards


def test_the_closing_tableau_counts_as_much_as_the_opening_one():
    cast, _, _, _ = run({"characters": ["席勒"], "in_frame_given": True,
                         "end_state": "席勒靠在椅背上，托尼·斯塔克站在门口",
                         "actions": [{"actor": "席勒", "action": "靠", "target": ""}]})
    assert cast == ["席勒", "托尼·斯塔克"]


def test_a_bare_two_character_name_stays_invisible_until_the_entity_index_exists():
    """席勒 and 佩珀 are two characters with no surname and no title, which mentioned_characters
    skips on purpose - 灵魂, 秘女, 船长 are common nouns as often as people.  Only entity_index.json
    can say that a particular short name belongs to exactly one person, and 在美漫当心灵导师的日子
    has never had one built, so reopening the scan does nothing for its protagonist.  The scan is
    half the fix; building the index is the other half.
    """
    cast, _, _, _ = run({"characters": ["托尼·斯塔克"], "in_frame_given": True,
                         "visual_prompt": "托尼·斯塔克站在桌前，席勒坐在对面，佩珀在门口",
                         "actions": [{"actor": "托尼·斯塔克", "action": "站", "target": ""}]})
    assert cast == ["托尼·斯塔克"]
