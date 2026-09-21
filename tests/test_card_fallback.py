"""A card gpt-image-2 will not draw at all, and the three things that hid it.

Five characters of 在美漫当心灵导师的日子 are trademarked - 尼克·弗瑞, 史蒂芬·斯特兰奇,
查尔斯·泽维尔, 托尔, 毒液 - and gpt-image-2 refuses every one of them.  The refusal is about
who is in the picture, so the toned-down retry cannot reach it: sixteen chapters failed on
those five, each paying for both attempts on the way down, and the batch log said only

    [00:38:37] ch1: card build FAILED: Traceback (most recent call last):

four times over, because the scan for the failing line ran backwards and matched the banner
that opens a traceback instead of the line that closes it.

Seedream draws all five from the same prompt.  The code to call it was already here and had
never run: it asked for the frame size the other backends share, 1080x1920, which is under
the pixel floor Seedream enforces, so the request would have come back 400 on the size
parameter before the model saw a word of the prompt.
"""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_policy import (ModerationRejected, SEEDREAM_FRAMING,
                                            refusal_text, seedream_prompt)
from novel_manga.application.production.flow import failure_line
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter_images import SEEDREAM_DIMENSIONS, SEEDREAM_PIXEL_FLOOR

# What the service actually answers, from outputs/meiman-daoshi/meiman-daoshi_1/render.log.
REFUSAL = ("image generation failed: {'createdAt': '2026-09-22 00:37:25', 'error': {'message': "
           "'Your input or generated content was blocked by safety review. Please revise your "
           "input and try again.'}, 'format': 'png', 'metadata': None, 'status': 'failed', "
           "'taskId': 'task_20260922003725_c9g2irlg', 'updatedAt': '2026-09-22 00:38:08', 'url': ''}")

RENDER_LOG_TAIL = [
    "  File \"/mnt/disk1/zengzhitao/novel-manga-video/src/novel_manga/media/asset_builder.py\", line 63, in ensure_card",
    "    return ensure_image(self.settings, self.provider, safe, output, reference=reference)",
    "RuntimeError: " + REFUSAL,
    "",
    "The above exception was the direct cause of the following exception:",
    "",
    "Traceback (most recent call last):",
    "  File \"/mnt/disk1/zengzhitao/novel-manga-video/scripts/render_clips_thin.py\", line 10, in <module>",
    "    raise SystemExit(main())",
    "novel_manga.media.asset_policy.ModerationRejected: character_008/turnaround.jpeg: " + REFUSAL,
]


def test_the_reported_line_is_the_exception_not_the_banner_above_it():
    reported = failure_line(RENDER_LOG_TAIL)
    assert reported.startswith("novel_manga.media.asset_policy.ModerationRejected: character_008")
    assert "Traceback" not in reported


def test_a_run_that_failed_without_a_traceback_still_reports_nothing_rather_than_noise():
    assert failure_line(["building cards", "cards ready; review sheet.jpg"]) == ""


def test_a_refusal_keeps_the_sentence_the_service_sent():
    """Cutting the first 200 characters kept the timestamp and dropped the reason."""
    assert refusal_text(RuntimeError(REFUSAL)) == (
        "Your input or generated content was blocked by safety review. "
        "Please revise your input and try again."
    )


def test_a_refusal_with_no_service_record_falls_back_to_its_last_line():
    assert refusal_text(RuntimeError("first line\nHTTP 500: upstream gone")) == "HTTP 500: upstream gone"


@pytest.mark.parametrize("ratio", sorted(SEEDREAM_DIMENSIONS))
def test_the_seedream_frame_clears_the_pixel_floor_and_keeps_the_ratio(ratio):
    """1080x1920 is 2,073,600 pixels and Seedream wants 3,686,400: a 400 before the prompt."""
    width, height = SEEDREAM_DIMENSIONS[ratio]
    assert width * height >= SEEDREAM_PIXEL_FLOOR
    wanted_w, wanted_h = (int(part) for part in ratio.split(":"))
    assert width * wanted_h == height * wanted_w


class Refuses:
    """gpt-image-2 on one of the five: it refuses the prompt and the softened prompt alike."""

    def __init__(self):
        self.prompts = []

    def create_image(self, prompt, output, **kwargs):
        self.prompts.append(prompt)
        raise RuntimeError(REFUSAL)


class Draws:
    """Seedream: draws whatever it is given, and remembers how it was asked."""

    def __init__(self):
        self.calls = []

    def create_image(self, prompt, output, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), "black").save(output)
        return ImageResult(path=output)


@pytest.fixture
def card(monkeypatch):
    """A factory whose first model refuses and whose fallback is watched."""
    drawn = Draws()
    monkeypatch.setattr("novel_manga.providers.phanrouter.PhanRouterMediaProvider",
                        lambda settings, **kwargs: drawn)
    with tempfile.TemporaryDirectory(prefix="nmv-card-fallback-") as tmp:
        yield Path(tmp), Refuses(), drawn


def test_a_card_refused_twice_is_drawn_by_the_fallback_model(card):
    root, refused, drawn = card
    factory = FramedAssetFactory(Settings(), refused)
    result = factory.ensure_card("角色资产：尼克·弗瑞；固定外貌：光头，独眼，戴眼罩",
                                 root / "character_008" / "turnaround.jpeg", aspect_ratio="9:16")
    assert result.path.is_file()
    assert len(refused.prompts) == 2                     # the prompt, then the toned-down one
    assert len(drawn.calls) == 1


def test_the_card_records_which_model_drew_it(card):
    """A card from the fallback must not be indistinguishable from the other 24."""
    import json
    root, refused, _ = card
    output = root / "character_008" / "turnaround.jpeg"
    FramedAssetFactory(Settings(), refused).ensure_card("画尼克·弗瑞", output, aspect_ratio="9:16")
    written = json.loads(output.with_suffix(output.suffix + ".request.json").read_text(encoding="utf-8"))
    assert written["image_model"] == "doubao-seedream-4.5"


def test_the_fallback_is_given_no_reference(card):
    """With a reference Seedream redraws instead of borrowing a look, and the figure came
    back cut off at the waist.  The style has to ride on the prompt alone."""
    root, refused, drawn = card
    factory = FramedAssetFactory(Settings(), refused)
    factory.ensure_card("画尼克·弗瑞", root / "c" / "turnaround.jpeg",
                        reference=root / "style_master.jpeg", aspect_ratio="9:16")
    assert drawn.calls[0].get("reference") is None


def test_the_fallback_prompt_leads_with_framing_and_ends_with_background(card):
    """Order is the whole point: a softly worded framing note cropped harder than silence,
    and a background note placed before the style direction simply loses to it."""
    root, refused, drawn = card
    FramedAssetFactory(Settings(), refused).ensure_card(
        "画尼克·弗瑞", root / "c" / "turnaround.jpeg", aspect_ratio="9:16")
    sent = drawn.calls[0]["prompt"]
    assert sent.startswith(SEEDREAM_FRAMING)
    assert sent.endswith("不作用在背景上。")
    assert sent.index("画尼克·弗瑞") > sent.index(SEEDREAM_FRAMING[:8])


def test_the_original_prompt_is_what_the_fallback_gets_not_the_scrubbed_one(card):
    """The scrub targets suggestive wording; it has nothing to do with why these were
    refused, and passing its output on would quietly change what the card shows."""
    root, refused, drawn = card
    FramedAssetFactory(Settings(), refused).ensure_card(
        "身材妩媚的角色", root / "c" / "turnaround.jpeg", aspect_ratio="9:16")
    assert "妩媚" in drawn.calls[0]["prompt"]
    assert "妩媚" not in refused.prompts[1]


def test_an_empty_fallback_setting_gives_up_the_way_it_used_to(card):
    root, refused, drawn = card
    factory = FramedAssetFactory(replace(Settings(), card_fallback_image_model=""), refused)
    with pytest.raises(ModerationRejected) as refusal:
        factory.ensure_card("画尼克·弗瑞", root / "c" / "turnaround.jpeg", aspect_ratio="9:16")
    assert "blocked by safety review" in str(refusal.value)
    assert not drawn.calls


def test_a_fallback_that_also_refuses_names_both_models(card):
    root, refused, _ = card
    def refuses_too(settings, **kwargs):
        return Refuses()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("novel_manga.providers.phanrouter.PhanRouterMediaProvider", refuses_too)
    try:
        with pytest.raises(ModerationRejected) as refusal:
            FramedAssetFactory(Settings(), refused).ensure_card(
                "画尼克·弗瑞", root / "c" / "turnaround.jpeg", aspect_ratio="9:16")
    finally:
        monkeypatch.undo()
    assert "doubao-seedream-4.5 also failed" in str(refusal.value)


def test_a_non_moderation_error_is_not_sent_to_the_fallback(card):
    """A timeout is this call's problem; paying a second model for it would hide it."""
    root, _, drawn = card

    class TimesOut:
        def create_image(self, prompt, output, **kwargs):
            raise RuntimeError("image task timed out: task_20260922003725_c9g2irlg")

    with pytest.raises(RuntimeError, match="timed out"):
        FramedAssetFactory(Settings(), TimesOut()).ensure_card(
            "画尼克·弗瑞", root / "c" / "turnaround.jpeg", aspect_ratio="9:16")
    assert not drawn.calls


def test_the_wrapper_is_a_pure_function_of_the_prompt():
    assert seedream_prompt("甲") == SEEDREAM_FRAMING + "甲" + seedream_prompt("")[len(SEEDREAM_FRAMING):]
