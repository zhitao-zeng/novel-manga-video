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
                "alignment": {
                    "events": [{"start": 0.0, "end": 1.8, "text": "雨停了。"}]
                },
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


def test_short_turn_mux_does_not_apply_one_pass_loudnorm(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = Renderer(Settings())
    visual = tmp_path / "visual.mp4"
    audio = tmp_path / "short.wav"
    output = tmp_path / "segment.mp4"
    visual.write_bytes(b"video")
    audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"segment")

    monkeypatch.setattr(render_module, "run", fake_run)
    monkeypatch.setattr(render_module, "media_duration", lambda path: 1.0)

    renderer.mux_turn(visual, audio, output)

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "loudnorm" not in graph
    assert "aresample=48000" in graph


def test_visual_audio_driver_mutes_narration_without_timeline_drift(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = Renderer(Settings())
    audios = [tmp_path / "narration.wav", tmp_path / "dialogue.wav"]
    for audio in audios:
        audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"driver")

    durations = {audios[0]: 2.0, audios[1]: 3.0}
    monkeypatch.setattr(render_module, "run", fake_run)
    monkeypatch.setattr(
        render_module,
        "media_duration",
        lambda path: durations.get(path, 5.1),
    )

    _, seconds, offsets, speed = renderer.compose_visual_group_audio(
        audios,
        tmp_path / "driver.wav",
        audible=[False, True],
        target_seconds=8.0,
    )

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "[0:a]aresample=48000" in graph
    assert "volume=0,asetpts=PTS-STARTPTS[a0]" in graph
    assert "[1:a]aresample=48000" in graph
    assert "volume=0,asetpts=PTS-STARTPTS[a1]" not in graph
    assert "atempo" not in graph
    assert seconds == 5.1
    assert offsets == [0.0, 2.1]
    assert speed == 1.0


def test_delivery_group_audio_peak_normalizes_turns_without_changing_timing(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = Renderer(Settings())
    audios = [tmp_path / "quiet.wav", tmp_path / "normal.wav"]
    for audio in audios:
        audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    def fake_run(command: list[str]):
        commands.append(command)
        Path(command[-1]).write_bytes(b"delivery")

    durations = {audios[0]: 1.4, audios[1]: 2.0}
    monkeypatch.setattr(render_module, "run", fake_run)
    monkeypatch.setattr(
        render_module,
        "media_duration",
        lambda path: durations.get(path, 3.5),
    )
    monkeypatch.setattr(
        renderer,
        "_peak_volume_db",
        lambda path: -37.6 if path == audios[0] else -2.0,
    )

    _, seconds, offsets, speed = renderer.compose_visual_group_audio(
        audios,
        tmp_path / "delivery.wav",
        target_seconds=8.0,
    )

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "volume=34.600dB" in graph
    assert "volume=-1.000dB" in graph
    assert "atempo" not in graph
    assert seconds == 3.5
    assert offsets == [0.0, 1.5]
    assert speed == 1.0
