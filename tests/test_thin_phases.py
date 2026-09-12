"""A character's look follows the chapter: the plan references the phase's card and says its anchor, the
review describes the same phase, and a novel without phases.json behaves as before."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_clip_plan_thin as planner  # noqa: E402
from novel_manga.models import Character, StoryBible  # noqa: E402
from thin_phases import chapter_of, load_phases, phase_card, phase_for, phase_labels, phased  # noqa: E402

PHASES = {"policy": "phase-cards-v1", "characters": {"沈玄川": [
    {"from": 1406, "to": 3504, "asset_id": "character_001-p2", "label": "白发青年", "hair": "满头白发，短发略显凌乱", "age": "青年"},
    {"from": 3505, "to": None, "asset_id": "character_001-p3", "label": "白发老年", "hair": "白发苍苍", "appearance": "面容苍老", "age": "老年"},
]}}


def bible() -> StoryBible:
    return StoryBible(
        novel_title="诸天", genre="仙侠", visual_style="2d", palette="p", style_fingerprint="f",
        characters=[Character(name="沈玄川", role="主角", appearance="清秀但略显疲惫的大学生", wardrobe="白色圆领T恤", hair="黑色短发，略显凌乱", silhouette="修长挺拔，站姿随意"),
                    Character(name="苏清月", appearance="身材高挑", wardrobe="蓝色连衣裙", hair="长发")],
        locations=["宿舍：床铺和书桌"],
    )


def novel(tmp_path: Path) -> Path:
    (tmp_path / "series_assets").mkdir()
    (tmp_path / "series_assets" / "phases.json").write_text(json.dumps(PHASES, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_phase_for_is_inclusive_and_open_ended(tmp_path):
    phases = load_phases(novel(tmp_path))
    assert phase_for(phases, "沈玄川", 1405) is None
    assert phase_for(phases, "沈玄川", 1406)["label"] == "白发青年"
    assert phase_for(phases, "沈玄川", 3504)["label"] == "白发青年"
    assert phase_for(phases, "沈玄川", 3505)["label"] == "白发老年"
    assert phase_for(phases, "沈玄川", 9999)["label"] == "白发老年"
    assert phase_for(phases, "苏清月", 2000) is None
    assert phase_for(phases, "沈玄川", None) is None
    assert load_phases(tmp_path / "nowhere") == {}


def test_phased_lays_the_look_over_a_copy():
    character = bible().characters[0]
    look = phased(character, PHASES["characters"]["沈玄川"][1])
    assert look.hair == "白发苍苍" and look.appearance == "面容苍老" and look.age == "老年"
    assert look.wardrobe == character.wardrobe  # not named by the phase: unchanged
    assert character.hair == "黑色短发，略显凌乱"  # the bible entry itself is untouched
    beast = phased(character, {"hair": "", "base_costume": "无衣物"})
    assert beast.hair == "" and beast.wardrobe == "无衣物"  # an explicit empty clears; a costume also sets wardrobe
    assert phased(character, None) is character


def test_plan_references_follow_the_chapter(tmp_path):
    novel_dir = novel(tmp_path)
    b = bible()
    location_map = {"宿舍": "宿舍：床铺和书桌"}
    later, bindings, _ = planner.build_references(["沈玄川", "苏清月"], "宿舍", b, location_map, novel_dir=novel_dir, chapter=2000)
    lead = [ref for ref in later if ref["role"] == "character" and ref["name"] == "沈玄川"]
    assert lead and all(ref["asset_id"] == "character_001-p2" for ref in lead)
    assert lead[0]["path"] == "series_assets/characters/character_001-p2/turnaround.jpeg" and lead[0]["phase"] == "白发青年"
    assert "满头白发" in bindings[0] and "黑色短发" not in bindings[0]
    other = [ref for ref in later if ref["role"] == "character" and ref["name"] == "苏清月"]
    assert other and other[0]["asset_id"] == "character_002" and "phase" not in other[0]
    assert "不得使用这两张图的相貌" in bindings[1]  # the two-view binding forbids the face on anyone else
    assert phase_labels([{"references": later}]) == ["沈玄川:白发青年"]
    # the variant has no expressions.jpeg, so it is referenced by its turnaround alone (two views for the base card)
    assert [ref["path"] for ref in lead] == ["series_assets/characters/character_001-p2/turnaround.jpeg"]
    assert "只对应@图片1" in bindings[0]
    assert len(other) == 2 and other[1]["path"].endswith("character_002/expressions.jpeg")

    early, bindings, _ = planner.build_references(["沈玄川"], "宿舍", b, location_map, novel_dir=novel_dir, chapter=100)
    assert early[0]["asset_id"] == "character_001" and "phase" not in early[0]
    assert "修长挺拔" in bindings[0] and "黑色短发" not in bindings[0]

    none, bindings, _ = planner.build_references(["沈玄川"], "宿舍", b, location_map, novel_dir=None, chapter=2000)
    assert none[0]["asset_id"] == "character_001" and "修长挺拔" in bindings[0]  # no novel dir: no phases, as before


def test_the_judge_gets_the_phase_card_once_it_is_drawn(tmp_path):
    novel_dir = novel(tmp_path)
    phases = load_phases(novel_dir)
    assert phase_card(novel_dir, phases, "沈玄川", 2000) is None  # phase known, card not drawn yet
    card = novel_dir / "series_assets" / "characters" / "character_001-p2" / "turnaround.jpeg"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"jpeg")
    assert phase_card(novel_dir, phases, "沈玄川", 2000) == Path("series_assets/characters/character_001-p2/turnaround.jpeg")
    assert phase_card(novel_dir, phases, "沈玄川", 100) is None  # no phase for the chapter: the plan's card stands
    assert phase_card(novel_dir, phases, "苏清月", 2000) is None


def test_chapter_of_reads_the_episode_directory():
    assert chapter_of(Path("/x/zhutian-card/zhutian-card_2043")) == 2043
    assert chapter_of(Path("/x/zhutian-card/series_assets")) is None
