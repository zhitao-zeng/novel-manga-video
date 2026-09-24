from __future__ import annotations
import novel_manga.episodes as ep_names
import json
import re
import shutil
import subprocess
from pathlib import Path
from PIL import Image
from ..util import media_duration, run
from .common import cover_title, log

from concurrent.futures import ThreadPoolExecutor
from ..render import Renderer, _fit_cover
from . import subtitles, chat_card

CHAT_CONTEXT_MESSAGES = 2

CHAT_HISTORY_EPISODES = 3

def _font(ctx, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(ctx.settings.font_path), size)

def landscape_cover(ctx, background: Path, output: Path, novel_title: str, art_title: str, label: str) -> Path:
    from PIL import ImageDraw
    W, H = ctx.settings.width, ctx.settings.height
    with Image.open(background).convert("RGB") as source:
        image = _fit_cover(source, W, H).convert("RGBA")
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for x in range(0, W // 2):
        draw.line((x, 0, x, H), fill=(6, 10, 20, int(190 * (1 - x / (W / 2)))))
    image = Image.alpha_composite(image, shade)
    draw = ImageDraw.Draw(image)
    draw.text((90, 70), novel_title, font=_font(ctx, 48), fill=(240, 197, 91), stroke_width=2, stroke_fill=(10, 8, 6))
    y = 150
    for line in [art_title[i:i + 6] for i in range(0, len(art_title), 6)][:2]:
        draw.text((86, y), line, font=_font(ctx, 132), fill=(255, 250, 235), stroke_width=6, stroke_fill=(14, 10, 8))
        y += 150
    draw.line((92, y + 10, 92 + 520, y + 10), fill=(238, 196, 93, 220), width=4)
    box = (W - 300, 64, W - 80, 156)
    draw.rounded_rectangle(box, radius=14, fill=(150, 26, 24))
    text_w = draw.textbbox((0, 0), label, font=_font(ctx, 52))[2]
    draw.text(((box[0] + box[2] - text_w) / 2, 74), label, font=_font(ctx, 52), fill=(255, 246, 218))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
    return output

def landscape_card(ctx, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
    from PIL import ImageDraw
    W, H = ctx.settings.width, ctx.settings.height
    with Image.open(background).convert("RGB") as source:
        image = _fit_cover(source, W, H).convert("RGBA")
    image = Image.alpha_composite(image, Image.new("RGBA", (W, H), (8, 12, 24, 150)))
    draw = ImageDraw.Draw(image)
    title_w = draw.textbbox((0, 0), novel_title, font=_font(ctx, 56))[2]
    draw.text(((W - title_w) / 2, H * 0.30), novel_title, font=_font(ctx, 56), fill=(255, 246, 218))
    label_w = draw.textbbox((0, 0), label, font=_font(ctx, 140))[2]
    draw.text(((W - label_w) / 2, H * 0.40), label, font=_font(ctx, 140), fill=(248, 205, 92), stroke_width=6, stroke_fill=(14, 10, 8))
    draw.line((W / 2 - 260, H * 0.72, W / 2 + 260, H * 0.72), fill=(238, 196, 93, 200), width=3)
    sub_w = draw.textbbox((0, 0), subtitle, font=_font(ctx, 48))[2]
    draw.text(((W - sub_w) / 2, H * 0.72 + 30), subtitle, font=_font(ctx, 48), fill=(255, 248, 228))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
    return output

def frame(ctx, video: Path, second: float, output: Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{second:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])
    return output

def chat_history(ctx, clip_id: str) -> dict[str, list[dict]]:
    """Earlier messages per conversation: this episode's previous clips, then
    the previous episodes, newest last.  Keyed like chat_card.channels()."""
    history: dict[str, list[dict]] = {}

    def absorb(plan: dict, stop_at: str | None) -> None:
        for other in plan.get("clips", []):
            if stop_at and other["clip_id"] == stop_at:
                break
            for run in chat_card.channels(other.get("chat_lines") or [], str(ctx.chat_screen.get("self_name", ""))):
                history.setdefault(run["key"], []).extend(run["messages"])

    try:
        index = ep_names.chapter_of(ctx.episode_dir.name)
    except (ValueError, IndexError):
        index = None
    if index is not None:
        for previous in range(max(1, index - CHAT_HISTORY_EPISODES), index):
            plan_path = ctx.novel_dir / f"{ctx.novel_dir.name}_{previous}" / "clip_plan.json"
            if plan_path.is_file():
                try:
                    absorb(json.loads(plan_path.read_text(encoding="utf-8")), None)
                except (OSError, ValueError):
                    pass
    absorb(ctx.clip_plan, clip_id)
    return history

def chat_segments(ctx, clip_id: str, clip_video: Path) -> list[dict]:
    """Phone-screen cards for this clip, drawn here instead of by the video model.

    The card is cut in just before the clip, so the messages land (one chime
    each) and the film then shows the character reading them.  Chat text is
    on screen, so these segments carry no subtitles.
    """
    if str(ctx.chat_screen.get("render", "card")) != "card":
        return []
    clip = next((c for c in ctx.clip_plan["clips"] if c["clip_id"] == clip_id), {})
    runs = chat_card.channels(clip.get("chat_lines") or [], str(ctx.chat_screen.get("self_name", "")))
    if not runs:
        return []
    history = chat_history(ctx, clip_id)
    out_dir = ctx.work / "chat"
    out_dir.mkdir(parents=True, exist_ok=True)
    background = None
    try:
        background = frame(ctx, clip_video, 0.4, out_dir / f"{clip_id}_plate.jpeg")
    except Exception as error:  # noqa: BLE001 - a missing plate only costs the blurred backdrop
        log(f"{clip_id}: chat card backdrop unavailable ({type(error).__name__})")
    segments = []
    card = 0
    for run in runs:
        names = [str(m.get("speaker_name", "")) for m in run["messages"]] + [str(ctx.chat_screen.get("self_name", "")), run["target"]]
        avatars = chat_card.load_avatars(ctx.novel_dir, [name for name in names if name])
        # A card that opens on an empty screen looks wrong; seed it with the
        # last messages of the same conversation so the new ones land below them.
        context = history.get(run["key"], [])[-CHAT_CONTEXT_MESSAGES:]
        messages = context + run["messages"]
        for window in chat_card.windows(len(run["messages"])):
            card += 1
            window = (window[0] + len(context), window[1] + len(context))
            path, seconds = chat_card.build_segment(
                messages, out_dir / f"{clip_id}_chat_{card:02d}.mp4",
                title=run["target"] or str(ctx.chat_screen.get("group_name", "群聊")),
                self_name=str(ctx.chat_screen.get("self_name", "")), group=not run["target"], avatars=avatars,
                width=ctx.settings.width, height=ctx.settings.height, fps=ctx.settings.fps, background=background,
                window=window,
            )
            log(f"{clip_id}: chat card {card} (messages {window[0] + 1}-{window[1]}, {seconds:.1f}s)")
            segments.append({"unit_id": f"{clip_id}_chat{card}", "role": "chat", "segment": str(path),
                             "duration": seconds, "audio_source": "chat_card", "subtitle_events": []})
    return segments

def title_card_image(ctx, text: str, background: Path | None, output: Path) -> Path:
    """A time-jump caption on a darkened, softened frame of the scene it leads into."""
    from PIL import ImageDraw, ImageFilter
    W, H = ctx.settings.width, ctx.settings.height
    if background is not None and Path(background).is_file():
        with Image.open(background) as source:
            image = _fit_cover(source.convert("RGB"), W, H).filter(ImageFilter.GaussianBlur(radius=max(6, W // 120)))
    else:
        image = Image.new("RGB", (W, H), (12, 14, 22))
    image = Image.alpha_composite(image.convert("RGBA"), Image.new("RGBA", (W, H), (6, 8, 16, 170)))
    draw = ImageDraw.Draw(image)
    lines = [line.strip() for line in str(text).splitlines() if line.strip()] or [" "]
    size = int(min(W, H) * 0.11)
    while size > 24 and max(draw.textbbox((0, 0), line, font=_font(ctx, size))[2] for line in lines) > W * 0.84:
        size -= 4
    font = _font(ctx, size)
    gap = int(size * 0.35)
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    y = (H - (sum(heights) + gap * (len(lines) - 1))) / 2
    for line, height in zip(lines, heights):
        width = draw.textbbox((0, 0), line, font=font)[2]
        draw.text(((W - width) / 2, y), line, font=font, fill=(255, 246, 222), stroke_width=max(2, size // 24), stroke_fill=(10, 8, 6))
        y += height + gap
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
    return output

def title_card_segment(ctx, clip: dict, background: Path | None) -> dict:
    """A title card from the plan ("二十年后"), drawn here and cut in where the plan puts it.  The runner used to
    take only the video clips, so every time jump the script announced this way was dropped (星海 817, 1146)."""
    out_dir = ctx.work / "titles"
    seconds = float(clip.get("request_seconds") or 3)
    image = title_card_image(ctx, clip.get("text", ""), background, out_dir / f"{clip['clip_id']}.jpeg")
    segment = ctx.renderer._silent_card_segment(image, out_dir / f"{clip['clip_id']}.mp4", seconds)
    log(f"{clip['clip_id']}: title card 「{clip.get('text', '')}」 ({seconds:.0f}s)")
    return {"unit_id": clip["clip_id"], "role": "title", "segment": str(segment), "duration": seconds,
            "audio_source": "title_card", "subtitle_events": []}

def inner_voice_audio(ctx, clip: dict, wav: Path) -> Path:
    """A faint short reflection distinguishes private thought without changing the speaker's pitch."""
    spoken = [r for r in clip.get('dialogue_bindings', [])
              if r.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue'}]
    if not spoken or not all(r.get('inner_monologue') for r in spoken):
        return wav
    output = ctx.work / 'audio' / f"{clip['clip_id']}_inner.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = media_duration(wav)
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(wav), '-af',
         f'aecho=1:0.92:45:0.10,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS',
         '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(output)])
    return output


def story_segments(ctx, results: list[dict]) -> list[dict]:
    """The episode's segments in plan order: chat cards, each clip, and the plan's title cards."""
    by_id = {record["clip_id"]: record for record in results}
    order = ctx.clip_plan["clips"]
    (ctx.work / "titles").mkdir(parents=True, exist_ok=True)
    segments: list[dict] = []
    for index, clip in enumerate(order):
        if clip["kind"] == "title_card":
            # Over the scene the jump lands in: the next rendered clip's opening frame, else the last one's end.
            after = next((by_id[c["clip_id"]] for c in order[index + 1:] if c["clip_id"] in by_id), None)
            before = next((by_id[c["clip_id"]] for c in reversed(order[:index]) if c["clip_id"] in by_id), None)
            plate, backdrop = ctx.work / "titles" / f"{clip['clip_id']}_plate.jpeg", None
            try:
                if after is not None:
                    backdrop = frame(ctx, Path(after["selected"]["video"]), 0.4, plate)
                elif before is not None:
                    video = Path(before["selected"]["video"])
                    backdrop = frame(ctx, video, max(0.1, media_duration(video) - 0.6), plate)
            except Exception as error:  # noqa: BLE001 - a missing plate only costs the backdrop
                log(f"{clip['clip_id']}: title card backdrop unavailable ({type(error).__name__})")
            segments.append(title_card_segment(ctx, clip, backdrop))
            continue
        record = by_id.get(clip["clip_id"])
        if record is None:
            continue
        selected = record["selected"]
        clip_video = Path(selected["video"])
        wav = inner_voice_audio(ctx, clip, clip_video.parent / "native.wav")
        segment, duration = ctx.renderer.mux_visual_group(clip_video, wav, ctx.work / "segments" / f"{record['clip_id']}.mp4")
        segments.extend(chat_segments(ctx, record["clip_id"], clip_video))
        segments.append({"unit_id": record["clip_id"], "role": "dialogue", "segment": str(segment), "duration": duration, "audio_source": "native_dialogue", "subtitle_events": subtitles.subtitle_events(ctx, record["clip_id"], selected)})
    return segments

class BatchRenderer(Renderer):
    def _join_with_crossfade(self, sequence, durations, output, *, crossfade_seconds=0.15):
        fps, width, height = self.settings.fps, self.settings.width, self.settings.height
        def normalize(item):
            index, path = item
            target = output.parent / f"join_{index:02d}.mp4"
            result = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-threads", "4", "-i", str(path),
                 "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
                 "-vsync", "cfr", "-c:v", "libx264", "-preset", "superfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)],
                capture_output=True, text=True)
            # ffmpeg can exit 0 having written a file with no streams; the
            # concat that follows then fails with a useless message, so the
            # normalised part is checked here where the input is still known.
            if result.returncode != 0 or media_duration(target) <= 0.0:
                raise RuntimeError(f"normalise failed for {path} (exit {result.returncode}): {(result.stderr or '')[-400:]}")
            return target
        with ThreadPoolExecutor(max_workers=4) as pool:  # segments normalise side by side
            normalized = list(pool.map(normalize, enumerate(sequence)))
        list_file = output.parent / "join_list.txt"
        list_file.write_text("".join(f"file '{p}'\n" for p in normalized), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(output)])
        offsets, cumulative = [], 0.0
        for path in normalized:
            offsets.append(cumulative)
            cumulative += media_duration(path)
        return offsets


    def write_ass_pages(self, path, subtitles):
        result = super().write_ass_pages(path, subtitles)
        width, height = self.settings.width, self.settings.height
        margin_v = 310 if height > width else max(60, round(height * 0.08))
        text = Path(result).read_text(encoding='utf-8').replace(',2,90,90,310,1', f',2,90,90,{margin_v},1', 1)
        Path(result).write_text(text, encoding='utf-8')
        return result


def assemble(ctx, results: list[dict], output_dir: Path) -> dict:
    video_id = ctx.episode_dir.name
    turn_segments = story_segments(ctx, results)
    first_video = Path(results[0]["selected"]["video"])
    last_video = Path(results[-1]["selected"]["video"])
    cover_frame = frame(ctx, first_video, min(1.5, max(0.1, media_duration(first_video) - 0.2)), ctx.work / "cover_frame.jpeg")
    ending_frame = frame(ctx, last_video, max(0.1, media_duration(last_video) - 0.6), ctx.work / "ending_frame.jpeg")
    chapter_title = ctx.script.get("video_title") or ctx.bible.novel_title
    art_title = cover_title(ctx.script.get("source_title") or "", chapter_title)
    cover = output_dir / f"{video_id}_cover.jpeg"
    ending = output_dir / f"{video_id}_ending.jpeg"
    # Episode number from the directory name: "<novel>_3" or "<novel>_1-grammar".
    number_match = re.search(r"_(\d+)(?:-[A-Za-z0-9]+)?$", video_id)
    episode_number = int(number_match.group(1)) if number_match else 1
    if ctx.settings.width > ctx.settings.height:
        landscape_cover(ctx, cover_frame, cover, ctx.bible.novel_title, art_title, f"第{episode_number:02d}集")
        landscape_card(ctx, ending_frame, ending, ctx.bible.novel_title, "未完待续", "敬请期待下一集")
    else:
        ctx.renderer.make_cover(cover_frame, cover, novel_title=ctx.bible.novel_title, art_title=art_title, episode_label=f"第{episode_number:02d}集")
        ctx.renderer.make_card(ending_frame, ending, ctx.bible.novel_title, "未完待续", "敬请期待下一集")
    final_video = output_dir / f"{video_id}.mp4"
    final, ass, joined, events = ctx.renderer.assemble_production(cover, ending, turn_segments, final_video, ctx.work)
    if output_dir != ctx.episode_dir:
        staged_ass = output_dir / f"{video_id}.ass"
        shutil.copy2(ass, staged_ass)
        ass = staged_ass
    # The actual end card rendered for this final, not a guess from the settings on a later recheck.
    silent_outro = media_duration(ctx.work / "outro.mp4") if ctx.settings.outro_seconds > 0 else 0.0
    return {'final_video': str(final), 'cover': str(cover), 'ending': str(ending), 'ass': str(ass),
            'duration': round(media_duration(final), 3), 'subtitle_events': len(events),
            'silent_outro_seconds': silent_outro, 'pending_publish': output_dir != ctx.episode_dir}
