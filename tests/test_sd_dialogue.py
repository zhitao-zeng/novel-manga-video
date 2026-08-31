import base64
import io
import json
from types import SimpleNamespace

import httpx
from PIL import Image

from novel_manga.config import Settings
from novel_manga.models import (
    AudioBeat,
    CameraBeat,
    CameraPlan,
    MotionBeat,
    PerformancePlan,
    SceneAudioPlan,
    ShotIntent,
)
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.sd_dialogue import (
    PUNCTUATION,
    build_sd_prompt,
    compile_directing_prompt,
    performance_action_only,
    subtitle_pages,
    timed_subtitle_pages,
)


def test_phanrouter_uses_separate_image_and_video_credentials() -> None:
    provider = PhanRouterMediaProvider(
        Settings(
                provider="phanrouter",
                phanrouter_api_key="video-secret",
                phanrouter_image_api_key="image-secret",
                asr_command="/bin/false",
        )
    )
    try:
        assert provider.image_headers["Authorization"] == "Bearer image-secret"
        assert provider.video_headers["Authorization"] == "Bearer video-secret"
    finally:
        provider.client.close()


def test_dialogue_prompt_contains_exact_line_and_single_speaker_constraint() -> None:
    line = "萧炎哥哥，以前你曾经对薰儿说过，要能放下，才能拿起。"
    prompt = build_sd_prompt("xuner", line, "镜头缓慢推进。")
    assert line in prompt
    assert "只有这个角色开口" in prompt
    assert "其他人物保持闭嘴" in prompt
    assert "整个镜头中始终清晰可见" in prompt
    assert "不切镜" in prompt
    assert "不要锁定参考图中的静态姿势" in prompt
    assert "【动作链】" in prompt
    assert "【摄影机轨迹】" in prompt
    assert "【摄影机模式】locked" in prompt
    assert "remains entirely motionless" in prompt
    assert "不摇移相机" not in prompt


def test_prompt_consumes_shot_intent_and_triggered_audio_timeline() -> None:
    prompt = build_sd_prompt(
        "narrator",
        "石碑显出斗之力三段。",
        "人群在结果出现后停止议论。",
        shot_intent=ShotIntent(
            dramatic_function="reveal",
            power_relation="主角被公开压低",
            emotion_target="先压迫再产生疑问",
            information_fact_ids=["fact_001"],
            viewer_focus="石碑结果与萧炎的克制反应",
            retention_beat_id="beat_001",
        ),
        audio_plan=SceneAudioPlan(
            ambience="测试广场低声议论",
            audio_beats=[
                AudioBeat(
                    position_ratio=0.2,
                    cue_type="impact",
                    cue="结果出现时短促冲击",
                    trigger="石碑显出三段",
                    retention_beat_id="beat_001",
                )
            ],
        ),
    )

    assert "功能=reveal" in prompt
    assert "主角被公开压低" in prompt
    assert "石碑结果与萧炎的克制反应" in prompt
    assert "20%处impact" in prompt
    assert "石碑显出三段" in prompt


def test_dialogue_prompt_uses_supplied_locked_identity_description() -> None:
    prompt = build_sd_prompt(
        "测试员",
        "萧炎，斗之力，三段！",
        "固定镜头。",
        actor_description="方脸、短须、深色长袍的中年测试员",
    )

    assert "方脸、短须、深色长袍的中年测试员" in prompt
    assert "萧炎，斗之力，三段！" in prompt


def test_phanrouter_native_audio_payload_uses_official_schema() -> None:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(video_model="sd2.5")

    payload = provider._video_payload(
        "说出台词", "https://example.test/frame.png", 4.2
    )

    assert payload["generate_audio"] is True
    assert payload["model"] == "sd2.5"
    assert payload["duration"] == 5
    assert payload["ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert payload["watermark"] is False
    assert payload["output_format"] == "mp4"
    assert [item["type"] for item in payload["content"]] == ["text", "image_url"]


def test_seedance25_payload_clamps_to_documented_duration_range() -> None:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(video_model="sd2.5")

    short = provider._video_payload(
        "短镜头", "https://example.test/frame.png", 0.2
    )
    long = provider._video_payload(
        "长镜头", "https://example.test/frame.png", 99.0
    )

    assert short["duration"] == 4
    assert long["duration"] == 30
    assert short["generate_audio"] is True

    provider.settings = SimpleNamespace(
        video_model="sd2.5",
        final_audio_policy="native_dialogue",
    )
    native = provider._video_payload(
        "人物直接说出台词", "https://example.test/frame.png", 4.0
    )
    assert native["generate_audio"] is True
    assert all(item.get("type") != "audio_url" for item in native["content"])

def test_seedance25_payload_keeps_ordered_character_and_scene_cards() -> None:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(video_model="sd2.5")

    payload = provider._video_payload(
        "图1是角色卡，图2是场景卡",
        "data:image/jpeg;base64,character",
        4.0,
        ("data:image/jpeg;base64,scene",),
    )

    images = [
        item
        for item in payload["content"]
        if item.get("type") == "image_url"
    ]
    assert [item["image_url"]["url"] for item in images] == [
        "data:image/jpeg;base64,character",
        "data:image/jpeg;base64,scene",
    ]
    assert all(item["role"] == "reference_image" for item in images)


def test_seedance25_payload_supports_text_to_video_without_image() -> None:
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(
        video_model="sd2.5",
        final_audio_policy="native_dialogue",
    )

    payload = provider._video_payload(
        "纯文本生成楚烟儿镜头",
        None,
        4.0,
    )

    assert payload["generate_audio"] is True
    assert payload["content"] == [
        {"type": "text", "text": "纯文本生成楚烟儿镜头"}
    ]


def test_seedance25_can_inline_a_locked_local_reference_image(tmp_path) -> None:
    frame = tmp_path / "frame.png"
    Image.new("RGB", (360, 640), (12, 34, 56)).save(frame)
    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(inline_reference_images=True)

    url = provider._restore_image_url(ImageResult(path=frame))

    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert image.size == (720, 1280)
        assert image.mode == "RGB"


def test_seedream_image_submit_download_and_sanitized_metadata(tmp_path) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 96), (12, 34, 56)).save(reference)
    submitted: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path.endswith("/v1/images/generations")
            assert request.headers["Authorization"] == "Bearer runtime-secret"
            submitted.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"data": [{"url": "https://media.test/frame.jpeg"}]},
            )
        if request.url == httpx.URL("https://media.test/frame.jpeg"):
            buffer = io.BytesIO()
            Image.new("RGB", (108, 192), (65, 43, 21)).save(buffer, format="JPEG")
            return httpx.Response(200, content=buffer.getvalue())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(
        image_model="doubao-seedream-5.0-lite",
        phanrouter_base_url="https://cloud.test/phanrouter",
    )
    provider.image_headers = {"Authorization": "Bearer runtime-secret"}
    provider.client = httpx.Client(transport=httpx.MockTransport(handle), timeout=2.0)
    output = tmp_path / "frame.jpeg"
    try:
        result = provider.create_image("二维国漫画风", output, reference)
    finally:
        provider.client.close()

    assert result.path == output
    assert result.public_url == "https://media.test/frame.jpeg"
    assert submitted["model"] == "doubao-seedream-5.0-lite"
    assert submitted["size"] == "1080x1920"
    assert submitted["watermark"] is False
    assert submitted["image"].startswith("data:image/jpeg;base64,")
    with Image.open(output) as image:
        assert image.size == (108, 192)
    metadata = json.loads(
        output.with_suffix(".jpeg.task.json").read_text(encoding="utf-8")
    )
    assert metadata["model"] == "doubao-seedream-5.0-lite"
    assert "runtime-secret" not in json.dumps(metadata)


def test_seedance25_retries_succeeded_task_until_cdn_file_is_ready(
    tmp_path, monkeypatch
) -> None:
    submitted: dict = {}
    media_requests = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal media_requests
        if request.method == "POST":
            assert request.url.path.endswith("/api/v3/contents/generations/tasks")
            assert request.headers["Authorization"] == "Bearer runtime-secret"
            submitted.update(json.loads(request.content))
            return httpx.Response(200, json={"task_id": "cgt-seedance25-test"})
        if request.url.path.endswith(
            "/api/v3/contents/generations/tasks/cgt-seedance25-test"
        ):
            return httpx.Response(
                200,
                json={
                    "code": "success",
                    "data": {
                        "task_id": "cgt-seedance25-test",
                        "status": "succeeded",
                        "model": "sd2.5",
                        "url": "https://media.test/result.mp4",
                        "output_format": "mp4",
                        "error": None,
                    },
                },
            )
        if request.url == httpx.URL("https://media.test/result.mp4"):
            media_requests += 1
            if media_requests == 1:
                raise httpx.ReadTimeout("slow CDN", request=request)
            if media_requests == 2:
                return httpx.Response(404, content=b"not replicated yet")
            return httpx.Response(200, content=b"seedance25-mp4")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(
        video_model="sd2.5",
        phanrouter_base_url="https://cloud.test/phanrouter",
        poll_timeout=2.0,
    )
    provider.video_headers = {"Authorization": "Bearer runtime-secret"}
    provider.client = httpx.Client(transport=httpx.MockTransport(handle), timeout=2.0)
    monkeypatch.setattr("novel_manga.providers.phanrouter.time.sleep", lambda _: None)
    output = tmp_path / "clip.mp4"
    try:
        provider.create_video(
            "女孩听见脚步声后回头，并逐字说出台词。",
            ImageResult(
                path=tmp_path / "frame.jpeg",
                public_url="https://media.test/frame.jpeg",
            ),
            output,
            duration=8,
        )
    finally:
        provider.client.close()

    assert output.read_bytes() == b"seedance25-mp4"
    assert media_requests == 3
    assert submitted["model"] == "sd2.5"
    assert submitted["watermark"] is False
    assert submitted["output_format"] == "mp4"
    assert [item.get("role") for item in submitted["content"][1:]] == [
        "reference_image",
    ]
    metadata = json.loads(
        output.with_suffix(".mp4.task.json").read_text(encoding="utf-8")
    )
    assert metadata["task_id"] == "cgt-seedance25-test"
    assert metadata["model"] == "sd2.5"
    assert "runtime-secret" not in json.dumps(metadata)


def test_narrator_prompt_keeps_mouths_closed() -> None:
    prompt = build_sd_prompt("narrator", "广场骤然安静。", "人群停止议论。")
    assert "所有人物都不说话" in prompt
    assert "嘴巴自然闭合" in prompt


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


def test_directing_beats_are_retimed_to_real_generated_duration() -> None:
    performance = PerformancePlan(
        objective="从迟疑转为坦白",
        start_state="低头闭嘴",
        motion_beats=[
            MotionBeat(phase="opening", trigger="听见追问", action="抬眼"),
            MotionBeat(phase="development", action="递出故事册"),
            MotionBeat(phase="resolution", action="松开手并停住"),
        ],
        end_state="注视对方",
    )
    camera = CameraPlan(
        start_position="左前方中景",
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory="向右横移",
                framing="保持胸像",
                parallax="书架移动快于远墙",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="短弧线减速",
                framing="停在近景",
                parallax="三层空间逐渐停止",
            ),
        ],
        end_position="右前方近景",
    )

    prompt = compile_directing_prompt(performance, camera, duration=9.0)

    assert "0.0-3.0秒" in prompt
    assert "6.0-9.0秒" in prompt
    assert "0.0-4.5秒" in prompt
    assert "4.5-9.0秒" in prompt


def test_fallback_camera_is_locked_while_actor_motion_stays_directed() -> None:
    prompt = build_sd_prompt(
        "林晚",
        "不要开门。",
        "林晚先抬眼，再抬手挡住门。",
    )

    assert "【摄影机模式】locked" in prompt
    assert "锁定机位" in prompt
    assert "Use action-reaction-action progression" in prompt
    assert "The camera physically moves through the 3D environment" not in prompt


def test_legacy_camera_words_do_not_leak_into_actor_performance() -> None:
    action = performance_action_only(
        "林晚抬眼，镜头缓慢推进，随后她握紧门把手，摄影机向右横移"
    )

    assert action == "林晚抬眼，随后她握紧门把手"


def test_motivated_camera_allows_only_one_trajectory_and_locks_axis() -> None:
    performance = PerformancePlan(
        objective="揭示闯入者",
        start_state="林晚挡在门前",
        motion_beats=[
            MotionBeat(phase="opening", trigger="门被推开", action="林晚侧身让出视线"),
            MotionBeat(phase="resolution", action="她停住并看向闯入者"),
        ],
        end_state="闯入者被清楚揭示",
    )
    camera = CameraPlan(
        mode="motivated_subtle",
        motivation="随人物让位揭示门后闯入者",
        action_axis="门与林晚之间的行动轴同侧",
        screen_direction="林晚保持画面左侧，闯入者从右侧出现",
        start_position="林晚正面中景",
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory="向右短距离横移一次",
                framing="揭示门后人物",
                parallax="门框快于远处走廊移动",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="减速停住",
                framing="保持双人中景",
                parallax="背景停止移动",
            ),
        ],
        end_position="行动轴同侧的双人中景",
    )

    prompt = build_sd_prompt(
        "narrator",
        "门后的人终于出现。",
        "林晚侧身。",
        performance_plan=performance,
        camera_plan=camera,
    )

    assert "【摄影机模式】motivated_subtle" in prompt
    assert "随人物让位揭示门后闯入者" in prompt
    assert "only the single motivated trajectory" in prompt
    assert "Do not add orbiting, a second move" in prompt
