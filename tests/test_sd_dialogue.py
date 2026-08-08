import base64
import io
import wave
from types import SimpleNamespace

from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.sd_dialogue import PUNCTUATION, build_sd_prompt, subtitle_pages, timed_subtitle_pages


def test_dialogue_prompt_contains_exact_line_and_single_speaker_constraint() -> None:
    line = "萧炎哥哥，以前你曾经对薰儿说过，要能放下，才能拿起。"
    prompt = build_sd_prompt("xuner", line, "镜头缓慢推进。")
    assert line in prompt
    assert "只有这个角色开口" in prompt
    assert "其他人物保持闭嘴" in prompt
    assert "整个镜头中始终清晰可见" in prompt
    assert "不切镜" in prompt


def test_reference_audio_prompt_removes_silent_instruction_and_requires_exact_sync() -> None:
    line = "我现在，还有资格让你这么叫吗？"
    prompt = build_sd_prompt("xiaoyan", line, "人物抬眼。", use_reference_audio=True)
    assert line in prompt
    assert "参考音频1" in prompt
    assert "口型必须与参考音频逐字同步" in prompt
    assert "静音期间嘴巴保持自然闭合" in prompt
    assert "人声结束后立即闭嘴" in prompt
    assert "不生成声音" not in prompt


def test_dialogue_prompt_uses_supplied_locked_identity_description() -> None:
    prompt = build_sd_prompt(
        "测试员",
        "萧炎，斗之力，三段！",
        "固定镜头。",
        use_reference_audio=True,
        actor_description="方脸、短须、深色长袍的中年测试员",
    )

    assert "方脸、短须、深色长袍的中年测试员" in prompt
    assert "萧炎，斗之力，三段！" in prompt


def test_phanrouter_reference_audio_payload_uses_official_schema(tmp_path) -> None:
    audio = tmp_path / "line.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 128)
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(video_model="sd2.0")

    payload, digest = provider._video_payload(
        "说出台词", "https://example.test/frame.png", 4.2, audio
    )

    assert payload["generate_audio"] is True
    assert payload["duration"] == 5
    assert payload["content"][-1]["type"] == "audio_url"
    assert payload["content"][-1]["role"] == "reference_audio"
    assert payload["content"][-1]["audio_url"]["url"].startswith("data:audio/wav;base64,")
    assert digest


def test_phanrouter_pads_short_wav_only_for_reference_payload(tmp_path) -> None:
    audio = tmp_path / "short.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x00\x00" * 12000)
    original = audio.read_bytes()

    content, _ = PhanRouterMediaProvider._reference_audio_content(audio)

    encoded = content["audio_url"]["url"].split(",", 1)[1]
    with wave.open(io.BytesIO(base64.b64decode(encoded)), "rb") as stream:
        assert stream.getnframes() / stream.getframerate() >= 2.0
    assert audio.read_bytes() == original


def test_narrator_prompt_keeps_mouths_closed() -> None:
    prompt = build_sd_prompt("narrator", "广场骤然安静。", "人群停止议论。")
    assert "所有人物都不说话" in prompt
    assert "嘴巴自然闭合" in prompt


def test_reference_audio_narrator_is_voiceover_without_character_lip_motion() -> None:
    prompt = build_sd_prompt(
        "narrator", "广场骤然安静。", "人群停止议论。", use_reference_audio=True
    )
    assert "唯一画外旁白" in prompt
    assert "画中人物不得开口或随旁白做口型" in prompt
    assert "不生成声音" not in prompt


def test_subtitle_pages_have_at_most_two_lines_and_no_punctuation_only_page() -> None:
    text = "萧家测试广场上，魔石碑亮起刺眼的光。曾经的第一天才萧炎，正等着命运的宣判。"
    pages = subtitle_pages(text)
    assert all(page.count(r"\N") <= 1 for page in pages)
    assert all(any(char not in PUNCTUATION + r"\N" for char in page) for page in pages)


def test_timed_pages_cover_exact_interval_proportionally() -> None:
    events = timed_subtitle_pages("这是很长的一句话，需要被正确切分，而且不能只剩下一个句号。", 4.0, 10.0)
    assert events[0]["start"] == 4.0
    assert events[-1]["end"] == 10.0
    assert all(event["end"] > event["start"] for event in events)
