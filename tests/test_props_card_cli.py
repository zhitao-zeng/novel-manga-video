"""The card CLI's id parsing routes prop ids to the props lane."""
from novel_manga.application.assets.cards import build_with_backoff, split_asset_ids


def test_split_asset_ids_routes_props():
    characters, locations, props = split_asset_ids("character_001,prop_002,location_003, prop_001")
    assert characters == {"character_001"} and locations == {"location_003"}
    assert props == {"prop_001", "prop_002"}


def test_build_with_backoff_passes_prop_ids():
    seen = {}

    class FakeFactory:
        def build_selected(self, root, bible, characters, locations, expressions=True, prop_ids=None):
            seen["prop_ids"] = prop_ids
            return "manifest"

    out = build_with_backoff(FakeFactory(), None, None, set(), set(), None, props={"prop_001"})
    assert out == "manifest"
    assert seen["prop_ids"] == {"prop_001"}
