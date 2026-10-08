"""A picture verdict belongs to both the picture and the character it checked."""
import json
import os
from pathlib import Path

import pytest

from novel_manga.application.review import cards
from novel_manga.config import Settings
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult
from novel_manga.review.prompts import describe


def setup_book(tmp_path, *, legacy=False):
    person = Character(name="甲", gender="男", age="青年", appearance="黑发青年，脸有永久疤痕",
                       wardrobe="黑袍", hair="黑色短发", signature_prop="药瓶")
    bible = StoryBible(novel_title="测试", genre="generic", visual_style="二维国漫", palette="蓝",
                       style_fingerprint="fixed", locations=[], characters=[person])
    (tmp_path / "story_bible.json").write_text(bible.model_dump_json())
    root = tmp_path / "series_assets"
    folder = root / "characters/character_001"
    folder.mkdir(parents=True)
    (folder / "turnaround.jpeg").write_bytes(b"unchanged image")
    (folder / "spec.json").write_text(person.model_dump_json())
    entry = {"name": "甲", "judged_at": 10 ** 12, "actions": [], "matches_description": True}
    if not legacy:
        entry["expected_description"] = describe(person)
    (root / "cards_review.json").write_text(json.dumps({"characters": {"character_001": entry},
        "locations": {}, "flags": [], "missing_cards": []}))
    return bible, folder


@pytest.mark.parametrize("legacy", [False, True])
def test_changed_bible_invalidates_an_unchanged_picture_verdict(tmp_path, legacy):
    bible, folder = setup_book(tmp_path, legacy=legacy)
    before_image = (folder / "turnaround.jpeg").stat().st_mtime
    before_report = (tmp_path / "series_assets/cards_review.json").read_bytes()
    assert cards.card_verdict_current(tmp_path, "character_001") is not None
    changed = bible.model_copy(update={"characters": [bible.characters[0].model_copy(update={
        "age": "中年", "appearance": "中年男性，脸有永久疤痕"})]})
    (tmp_path / "story_bible.json").write_text(changed.model_dump_json())

    assert cards.card_verdict_current(tmp_path, "character_001") is None
    assert (folder / "turnaround.jpeg").stat().st_mtime == before_image
    assert (tmp_path / "series_assets/cards_review.json").read_bytes() == before_report


@pytest.mark.parametrize("legacy", [False, True])
def test_voice_profile_changes_do_not_invalidate_a_visual_verdict(tmp_path, legacy):
    bible, _ = setup_book(tmp_path, legacy=legacy)
    changed = bible.model_copy(update={"characters": [bible.characters[0].model_copy(update={
        "voice_profile_id": "different-reference-voice", "motion_signature": "从容行走"})]})
    (tmp_path / "story_bible.json").write_text(changed.model_dump_json())
    assert cards.card_verdict_current(tmp_path, "character_001") is not None


def test_incomplete_legacy_spec_does_not_claim_current_source_was_reviewed(tmp_path):
    _, folder = setup_book(tmp_path, legacy=True)
    (folder / "spec.json").write_text(json.dumps({"name": "甲", "appearance": "黑发青年"}))
    assert cards.card_verdict_current(tmp_path, "character_001") is None


def test_new_review_records_the_exact_description_sent_to_the_judge(tmp_path, monkeypatch):
    bible, _ = setup_book(tmp_path)
    seen = []
    monkeypatch.setattr(cards, "load_profile", lambda *a: {})
    monkeypatch.setattr(cards, "load_genre", lambda *a: {})

    def judge(person, views):
        seen.append(describe(person))
        return {"photoreal": 0, "same_person": True, "matches_description": True,
                "text_or_extra_people": False}

    monkeypatch.setattr(cards.review_judges, "judge_character_cards", judge)
    report = cards.review_cards(tmp_path, only_ids={"character_001"})
    assert report["characters"]["character_001"]["expected_description"] == seen[0] == describe(bible.characters[0])
    assert cards.card_verdict_current(tmp_path, "character_001") is not None


@pytest.mark.parametrize("change,still_current", [({"wardrobe": "红袍"}, False),
                                                 ({"voice_profile_id": "new-voice"}, True)])
def test_legacy_cache_after_normal_build_keeps_its_original_spec_evidence(tmp_path, change, still_current):
    bible, folder = setup_book(tmp_path, legacy=True)
    bible = bible.model_copy(update={"characters": [bible.characters[0].model_copy(update={
        "base_costume": "黑袍"})]})
    (tmp_path / "story_bible.json").write_text(bible.model_dump_json())

    class Draws:
        calls = 0

        def create_image(self, prompt, output, **kw):
            self.calls += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"generated main card")
            return ImageResult(path=output)

    provider = Draws()
    FramedAssetFactory(Settings(), provider).build_selected(tmp_path / "series_assets", bible,
        {"character_001"}, set(), expressions=False, prop_ids=set())
    os.utime(folder / "turnaround.jpeg", (1000, 1000))
    os.utime(folder / "spec.json", (1000, 1000))
    review_path = tmp_path / "series_assets/cards_review.json"
    record = json.loads(review_path.read_text())
    record["characters"]["character_001"]["judged_at"] = 1001
    review_path.write_text(json.dumps(record))
    original_spec = (folder / "spec.json").read_bytes()
    changed = bible.model_copy(update={"characters": [bible.characters[0].model_copy(update=change)]})
    (tmp_path / "story_bible.json").write_text(changed.model_dump_json())

    FramedAssetFactory(Settings(reuse_existing_assets=True), provider).build_selected(
        tmp_path / "series_assets", changed, {"character_001"}, set(), expressions=False, prop_ids=set())

    assert provider.calls == 1
    assert (folder / "spec.json").read_bytes() == original_spec
    assert (folder / "spec.json").stat().st_mtime == 1000
    assert (cards.card_verdict_current(tmp_path, "character_001") is not None) is still_current


def test_spec_written_after_a_legacy_judgment_is_not_its_input_proof(tmp_path):
    _, folder = setup_book(tmp_path, legacy=True)
    review_path = tmp_path / "series_assets/cards_review.json"
    record = json.loads(review_path.read_text())
    record["characters"]["character_001"]["judged_at"] = 1001
    review_path.write_text(json.dumps(record))
    os.utime(folder / "turnaround.jpeg", (1000, 1000))
    os.utime(folder / "spec.json", (1002, 1002))
    assert cards.card_verdict_current(tmp_path, "character_001") is None
