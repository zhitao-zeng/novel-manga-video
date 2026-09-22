"""A stage that says 席勒 does something to 托尼·斯塔克 has 托尼·斯塔克 in the picture.

in_frame is believed rather than topped up from the prose, because scanning for names invented
ghosts.  But an action is not prose: fields.py asks in_frame to hold "说话的人和这一阶段与他有动作
往来的人", so `actions=[(席勒 → 托尼·斯塔克)]` with `in_frame=[席勒]` is the model contradicting its
own two fields.  Believing in_frame there costs 托尼 his reference card - he is still described in
the shot, the H3 naming table has no tag for him, and the translation writes "Stark", a person the
renderer must invent a face for.  Chapter 12's first part came back with ten such names.
"""
from __future__ import annotations

from types import SimpleNamespace

from novel_manga.planning.normalization import cast_and_actions

NAMES = ["席勒", "托尼·斯塔克", "佩珀"]
CTX = SimpleNamespace(aliases={}, entity_forms={}, entity_generic={}, story_blueprint={})
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


def test_someone_merely_named_in_the_prose_still_does_not_join():
    # the ghost guard: in_frame stays authoritative for everything that is not an action partner
    cast, _, _, _ = run({"characters": ["席勒"], "in_frame_given": True,
                         "visual_prompt": "席勒坐在桌前，佩珀在楼下等着",
                         "actions": [{"actor": "席勒", "action": "喝咖啡", "target": ""}]})
    assert cast == ["席勒"]


def test_an_unnamed_partner_stays_an_extra_and_never_becomes_a_character():
    cast, extras, _, _ = run({"characters": ["席勒"], "in_frame_given": True, "extras": ["银白色机甲"],
                              "actions": [{"actor": "银白色机甲", "action": "撞开", "target": "窗户"}]})
    assert cast == ["席勒"] and "银白色机甲" in extras
