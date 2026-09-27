"""A worn phase card is drawn from the wearer's base card (the face) and the prop's card (the armour).

美漫 ch12 (2026-09-25): drawn from the prop card alone, 托尼's face came from the text and was another man's;
with his base card as picture 1 it is his."""
from types import SimpleNamespace

from novel_manga.application.assets.phase_cards import worn_inputs
from novel_manga.media import asset_builder

SUIT = SimpleNamespace(name="马克2机甲", appearance="银白色金属人形机甲")


def test_the_wearers_face_is_picture_one_and_the_armour_picture_two(tmp_path):
    base, card = tmp_path / "base.jpeg", tmp_path / "prop.jpeg"
    base.write_bytes(b"face")
    card.write_bytes(b"suit")
    lead, note, references = worn_inputs("托尼·斯塔克", SUIT, card, base)
    assert references == [base, card]
    assert lead.startswith("图1是托尼·斯塔克本人的角色卡") and "不采用图1的服装" in lead
    assert "图2是马克2机甲的设定图" in lead and "以图2中该物品" in note


def test_without_a_base_card_the_prop_card_alone_as_before(tmp_path):
    card = tmp_path / "prop.jpeg"
    card.write_bytes(b"suit")
    lead, note, references = worn_inputs("托尼·斯塔克", SUIT, card, tmp_path / "missing.jpeg")
    assert references == [card] and lead == "" and "以参考图中该物品" in note


def test_the_second_picture_reaches_the_image_call(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(asset_builder, "ensure_image", lambda settings, provider, prompt, output, **kw: seen.update(kw))
    factory = asset_builder.FramedAssetFactory.__new__(asset_builder.FramedAssetFactory)
    factory.settings = factory.provider = None
    factory.ensure_card("p", tmp_path / "o.jpeg", reference=tmp_path / "a.jpeg", additional_references=[tmp_path / "b.jpeg"])
    assert seen["reference"] == tmp_path / "a.jpeg" and seen["additional_references"] == (tmp_path / "b.jpeg",)
