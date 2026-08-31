#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.render import Renderer, TimedSubtitle
from novel_manga.util import atomic_write_json, media_duration


ROOT = Path(
    "outputs/ftj-anime-api10-v1/ftj-anime-api10/"
    "ftj-anime-api10_1_live_action_sample_v2_fullcard"
)
WORK = ROOT / "work"
KEYFRAMES = WORK / "keyframes"
RAW_VIDEO = WORK / "raw_video"
SEGMENTS = WORK / "segments"
FINAL = ROOT / "ftj_live_action_chapter1_probe_v2_fullcard.mp4"

SERIES_ROOT = Path("outputs/ftj-anime-api10-v1/ftj-anime-api10/series_assets")
CHU_YAN_CARD = (
    SERIES_ROOT
    / "characters/character_001/versions/live_action_character_card_v2/"
    "review_character_card.png"
)
CHU_YANER_CARD = (
    SERIES_ROOT
    / "characters/character_002/versions/live_action_character_card_v1/"
    "review_character_card.png"
)
EXAMINER_CARD = (
    SERIES_ROOT
    / "characters/character_007/versions/live_action_character_card_v1/"
    "review_character_card.png"
)
SQUARE_CARD = (
    SERIES_ROOT
    / "locations/location_001/versions/live_action_location_card_v1/"
    "review_location_card.png"
)


RESULT_LINE = "战之气：九段！级别：高级！"
LONG_LINE = (
    "烟儿小姐，半年之后，你应该便能凝聚战气之旋。"
    "如果你成功的话，那么以十四岁年龄成为一名真正的战者，"
    "你是楚家百年内的第二人！"
)
THANKS_LINE = "谢谢。"
BROTHER_LINE = "楚焱哥哥。"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def image_prompt(
    *,
    subject: str,
    composition: str,
    action_start: str,
) -> str:
    return (
        "GPT Image 2双参考仿真人AI短剧关键帧。图1只锁定同一虚构数字演员的脸、年龄、发型、"
        "身材和固定服装；图2只锁定楚家广场的建筑、黑色灵碑、石板地面、山向和左上方日光。"
        "把图1演员自然放入图2，绝不继承角色卡或场景卡的排版、文字、边框和多视图。"
        f"主体：{subject}。构图：{composition}。动作开始前一瞬：{action_start}。"
        "中国古装仿真人AI短剧正片截图，真实人物比例和真人短剧镜头语言，但明确是虚构数字演员；"
        "皮肤为干净哑光的轻微电影概念绘制质感，没有可识别真人照片的毛孔与摄影写实纹理；"
        "粗布与木石材质清楚，克制电影光线，整体接近真人但仍可辨认为AI影视角色，"
        "人物闭嘴，只有一个具名角色，画面明亮清楚。禁止换脸、改变发型服装、增加演员、"
        "现代物品、盔甲、华服、动漫、插画、2.5D、游戏CG、磨皮网红脸、文字、数字、"
        "牌匾字、字幕、Logo和水印。"
    )


KEYFRAME_SPECS = {
    "yaner_walk": {
        "reference": CHU_YANER_CARD,
        "additional": (SQUARE_CARD,),
        "prompt": image_prompt(
            subject="楚烟儿，同一位紫衣年轻演员，全身进入广场表演区",
            composition="9:16纵向中全景，楚烟儿位于左侧三分线，灵碑在右后方，前方留出行走空间",
            action_start="她刚迈出轻盈但真实的一步，紫色长裙和高马尾自然垂落",
        ),
    },
    "examiner": {
        "reference": EXAMINER_CARD,
        "additional": (SQUARE_CARD,),
        "prompt": image_prompt(
            subject="中年测验员，同一位方脸短发灰鬓演员，深灰暗红滚边长袍",
            composition="9:16纵向胸像，人物在右侧三分线，面向画外左侧楚烟儿，灵碑边缘虚化",
            action_start="他刚抬眼准备宣读结果，神情漠然，双唇闭合",
        ),
    },
    "yaner_closeup": {
        "reference": CHU_YANER_CARD,
        "additional": (SQUARE_CARD,),
        "prompt": image_prompt(
            subject="楚烟儿，同一位紫衣高马尾年轻演员，清冷平静",
            composition="9:16纵向四分之三胸像，人物在左侧三分线，视线朝画外右侧测验员，嘴部无遮挡",
            action_start="她安静听着测验结果，目光稳定，肩线放松，双唇闭合",
        ),
    },
    "chuyan_reaction": {
        "reference": CHU_YAN_CARD,
        "additional": (SQUARE_CARD,),
        "prompt": image_prompt(
            subject="楚焱，同一位短发灰蓝粗布长袍年轻演员，站在人群后排但画面只保留他一人",
            composition="9:16纵向四分之三胸像，人物位于右侧三分线，远处灵碑虚化，视线朝画外左侧",
            action_start="他低垂目光，听见楚烟儿成绩后短暂停顿，双唇闭合",
        ),
    },
    "stele_clean": {
        "reference": SQUARE_CARD,
        "additional": (),
        "prompt": (
            "GPT Image 2真人短剧剧情关键帧。输入是一张楚家广场场景设定卡，只继承同一个"
            "黑色灵碑、石质底座、广场建筑、石板、山向和左上方白日日光；绝不继承卡片排版、"
            "文字、边框、色卡或多视图。输出单张9:16真人古装短剧正片插入镜：黑色灵碑与底座"
            "占画面中央，表面只有自然暗金矿物细纹，背景广场轻微虚化。无人物、无可读文字、"
            "无数字、无符号、无发光结果、无字幕、无Logo和水印，不得输出设定卡或拼图。"
        ),
    },
}


NO_MUSIC = (
    "音频必须只有干净普通话人声、必要同步音效和稳定低声的广场环境声；"
    "绝对禁止BGM、配乐、旋律、鼓点、吟唱、音乐过门和情绪音乐。"
)


VIDEO_SPECS = [
    {
        "name": "opening_walk",
        "keyframe": "yaner_walk",
        "duration": 4.0,
        "native_audio": True,
        "prompt": (
            "4秒9:16真人古装短剧连续镜头。楚烟儿在明亮楚家广场中从左向灵碑轻步走近，"
            "只走两步后自然停住；摄影机沿同侧做一次极慢短距离跟移，服装和高马尾有真实惯性。"
            "同一演员、同一紫衣、同一广场，不增加人物，不切镜，不出现文字。"
            "不说话，只生成连续稳定的白日广场微风与远处低声人群环境，" + NO_MUSIC
        ),
    },
    {
        "name": "result_announcement",
        "keyframe": "examiner",
        "duration": 5.0,
        "native_audio": True,
        "prompt": (
            f"5秒9:16真人古装短剧对话近景。只有中年测验员可见并自然说：‘{RESULT_LINE}’。"
            "标准普通话，权威、克制、清晰，不加词。说话时只有自然呼吸、轻微下颌和一次抬眼，"
            "句末闭嘴停住0.4秒；摄影机固定，嘴部无遮挡。同一演员、服装、日光和广场，"
            "不新增人物、不切镜、不出现文字。" + NO_MUSIC
        ),
    },
    {
        "name": "examiner_long_master",
        "keyframe": "examiner",
        "duration": 14.0,
        "native_audio": True,
        "prompt": (
            f"14秒9:16真人古装短剧对话母带。只有中年测验员可见并完整自然说：‘{LONG_LINE}’。"
            "标准普通话，恭敬但仍有长辈权威，语速自然，不加词、不重复。人物保持胸像，"
            "只有自然呼吸、下颌与一次轻微点头，句末闭嘴停住；摄影机固定。"
            "同一演员、服装、日光和广场，不新增人物、不切镜、不出现文字。" + NO_MUSIC
        ),
    },
    {
        "name": "stele_broll",
        "keyframe": "stele_clean",
        "duration": 4.0,
        "native_audio": False,
        "prompt": (
            "4秒9:16真人古装短剧无声插入镜。固定同一楚家黑色测验灵碑与石质底座，"
            "日光下金色矿物细纹只发生一次极轻的亮度变化，摄影机缓慢横移少许后停住。"
            "无人物、无声音、无文字、无数字、无符号、无BGM、无新增物体。"
        ),
    },
    {
        "name": "yaner_reaction_broll",
        "keyframe": "yaner_closeup",
        "duration": 4.0,
        "native_audio": False,
        "prompt": (
            "4秒9:16真人古装短剧无声反应镜。楚烟儿安静听画外测验员说话，先稳定注视，"
            "随后极轻地点头一次，表情仍平静，不喜形于色；摄影机做一次约5%的极慢推近。"
            "全程闭嘴，无声音、无BGM，不新增人物、不切镜、不改变脸、发型、紫衣和日光。"
        ),
    },
    {
        "name": "chuyan_reaction_broll",
        "keyframe": "chuyan_reaction",
        "duration": 4.0,
        "native_audio": False,
        "prompt": (
            "4秒9:16真人古装短剧无声反应镜。楚焱在广场后排听见楚烟儿受赞誉，"
            "目光短暂停住后缓慢移向远处灵碑，呼吸克制，嘴角没有笑；摄影机固定。"
            "全程闭嘴，无声音、无BGM，不新增人物、不切镜、不改变短发、灰蓝粗布衣和日光。"
        ),
    },
    {
        "name": "yaner_thanks",
        "keyframe": "yaner_closeup",
        "duration": 4.0,
        "native_audio": True,
        "prompt": (
            f"4秒9:16真人古装短剧对话近景。只有楚烟儿可见并自然轻声说：‘{THANKS_LINE}’。"
            "标准普通话，平静克制，不加词；只做一次轻微点头，句末闭嘴。摄影机固定，"
            "嘴部无遮挡，同一演员、紫衣、广场与日光，不新增人物、不切镜、不出现文字。" + NO_MUSIC
        ),
    },
    {
        "name": "yaner_approach",
        "keyframe": "yaner_walk",
        "duration": 4.0,
        "native_audio": False,
        "prompt": (
            "4秒9:16真人古装短剧无声过渡镜。楚烟儿从灵碑旁转身，朝画外楚焱方向走两步，"
            "裙摆与高马尾自然响应，最后在画面左侧停住；摄影机同侧短距离跟移。"
            "全程闭嘴，无声音、无BGM，不新增人物、不改变脸、紫衣、广场和日光，不切镜。"
        ),
    },
    {
        "name": "yaner_brother",
        "keyframe": "yaner_closeup",
        "duration": 4.0,
        "native_audio": True,
        "prompt": (
            f"4秒9:16真人古装短剧对话近景。只有楚烟儿可见，她望向画外楚焱，"
            f"恭敬而温暖地自然说：‘{BROTHER_LINE}’。标准普通话，不加词。"
            "先轻微弯腰，再抬眼说话，句末闭嘴停住；摄影机约5%极慢推近。"
            "同一演员、紫衣、广场与日光，不新增人物、不切镜、不出现文字。" + NO_MUSIC
        ),
    },
    {
        "name": "chuyan_after",
        "keyframe": "chuyan_reaction",
        "duration": 4.0,
        "native_audio": False,
        "prompt": (
            "4秒9:16真人古装短剧无声反应镜。楚焱听见画外楚烟儿叫哥哥，先微微一愣，"
            "随后视线闪动但仍克制，最后停在她的方向；摄影机一次极慢推近。"
            "全程闭嘴，无声音、无BGM，不新增人物、不改变短发、灰蓝粗布衣和日光，不切镜。"
        ),
    },
]


def prepare_keyframes(
    media: PhanRouterMediaProvider,
    names: tuple[str, ...] | None = None,
) -> dict[str, ImageResult]:
    KEYFRAMES.mkdir(parents=True, exist_ok=True)
    results: dict[str, ImageResult] = {}
    selected = {
        name: spec
        for name, spec in KEYFRAME_SPECS.items()
        if names is None or name in names
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                media.create_image,
                str(spec["prompt"]),
                KEYFRAMES / f"{name}.jpeg",
                Path(spec["reference"]),
                tuple(Path(path) for path in spec["additional"]),
            ): name
            for name, spec in selected.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
            print(json.dumps({"stage": "keyframe", "done": name}, ensure_ascii=False), flush=True)
    return results


def prepare_videos(
    speech_media: PhanRouterMediaProvider,
    silent_media: PhanRouterMediaProvider,
    keyframes: dict[str, ImageResult],
    specs: list[dict] | None = None,
) -> None:
    RAW_VIDEO.mkdir(parents=True, exist_ok=True)

    def create(spec: dict) -> Path:
        image = keyframes[str(spec["keyframe"])]
        provider = speech_media if spec["native_audio"] else silent_media
        prompt = (
            "视觉必须保持仿真人AI短剧数字演员质感，真实比例但明确非真人照片。"
            + str(spec["prompt"]).replace(
                "真人古装短剧",
                "仿真人AI古装短剧数字演员",
            )
        )
        return provider.create_video(
            prompt,
            image,
            RAW_VIDEO / f"{spec['name']}.mp4",
            float(spec["duration"]),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        selected_specs = specs if specs is not None else VIDEO_SPECS
        futures = {executor.submit(create, spec): spec["name"] for spec in selected_specs}
        for future in as_completed(futures):
            name = futures[future]
            future.result()
            print(json.dumps({"stage": "video", "done": name}, ensure_ascii=False), flush=True)


def standard_segment(source: Path, output: Path, duration: float, keep_audio: bool) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=25,format=yuv420p,setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration=1,trim=duration={duration:.3f}"
    )
    if keep_audio:
        run([
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-filter_complex",
            f"[0:v]{video_filter}[v];[0:a]aresample=48000,apad,"
            f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
            "afade=t=in:st=0:d=0.08,"
            f"afade=t=out:st={max(0.0, duration - 0.10):.3f}:d=0.10[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(output),
        ])
    else:
        run([
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", f"[0:v]{video_filter}[v]",
            "-map", "[v]", "-map", "1:a:0", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(output),
        ])
    return output


def long_speech_edit(output: Path) -> Path:
    sources = [
        RAW_VIDEO / "examiner_long_master.mp4",
        RAW_VIDEO / "stele_broll.mp4",
        RAW_VIDEO / "yaner_reaction_broll.mp4",
        RAW_VIDEO / "chuyan_reaction_broll.mp4",
    ]
    filters = []
    windows = [(0.0, 3.0), (0.0, 3.0), (0.0, 3.0), (0.0, 3.0), (12.0, 14.0)]
    source_indexes = [0, 1, 2, 3, 0]
    for index, (source_index, (start, end)) in enumerate(zip(source_indexes, windows, strict=True)):
        filters.append(
            f"[{source_index}:v]trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=25,format=yuv420p"
            f"[v{index}]"
        )
    filters.append("[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0[v]")
    filters.append(
        "[0:a]aresample=48000,apad,atrim=duration=14.000,asetpts=PTS-STARTPTS,"
        "afade=t=in:st=0:d=0.08,afade=t=out:st=13.900:d=0.10[a]"
    )
    command = ["ffmpeg", "-y", "-v", "error"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
        "-t", "14.000", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ])
    run(command)
    return output


def assemble(settings: Settings) -> dict:
    SEGMENTS.mkdir(parents=True, exist_ok=True)
    ordered = [
        standard_segment(RAW_VIDEO / "opening_walk.mp4", SEGMENTS / "01_opening.mp4", 4.0, False),
        standard_segment(RAW_VIDEO / "result_announcement.mp4", SEGMENTS / "02_result.mp4", 5.0, True),
        long_speech_edit(SEGMENTS / "03_long_speech.mp4"),
        standard_segment(RAW_VIDEO / "yaner_thanks.mp4", SEGMENTS / "04_thanks.mp4", 4.0, True),
        standard_segment(RAW_VIDEO / "yaner_approach.mp4", SEGMENTS / "05_approach.mp4", 4.0, False),
        standard_segment(RAW_VIDEO / "yaner_brother.mp4", SEGMENTS / "06_brother.mp4", 4.0, True),
        standard_segment(RAW_VIDEO / "chuyan_after.mp4", SEGMENTS / "07_reaction.mp4", 4.0, False),
    ]
    concat = WORK / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{path.resolve()}'" for path in ordered) + "\n",
        encoding="utf-8",
    )
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])

    ambience = WORK / "courtyard_ambience.wav"
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(RAW_VIDEO / "opening_walk.mp4"),
        "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(ambience),
    ])
    mixed = WORK / "mixed.mp4"
    duration = media_duration(joined)
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(joined),
        "-stream_loop", "-1", "-i", str(ambience),
        "-filter_complex",
        f"[0:a]volume=1.0[a0];[1:a]volume=0.12,atrim=duration={duration:.3f}[room];"
        "[a0][room]amix=inputs=2:duration=first:dropout_transition=0[a]",
        "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
        "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(mixed),
    ])

    subtitles = [
        TimedSubtitle(start=4.15, end=8.85, text=RESULT_LINE, role="中年测验员"),
        TimedSubtitle(start=9.15, end=22.75, text=LONG_LINE, role="中年测验员"),
        TimedSubtitle(start=23.15, end=26.75, text=THANKS_LINE, role="楚烟儿"),
        TimedSubtitle(start=31.15, end=34.75, text=BROTHER_LINE, role="楚烟儿"),
    ]
    ass = Renderer(settings).write_ass_pages(WORK / "subtitles.ass", subtitles)
    escaped_ass = str(ass.resolve()).replace("'", r"\'").replace(":", r"\:")
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(mixed),
        "-vf", f"ass='{escaped_ass}'", "-c:v", "libx264", "-preset", "fast",
        "-crf", "18", "-r", "25", "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(FINAL),
    ])
    return {
        "final_video": str(FINAL),
        "duration": round(media_duration(FINAL), 6),
        "segments": [str(path) for path in ordered],
        "subtitles": str(ass),
        "audio_policy": "seedance native dialogue; no per-clip BGM; one continuous courtyard ambience bed",
    }


def main() -> int:
    if not os.getenv("PHANROUTER_API_KEY"):
        raise RuntimeError("PHANROUTER_API_KEY is required in the environment")
    base = Settings.from_env(
        provider="phanrouter",
        output_root="outputs/ftj-anime-api10-v1",
        admission_mode="preview",
    )
    base = replace(
        base,
        image_model="gpt-image-2",
        video_model="sd2.5",
        inline_reference_images=True,
        final_audio_policy="seedance_native_unchecked",
    )
    silent = replace(base, final_audio_policy="locked_tts")
    ROOT.mkdir(parents=True, exist_ok=True)
    privacy_probe = os.getenv(
        "NOVEL_LIVE_ACTION_PRIVACY_PROBE",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
    keyframe_names = ("yaner_closeup",) if privacy_probe else None
    keyframes = prepare_keyframes(
        PhanRouterMediaProvider(base),
        names=keyframe_names,
    )
    if privacy_probe:
        brother = next(
            spec for spec in VIDEO_SPECS if spec["name"] == "yaner_brother"
        )
        prepare_videos(
            PhanRouterMediaProvider(base),
            PhanRouterMediaProvider(silent),
            keyframes,
            specs=[brother],
        )
        result = {
            "stage": "privacy_probe_complete",
            "keyframe": str(KEYFRAMES / "yaner_closeup.jpeg"),
            "video": str(RAW_VIDEO / "yaner_brother.mp4"),
        }
        atomic_write_json(ROOT / "privacy_probe.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    prepare_videos(
        PhanRouterMediaProvider(base),
        PhanRouterMediaProvider(silent),
        keyframes,
    )
    result = assemble(base)
    trace = {
        "sample": "chapter1 live-action probe",
        "source_episode": "第001章 陨落的天才",
        "source_shots": [8, 9, 10],
        "dialogue": [RESULT_LINE, LONG_LINE, THANKS_LINE, BROTHER_LINE],
        **result,
    }
    atomic_write_json(ROOT / "sample_manifest.json", trace)
    print(json.dumps(trace, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
