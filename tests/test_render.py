from pathlib import Path

import novel_manga.render as render_module
from PIL import Image
from novel_manga.config import Settings
from novel_manga.render import Renderer, TimedSubtitle


def test_cover_uses_text_free_plate_and_writes_exact_submission_jpeg(
    tmp_path: Path,
) -> None:
    settings = Settings(width=1080, height=1920)
    renderer = Renderer(settings)
    background = tmp_path / "plate.jpg"
    output = tmp_path / "cover.jpg"
    Image.new("RGB", (1080, 1920), (110, 125, 150)).save(background, "JPEG")

    renderer.make_cover(
        background,
        output,
        novel_title="斗破苍穹",
        art_title="陨落的天才",
        episode_label="第01集",
    )

    with Image.open(output) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1920)
        assert image.getpixel((60, 80)) != (110, 125, 150)


def test_production_subtitles_use_outline_without_opaque_mask(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = Renderer(Settings())
    intro = tmp_path / "intro.jpeg"
    ending = tmp_path / "ending.jpeg"
    segment = tmp_path / "segment.mp4"
    for path in (intro, ending, segment):
        path.write_bytes(b"fixture")
    monkeypatch.setattr(
        renderer,
        "_silent_card_segment",
        lambda image, output, duration: image,
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"generated")

    monkeypatch.setattr(render_module, "run", fake_run)
    (tmp_path / "work").mkdir()
    renderer.assemble_production(
        intro,
        ending,
        [
            {
                "unit_id": "shot_001_turn_01",
                "role": "narrator",
                "segment": str(segment),
                "duration": 2.0,
                "subtitle_events": [
                    {
                        "unit_id": "shot_001_turn_01",
                        "role": "narrator",
                        "start": 0.0,
                        "end": 1.8,
                        "text": "雨停了。",
                        "subtitle_source": "native_audio_asr",
                    }
                ],
            }
        ],
        tmp_path / "final.mp4",
        tmp_path / "work",
    )

    subtitle_command = next(command for command in commands if "-vf" in command)
    video_filter = subtitle_command[subtitle_command.index("-vf") + 1]
    assert "ass=" in video_filter
    assert "drawbox" not in video_filter
    ass = (tmp_path / "work/subtitles.ass").read_text(encoding="utf-8")
    assert "&H00000000" in ass


def test_subtitle_style_remains_at_most_two_lines(tmp_path: Path) -> None:
    renderer = Renderer(Settings())
    ass = renderer.write_ass_pages(
        tmp_path / "subtitles.ass",
        [TimedSubtitle(start=0.0, end=2.0, text=r"第一行\N第二行")],
    )

    content = ass.read_text(encoding="utf-8")
    assert r"第一行\N第二行" in content
    assert "BorderStyle" in content
