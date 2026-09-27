"""A location card is an empty room, and one that is not stops the clips that use it (2026-09-26).

The agent's ch12 lab card came from the local card model with three strangers in it; forty of the
episode's sixty-one clips used it, and H3 casts whoever it finds in a set.  The template asked for room
for one or two people to stand and walk in, which drew them (3, 3 and 2 at three seeds, none without it),
and the service draws at one fixed seed, so asking again returned the same room.
"""
import json

from novel_manga.application.preparation import readiness
from novel_manga.config import Settings
from novel_manga.media import asset_builder, card_check
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_images import ensure_image
from novel_manga.media.asset_prompts import location_prompt
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult

LAB = "series_assets/locations/location_004/establishing.jpeg"


class Draws:
    """Writes a different picture on every call and remembers the seed each call was given."""

    def __init__(self):
        self.seeds = []

    def create_image(self, prompt, output, **kwargs):
        self.seeds.append(kwargs.get("seed"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"picture {len(self.seeds)}".encode())
        return ImageResult(path=output)


def test_the_template_asks_for_floor_not_for_people():
    bible = StoryBible(novel_title="测试", genre="generic", visual_style="二维美漫", palette="冷蓝",
                       style_fingerprint="fixed", characters=[Character(name="甲", appearance="黑发", wardrobe="蓝衣")],
                       locations=["实验室：机械臂"])
    prompt = location_prompt(bible, "实验室：机械臂")
    assert "人物站立" not in prompt and "表演空间" not in prompt
    assert "空地" in prompt and "不得出现人物" in prompt


def test_a_seed_is_recorded_only_for_a_card_drawn_again_on_purpose(tmp_path):
    draws = Draws()
    ensure_image(Settings(), draws, "空房间", tmp_path / "a.jpeg")
    ensure_image(Settings(), draws, "空房间", tmp_path / "b.jpeg", seed=1009)
    assert draws.seeds == [None, 1009]
    assert "seed" not in json.loads((tmp_path / "a.jpeg.request.json").read_text())
    assert json.loads((tmp_path / "b.jpeg.request.json").read_text())["seed"] == 1009


def test_a_card_with_people_in_it_is_drawn_again_at_another_seed(tmp_path, monkeypatch):
    counts = iter([{"people_in_scene": 2, "pictures_of_people": 0, "where": "门口两个人"},
                   {"people_in_scene": 0, "pictures_of_people": 1, "where": "墙上一张照片"}])
    monkeypatch.setattr(asset_builder, "people_in_card", lambda card: next(counts))
    draws = Draws()
    factory = FramedAssetFactory(Settings(), draws)
    card = tmp_path / LAB
    factory._keep_empty("空房间", card, factory.ensure_card("空房间", card, aspect_ratio="16:9"), aspect_ratio="16:9")
    assert draws.seeds == [None, FramedAssetFactory.EMPTY_RETRY_SEEDS[0]]
    assert card.with_name("establishing.people-rejected-1.jpeg").read_bytes() == b"picture 1"
    saved = json.loads(card.with_suffix(".jpeg.request.json").read_text())
    assert saved["seed"] == FramedAssetFactory.EMPTY_RETRY_SEEDS[0]
    assert saved["people_check"]["people_in_scene"] == 0 and card_check.people_found(card) == 0


def test_a_judge_that_cannot_be_reached_leaves_the_card_unchecked_rather_than_stopped(tmp_path, monkeypatch):
    def down(card):
        raise RuntimeError("every endpoint refused")
    monkeypatch.setattr(asset_builder, "people_in_card", down)
    draws = Draws()
    factory = FramedAssetFactory(Settings(), draws)
    card = tmp_path / LAB
    factory._keep_empty("空房间", card, factory.ensure_card("空房间", card))
    assert draws.seeds == [None]
    assert "error" in json.loads(card.with_suffix(".jpeg.request.json").read_text())["people_check"]
    assert card_check.people_found(card) == 0


def lab_clip():
    return {"clip_id": "clip_21", "kind": "video", "request_seconds": 10, "prompt": "",
            "references": [{"role": "location", "name": "斯塔克大厦实验室", "path": LAB}]}


def test_a_card_still_holding_people_stops_the_clips_that_use_it(tmp_path):
    card = tmp_path / LAB
    card.parent.mkdir(parents=True)
    card.write_bytes(b"lab with three strangers")
    card_check.record_people_check(card, {"people_in_scene": 3, "pictures_of_people": 2, "where": "风衣男等三人"})
    block, _ = readiness.render_risks(lab_clip(), tmp_path)
    assert len(block) == 1 and "3个人" in block[0]
    assert readiness.render_risks(lab_clip(), tmp_path, {"card_people": [LAB]})[0] == []
    card.write_bytes(b"the same room, redrawn by hand")      # the count was about another picture
    assert readiness.render_risks(lab_clip(), tmp_path)[0] == []
