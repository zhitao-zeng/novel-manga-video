"""Selected redraws publish complete cards; reuse does not rewrite their history."""
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

from novel_manga.application.assets import cards
from novel_manga.config import Settings
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_images import ensure_image
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult
from novel_manga.review.prompts import describe


class Draws:
    def __init__(self):
        self.calls = []
        self.fail_expression = False

    def create_image(self, prompt, output, **kwargs):
        self.calls.append((prompt, output))
        if self.fail_expression and output.name == "expressions.jpeg":
            raise RuntimeError("expression provider unavailable")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (len(self.calls) * 20, 0, 0)).save(output)
        output.with_suffix(output.suffix + ".task.json").write_text(
            json.dumps({"task_id": f"fake-{len(self.calls)}"}))
        return ImageResult(path=output)


def bible():
    return StoryBible(novel_title="测试", genre="generic", visual_style="二维国漫",
        palette="蓝", style_fingerprint="fixed", locations=["厅堂"], characters=[
            Character(name="甲", appearance="瘦长脸", wardrobe="蓝衣", hair="黑发"),
            Character(name="乙", appearance="圆脸", wardrobe="白衣", hair="棕发")])


def revised(source):
    return source.model_copy(update={"characters": [
        source.characters[0].model_copy(update={"appearance": "宽脸，明确青年外貌"}),
        source.characters[1]]})


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".redraw" not in p.parts and p.suffix != ".lock"}


def test_locked_reuse_keeps_the_actual_generation_record(tmp_path):
    drawn = Draws()
    path = tmp_path / "turnaround.jpeg"
    ensure_image(Settings(), drawn, "旧外貌", path)
    meta = path.with_suffix(".jpeg.request.json")
    before = meta.read_bytes()
    image = path.read_bytes()

    result = ensure_image(Settings(reuse_existing_assets=True), drawn, "新外貌", path)

    assert len(drawn.calls) == 1
    assert path.read_bytes() == image
    assert meta.read_bytes() == before
    assert result.request_current is False


def test_an_unrecorded_locked_image_does_not_gain_a_fabricated_request(tmp_path):
    path = tmp_path / "turnaround.jpeg"
    path.write_bytes(b"historical image")
    result = ensure_image(Settings(reuse_existing_assets=True), Draws(), "新外貌", path)
    assert result.request_current is False
    assert not path.with_suffix(".jpeg.request.json").exists()


def test_a_legacy_locked_stamp_is_not_current_generation_proof(tmp_path):
    drawn = Draws()
    path = tmp_path / "turnaround.jpeg"
    ensure_image(Settings(), drawn, "新外貌", path)
    meta = path.with_suffix(".jpeg.request.json")
    record = json.loads(meta.read_text())
    record.update(origin="locked-existing-asset", previous_request_sha256="old-request")
    meta.write_text(json.dumps(record))
    before = meta.read_bytes()

    result = ensure_image(Settings(reuse_existing_assets=True), drawn, "新外貌", path)

    assert result.request_current is False
    assert len(drawn.calls) == 1
    assert meta.read_bytes() == before


def test_reused_cards_keep_their_effective_specs_and_manifest(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    source = bible()
    factory.build_selected(root, source, {"character_001", "character_002"}, set(), prop_ids=set())
    before = snapshot(root)

    factory.build_selected(root, revised(source), {"character_001"}, set(), prop_ids=set())

    assert len(drawn.calls) == 4
    assert snapshot(root) == before


def test_explicit_redraw_changes_only_the_selected_asset_and_preserves_history(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    source = bible()
    factory.build_selected(root, source, {"character_001", "character_002"}, set(), prop_ids=set())
    selected = root / "characters/character_001"
    marker = selected / ".regenerated.turnaround.txt"
    marker.write_text("historical repair limit")
    (root / "cards_review.json").write_text(json.dumps({"characters": {
        "character_001": {"name": "甲", "judged_at": 1, "actions": ["regenerate"]},
        "character_002": {"name": "乙", "judged_at": 2, "actions": []}},
        "locations": {}, "flags": ["character_001 甲: regenerate"], "missing_cards": []}))
    original = snapshot(root)
    old_manifest = json.loads((root / "manifest.json").read_text())

    factory.build_selected(root, revised(source), {"character_001"}, set(), prop_ids=set(), redraw=True)

    assert len(drawn.calls) == 6
    assert "宽脸" in json.loads((selected / "spec.json").read_text())["appearance"]
    assert json.loads((root / "manifest.json").read_text())["characters"][0]["identity_invariants"][0].startswith("宽脸")
    assert json.loads((root / "manifest.json").read_text())["characters"][1] == old_manifest["characters"][1]
    for name in ("turnaround.jpeg", "expressions.jpeg", "spec.json"):
        assert (selected / name).read_bytes() != original[f"characters/character_001/{name}"]
        assert any(p.read_bytes() == original[f"characters/character_001/{name}"]
                   for p in selected.glob(f"{Path(name).stem}.stale-*{Path(name).suffix}"))
    for path, content in original.items():
        if path.startswith("characters/character_002/"):
            assert (root / path).read_bytes() == content
    review = json.loads((root / "cards_review.json").read_text())
    assert "character_001" not in review["characters"]
    assert review["characters"]["character_002"]["judged_at"] == 2
    assert marker.read_text() == "historical repair limit"
    assert any(json.loads(p.read_text())["judged_at"] == 1 for p in selected.glob("card_review.stale-*.json"))


def test_a_failed_expression_redraw_keeps_the_entire_active_card(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    source = bible()
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    factory.build_selected(root, source, {"character_001"}, set(), prop_ids=set())
    before = snapshot(root)
    drawn.fail_expression = True

    with pytest.raises(RuntimeError, match="expression provider unavailable"):
        factory.build_selected(root, revised(source), {"character_001"}, set(), prop_ids=set(), redraw=True)

    assert snapshot(root) == before
    drawn.fail_expression = False
    calls = len(drawn.calls)
    factory.build_selected(root, revised(source), {"character_001"}, set(), prop_ids=set(), redraw=True)
    assert len(drawn.calls) == calls + 1  # The successful staged main card is reused.


def test_review_service_recovery_reuses_both_unpublished_candidate_images(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    factory.build_selected(root, bible(), {"character_001"}, set(), prop_ids=set())
    before = snapshot(root)

    def unavailable(candidate, asset_id):
        assert (candidate / "series_assets/characters" / asset_id / "turnaround.jpeg").is_file()
        assert (candidate / "series_assets/characters" / asset_id / "expressions.jpeg").is_file()
        raise ConnectionError("review endpoint unavailable")

    with pytest.raises(ConnectionError, match="review endpoint unavailable"):
        factory.build_selected(root, revised(bible()), {"character_001"}, set(), redraw=True,
                               review_candidate=unavailable)
    assert snapshot(root) == before
    calls = len(drawn.calls)

    def restored(candidate, asset_id):
        return {"flags": [], "missing_cards": [], "characters": {asset_id: {
            "name": "甲", "judged_at": 10 ** 12, "actions": []}}}

    factory.build_selected(root, revised(bible()), {"character_001"}, set(), redraw=True,
                           review_candidate=restored)
    assert len(drawn.calls) == calls
    assert (root / "characters/character_001/turnaround.jpeg").read_bytes() != before[
        "characters/character_001/turnaround.jpeg"]


def test_redraw_requires_a_nonempty_explicit_asset_scope(tmp_path):
    drawn = Draws()
    with pytest.raises(ValueError, match="explicit"):
        FramedAssetFactory(Settings(), drawn).build_selected(
            tmp_path / "series_assets", bible(), set(), set(), redraw=True)
    assert not drawn.calls


def test_repeated_explicit_redraw_preserves_each_previous_generation(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    source = bible()
    folder = root / "characters/character_001"
    prior = []
    factory.build_selected(root, source, {"character_001"}, set(), expressions=False, prop_ids=set())
    for _ in range(3):
        prior.append((folder / "turnaround.jpeg").read_bytes())
        factory.build_selected(root, source, {"character_001"}, set(), expressions=False, redraw=True)
    archived = [p.read_bytes() for p in folder.glob("turnaround.stale-*.jpeg")]
    assert len(archived) == 3
    assert all(image in archived for image in prior)


def test_replacing_a_missing_card_removes_only_its_missing_summary(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(), drawn)
    factory.build_selected(root, bible(), {"character_001"}, set(), expressions=False, prop_ids=set())
    (root / "cards_review.json").write_text(json.dumps({"characters": {}, "locations": {},
        "missing_cards": ["character_001 甲", "character_002 乙"],
        "flags": ["待重建的卡（所属章节建卡时生成）：character_001 甲、character_002 乙"]}))

    factory.build_selected(root, bible(), {"character_001"}, set(), expressions=False, redraw=True)

    report = json.loads((root / "cards_review.json").read_text())
    assert report["missing_cards"] == ["character_002 乙"]
    assert report["flags"] == ["待重建的卡（所属章节建卡时生成）：character_002 乙"]


def test_fast_resume_does_not_review_or_publish_a_rejected_expression_sheet(tmp_path):
    drawn = Draws()
    root = tmp_path / "series_assets"
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), drawn)
    factory.build_selected(root, bible(), {"character_001"}, set(), prop_ids=set())

    with pytest.raises(ValueError, match="review failed"):
        factory.build_selected(root, revised(bible()), {"character_001"}, set(), redraw=True,
            review_candidate=lambda candidate, aid: {"flags": ["age mismatch"]})

    newest = revised(bible()).model_copy(update={"characters": [
        revised(bible()).characters[0].model_copy(update={"appearance": "宽脸，明显成熟的中年外貌"}),
        bible().characters[1]]})

    def accepts_primary_only(candidate, aid):
        card = candidate / "series_assets/characters" / aid
        assert (card / "turnaround.jpeg").is_file()
        assert not (card / "expressions.jpeg").exists()
        return {"flags": [], "missing_cards": [], "characters": {aid: {
            "name": "甲", "judged_at": 10 ** 12, "actions": []}}}

    manifest = factory.build_selected(root, newest, {"character_001"}, set(), expressions=False,
        redraw=True, review_candidate=accepts_primary_only)

    assert manifest.characters[0].secondary_image is None
    assert not (root / "characters/character_001/expressions.jpeg").exists()
    assert list((root / "characters/character_001").glob("expressions.stale-*.jpeg"))


def test_age_reaches_the_same_generation_prompt_without_deriving_visual_age(tmp_path):
    drawn = Draws()
    source = bible().model_copy(update={"characters": [bible().characters[0].model_copy(update={
        "age": "实际一百岁，外貌青年", "appearance": "明确青年外貌，瘦长脸"})]})
    FramedAssetFactory(Settings(), drawn).build_selected(
        tmp_path / "series_assets", source, {"character_001"}, set(), expressions=False, prop_ids=set())
    prompt = drawn.calls[0][0]
    assert "实际一百岁，外貌青年" in prompt and "明确青年外貌，瘦长脸" in prompt
    assert "寿龄" in prompt and "外观" in prompt
    assert "老年" not in prompt


def test_cli_rejects_empty_redraw_scope_before_initializing_a_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build_cards_thin.py", "--novel-dir", str(tmp_path), "--assets", "", "--redraw"])
    monkeypatch.setattr(cards.Settings, "from_env", lambda **kw: pytest.fail("provider setup must not run"))
    with pytest.raises(SystemExit) as result:
        cards.main()
    assert result.value.code == 2


@pytest.mark.parametrize("accepted", [False, True])
def test_cli_redraw_reviews_candidates_before_publishing(tmp_path, monkeypatch, accepted):
    root = tmp_path / "series_assets"
    drawn = Draws()
    source = bible()
    FramedAssetFactory(Settings(reuse_existing_assets=True), drawn).build_selected(
        root, source, {"character_001", "character_002"}, set(), prop_ids=set())
    (tmp_path / "story_bible.json").write_text(revised(source).model_dump_json())
    (root / "cards_review.json").write_text(json.dumps({"characters": {
        "character_001": {"name": "甲", "judged_at": 1, "actions": []},
        "character_002": {"name": "乙", "judged_at": 2, "actions": []}},
        "locations": {}, "flags": [], "missing_cards": []}))
    before = snapshot(root)
    judged = []

    def review(candidate, only_ids=None, **kwargs):
        assert only_ids == {"character_001"}
        assert candidate != tmp_path
        # The old active bundle is still untouched when the judge sees new images.
        assert snapshot(root) == before
        frozen = json.loads((candidate / "story_bible.json").read_text())
        assert frozen["characters"][0]["appearance"].startswith("宽脸")
        assert (candidate / "series_assets/characters/character_001/expressions.jpeg").is_file()
        judged.append(candidate)
        report = {"policy": "test-candidate", "characters": {
            "character_001": {"name": "甲", "judged_at": 10 ** 12,
                              "expected_description": describe(Character.model_validate(frozen["characters"][0])),
                              "actions": [] if accepted else ["regenerate"]}},
            "locations": {}, "missing_cards": [],
            "flags": [] if accepted else ["character_001 甲: regenerate"]}
        (candidate / "series_assets/cards_review.json").write_text(json.dumps(report))
        return report

    monkeypatch.setattr("novel_manga.application.review.cards.review_cards", review)
    monkeypatch.setattr(cards, "load_dotenv", lambda _: None)
    monkeypatch.setattr(cards, "load_profile", lambda *a, **kw: {"frame": "9:16", "expressions": True})
    monkeypatch.setattr(cards, "load_genre", lambda _: {})
    monkeypatch.setattr(cards, "load_style", lambda *a: {})
    monkeypatch.setattr(cards.Settings, "from_env", lambda **kw: Settings(reuse_existing_assets=True))
    monkeypatch.setattr(cards, "FramedPhanRouter", lambda *a: drawn)
    monkeypatch.setattr(sys, "argv", ["build_cards_thin.py", "--novel-dir", str(tmp_path),
        "--assets", "character_001", "--redraw", "--review"])

    assert cards.main() == (0 if accepted else 2)
    assert len(judged) == 1
    assert len(drawn.calls) == 6  # No automatic retry after a rejected candidate.
    if not accepted:
        assert snapshot(root) == before
        assert json.loads((judged[0] / "series_assets/cards_review.json").read_text())["flags"]
    else:
        assert (root / "characters/character_001/turnaround.jpeg").read_bytes() != before[
            "characters/character_001/turnaround.jpeg"]
        current_review = json.loads((root / "cards_review.json").read_text())
        assert current_review["characters"]["character_001"]["judged_at"] == 10 ** 12
        assert current_review["characters"]["character_001"]["expected_description"] == describe(
            revised(source).characters[0])
        assert current_review["characters"]["character_002"]["judged_at"] == 2
