"""An expression fallback retains source identity and its main-card reference."""
import json
from pathlib import Path
import sys

from PIL import Image

from novel_manga.application.assets import phase_cards
from novel_manga.config import Settings
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_policy import SEEDREAM_BACKGROUND, SEEDREAM_FRAMING
from novel_manga.media.asset_prompts import character_prompt, expression_prompt
from novel_manga.models.bible import Character, StoryBible
from novel_manga.providers.base import ImageResult


def source():
    return StoryBible(novel_title="测试", genre="generic", visual_style="二维国漫", palette="蓝",
        style_fingerprint="fixed", locations=[], characters=[Character(
            name="科查", gender="男", age="实际一百多岁；外貌年龄未明确",
            appearance="矮小瘦削，脸至胸脯布满红白黑疤痕，鼻子和耳朵只余黑色窟窿，牙齿发黑破烂",
            wardrobe="破洞宽大黑袍", base_costume="布满药汁色斑的破洞宽大黑袍", hair="稀疏暗色短发")])


class Draws:
    def __init__(self):
        self.calls = []

    def create_image(self, prompt, output, **kwargs):
        self.calls.append({"prompt": prompt, "output": output, **kwargs})
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), "blue").save(output)
        return ImageResult(path=output)


class RefusesExpressions(Draws):
    def create_image(self, prompt, output, **kwargs):
        if output.name == "expressions.jpeg":
            self.calls.append({"prompt": prompt, "output": output, **kwargs})
            raise RuntimeError("Your input was blocked by safety review")
        return super().create_image(prompt, output, **kwargs)


def test_primary_and_expression_prompts_carry_literal_source_identity():
    bible = source()
    person = bible.characters[0]
    primary = character_prompt(bible, person.name, person.appearance, person.base_costume,
                               gender=person.gender, age=person.age)
    expression = expression_prompt(bible, person.name, character=person)
    for prompt in (primary, expression):
        assert "性别：男" in prompt
        assert person.age in prompt and person.appearance in prompt
        assert person.base_costume in prompt
    assert "胸像" in expression


def test_expression_refusal_fallback_keeps_primary_reference_and_bust_framing(tmp_path, monkeypatch):
    ordinary, fallback = RefusesExpressions(), Draws()
    monkeypatch.setattr("novel_manga.providers.phanrouter.PhanRouterMediaProvider", lambda *a, **kw: fallback)
    bible = source()
    root = tmp_path / "series_assets"
    FramedAssetFactory(Settings(), ordinary).build_selected(root, bible, {"character_001"}, set(), prop_ids=set())

    assert len(ordinary.calls) == 3  # Main card, refused expression, toned-down retry.
    assert len(fallback.calls) == 1
    request = fallback.calls[0]
    primary = root / "characters/character_001/turnaround.jpeg"
    assert request["reference"] == primary and primary.is_file()
    assert "性别：男" in request["prompt"]
    assert bible.characters[0].age in request["prompt"]
    assert "鼻子和耳朵只余黑色窟窿" in request["prompt"]
    assert "胸像" in request["prompt"]
    assert SEEDREAM_FRAMING not in request["prompt"]
    assert "头顶到鞋底" not in request["prompt"] and "全身站立像" not in request["prompt"]
    assert request["prompt"].endswith(SEEDREAM_BACKGROUND)
    record = json.loads((root / "characters/character_001/expressions.jpeg.request.json").read_text())
    assert record["image_model"] == "doubao-seedream-4.5" and record["reference_sha256"]


def test_existing_phase_expression_uses_current_character_facts(tmp_path, monkeypatch):
    bible = source()
    (tmp_path / "story_bible.json").write_text(bible.model_dump_json())
    root = tmp_path / "series_assets"
    card = root / "characters/character_001-scar/turnaround.jpeg"
    card.parent.mkdir(parents=True)
    Image.new("RGB", (16, 16), "blue").save(card)
    (root / "phases.json").write_text(json.dumps({"characters": {"科查": [
        {"from": 1, "to": 10, "asset_id": "character_001-scar", "label": "永久毁容"}]}}))
    drawn = Draws()
    monkeypatch.setattr(phase_cards, "load_dotenv", lambda _: None)
    monkeypatch.setattr(phase_cards, "load_profile", lambda *a, **kw: {"frame": "9:16"})
    monkeypatch.setattr(phase_cards, "load_genre", lambda _: {})
    monkeypatch.setattr(phase_cards, "load_style", lambda *a: {})
    monkeypatch.setattr(phase_cards.Settings, "from_env", lambda **kw: Settings())
    monkeypatch.setattr(phase_cards, "FramedPhanRouter", lambda *a: drawn)
    monkeypatch.setattr(sys, "argv", ["build_phase_cards.py", "--novel-dir", str(tmp_path),
                                     "--only", "科查", "--expressions"])

    assert phase_cards.main() == 0
    assert len(drawn.calls) == 1
    assert drawn.calls[0]["reference"] == card
    assert "性别：男" in drawn.calls[0]["prompt"]
    assert "鼻子和耳朵只余黑色窟窿" in drawn.calls[0]["prompt"]


def test_legacy_expression_prompt_call_remains_supported():
    prompt = expression_prompt(source(), "科查", "克制")
    assert "胸像" in prompt and "克制" in prompt
