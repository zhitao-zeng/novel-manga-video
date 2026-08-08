from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import Settings
from .util import media_duration, run


@dataclass(frozen=True)
class TimedSubtitle:
    start: float
    end: float
    text: str
    role: str = "narrator"


def _fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * ratio), round(image.height * ratio)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _wrap(text: str, width: int = 11, max_lines: int = 3) -> list[str]:
    clean = "".join(text.split())
    return [clean[i:i + width] for i in range(0, len(clean), width)][:max_lines] or [""]


class Renderer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.settings.font_path), size)

    def make_card(self, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
        with Image.open(background).convert("RGB") as source:
            image = _fit_cover(source, self.settings.width, self.settings.height)
        image = ImageEnhance.Contrast(image).enhance(0.88)
        blurred = image.filter(ImageFilter.GaussianBlur(1.2))
        image = Image.blend(image, blurred, 0.18)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle((0, 0, self.settings.width, self.settings.height), fill=(8, 12, 24, 76))
        draw.rounded_rectangle((70, 180, 1010, 620), radius=38, fill=(10, 14, 30, 178), outline=(238, 196, 93, 230), width=5)

        y = 235
        for line in _wrap(novel_title, width=10, max_lines=2):
            box = draw.textbbox((0, 0), line, font=self._font(82), stroke_width=4)
            x = (self.settings.width - (box[2] - box[0])) / 2
            draw.text((x, y), line, font=self._font(82), fill=(255, 246, 218), stroke_width=4, stroke_fill=(35, 25, 18))
            y += 102
        box = draw.textbbox((0, 0), label, font=self._font(54))
        draw.text(((self.settings.width - (box[2] - box[0])) / 2, 475), label, font=self._font(54), fill=(248, 205, 92), stroke_width=3, stroke_fill="black")
        if subtitle:
            draw.rounded_rectangle((95, 1420, 985, 1695), radius=34, fill=(8, 12, 24, 190))
            y = 1470
            for line in _wrap(subtitle, width=15, max_lines=2):
                box = draw.textbbox((0, 0), line, font=self._font(50))
                draw.text(((1080 - (box[2] - box[0])) / 2, y), line, font=self._font(50), fill="white", stroke_width=3, stroke_fill="black")
                y += 68
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "JPEG", quality=96, subsampling=0)
        return output

    def _silent_card_segment(self, image: Path, output: Path, duration: float) -> Path:
        frames = max(1, round(duration * self.settings.fps))
        run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(image),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", (
                f"scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},"
                f"zoompan=z='min(zoom+0.00025,1.04)':d={frames}:"
                f"s={self.settings.width}x{self.settings.height}:fps={self.settings.fps},format=yuv420p"
            ),
            "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-shortest", str(output),
        ])
        return output

    def mux_shot(self, visual: Path, audio: Path, output: Path) -> tuple[Path, float]:
        duration = media_duration(audio) + 0.08
        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(visual), "-i", str(audio),
            "-vf", (
                f"scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},fps={self.settings.fps},format=yuv420p"
            ),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(self.settings.fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(output),
        ])
        return output, media_duration(output)

    def mux_turn(self, visual: Path, audio: Path, output: Path, *, pause_after: float = 0.3) -> tuple[Path, float]:
        """Mux one exact reference-audio turn without looping generated motion."""
        duration = media_duration(audio) + pause_after
        output.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-i", str(visual), "-i", str(audio),
            "-filter_complex",
            (
                f"[0:v]scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},fps={self.settings.fps},format=yuv420p,"
                f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={pause_after + 1.0:.3f}[v];"
                f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"apad=pad_dur={pause_after:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]"
            ),
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-r", str(self.settings.fps), "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output),
        ])
        return output, media_duration(output)

    @staticmethod
    def _ass_time(seconds: float) -> str:
        centis = max(0, round(seconds * 100))
        hours, centis = divmod(centis, 360000)
        minutes, centis = divmod(centis, 6000)
        secs, centis = divmod(centis, 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

    def write_ass(self, path: Path, subtitles: list[TimedSubtitle]) -> Path:
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.settings.width}
PlayResY: {self.settings.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H78000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: list[str] = []
        for item in subtitles:
            clean = item.text.replace("{", "（").replace("}", "）").replace("\n", "")
            chunks = [clean[i:i + 18] for i in range(0, len(clean), 18)] or [""]
            pages = [chunks[i:i + 2] for i in range(0, len(chunks), 2)]
            span = max(0.2, (item.end - item.start) / len(pages))
            for index, page in enumerate(pages):
                start = item.start + index * span
                end = item.end if index == len(pages) - 1 else min(item.end, start + span)
                text = r"\N".join(page)
                events.append(
                    f"Dialogue: 0,{self._ass_time(start)},{self._ass_time(end)},Default,{item.role},0,0,0,,{text}"
                )
        path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
        return path

    def write_ass_pages(self, path: Path, subtitles: list[TimedSubtitle]) -> Path:
        """Write already aligned/paged events without changing their text or timing."""
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.settings.width}
PlayResY: {self.settings.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H78000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        rows = []
        for item in subtitles:
            text = item.text.replace("{", "（").replace("}", "）").replace("\n", r"\N")
            rows.append(
                f"Dialogue: 0,{self._ass_time(item.start)},{self._ass_time(item.end)},"
                f"Default,{item.role},0,0,0,,{text}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        return path

    def assemble(
        self,
        cover: Path,
        ending: Path,
        shot_segments: list[tuple[Path, float, str]],
        output: Path,
        work_dir: Path,
    ) -> tuple[Path, Path]:
        intro = self._silent_card_segment(cover, work_dir / "intro.mp4", self.settings.intro_seconds)
        outro = self._silent_card_segment(ending, work_dir / "outro.mp4", self.settings.outro_seconds)
        sequence = [intro] + [item[0] for item in shot_segments] + [outro]
        concat_file = work_dir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{path.resolve()}'" for path in sequence) + "\n", encoding="utf-8")
        joined = work_dir / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

        cursor = self.settings.intro_seconds
        subtitles: list[TimedSubtitle] = []
        for _, duration, text in shot_segments:
            subtitles.append(TimedSubtitle(start=cursor, end=cursor + duration, text=text))
            cursor += duration
        ass = self.write_ass(work_dir / "subtitles.ass", subtitles)
        output.parent.mkdir(parents=True, exist_ok=True)
        escaped_ass = str(ass.resolve()).replace("'", r"\'").replace(":", r"\:")
        run([
            "ffmpeg", "-y", "-i", str(joined),
            "-vf", f"ass='{escaped_ass}'", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", str(self.settings.fps), "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", str(output),
        ])
        if self.settings.bgm_path:
            mixed = work_dir / "mixed.mp4"
            run([
                "ffmpeg", "-y", "-i", str(output), "-stream_loop", "-1", "-i", str(self.settings.bgm_path),
                "-filter_complex", "[0:a]volume=1.0[voice];[1:a]volume=0.08[bgm];[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", str(mixed),
            ])
            shutil.move(mixed, output)
        return output, ass

    def assemble_production(
        self,
        intro_card: Path,
        ending: Path,
        turn_segments: list[dict],
        output: Path,
        work_dir: Path,
    ) -> tuple[Path, Path, Path, list[dict]]:
        intro = self._silent_card_segment(intro_card, work_dir / "intro.mp4", self.settings.intro_seconds)
        outro = self._silent_card_segment(ending, work_dir / "outro.mp4", self.settings.outro_seconds)
        sequence = [intro] + [Path(item["segment"]) for item in turn_segments] + [outro]
        concat_file = work_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{path.resolve()}'" for path in sequence) + "\n",
            encoding="utf-8",
        )
        joined = work_dir / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

        cursor = self.settings.intro_seconds
        events: list[dict] = []
        for item in turn_segments:
            for event in item["alignment"]["events"]:
                events.append(
                    {
                        "unit_id": item["unit_id"],
                        "role": item["role"],
                        "start": cursor + float(event["start"]),
                        "end": cursor + float(event["end"]),
                        "text": str(event["text"]),
                    }
                )
            cursor += float(item["duration"])
        story_end = cursor
        subtitles = [
            TimedSubtitle(
                start=float(event["start"]),
                end=float(event["end"]),
                text=str(event["text"]),
                role=str(event["role"]),
            )
            for event in events
        ]
        ass = self.write_ass_pages(work_dir / "subtitles.ass", subtitles)
        output.parent.mkdir(parents=True, exist_ok=True)
        escaped_ass = str(ass.resolve()).replace("'", r"\'").replace(":", r"\:")
        # Some reference-audio video providers occasionally burn their own
        # inaccurate text into the lower safe area despite a no-text prompt.
        # Mask that provider text uniformly during the story, then render the
        # locked/aligned ASS subtitles above the mask.  Keeping the mask off the
        # intro/outro preserves the fixed series templates.
        subtitle_mask = (
            "drawbox=x=40:y=1380:w=1000:h=320:color=black@1.0:t=fill:"
            f"enable='between(t,{self.settings.intro_seconds:.3f},{story_end:.3f})'"
        )
        run([
            "ffmpeg", "-y", "-i", str(joined), "-vf", f"{subtitle_mask},ass='{escaped_ass}'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", str(self.settings.fps), "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", str(output),
        ])
        if self.settings.bgm_path:
            mixed = work_dir / "mixed.mp4"
            run([
                "ffmpeg", "-y", "-i", str(output), "-stream_loop", "-1", "-i", str(self.settings.bgm_path),
                "-filter_complex", "[0:a]volume=1.0[voice];[1:a]volume=0.08[bgm];[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", str(mixed),
            ])
            shutil.move(mixed, output)
        return output, ass, joined, events
