from __future__ import annotations

import math
import re
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

    def normalize_jpeg(self, source: Path, output: Path) -> Path:
        """Normalize a generated still to the exact submission canvas and format."""
        with Image.open(source).convert("RGB") as image:
            normalized = _fit_cover(image, self.settings.width, self.settings.height)
        output.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(output, "JPEG", quality=96, subsampling=0)
        return output

    def make_cover(
        self,
        background: Path,
        output: Path,
        *,
        novel_title: str,
        art_title: str,
        episode_label: str,
    ) -> Path:
        """Compose exact cover glyphs over a model-generated text-free plate.

        Image models remain responsible for characters, setting and lighting;
        deterministic rendering owns every visible Chinese glyph.  This avoids
        plausible-looking but incorrect episode seals and keeps dialogue off
        the cover while retaining a poster-like title treatment.
        """

        with Image.open(background).convert("RGB") as source:
            base = _fit_cover(source, self.settings.width, self.settings.height)
        image = base.convert("RGBA")
        shade = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shade_pixels = shade.load()
        gradient_end = min(720, self.settings.height)
        for y in range(gradient_end):
            alpha = round(178 * (1.0 - y / gradient_end))
            for x in range(self.settings.width):
                shade_pixels[x, y] = (4, 7, 12, alpha)
        image = Image.alpha_composite(image, shade)
        draw = ImageDraw.Draw(image, "RGBA")

        series_font = self._font(66)
        badge_font = self._font(55)
        draw.text(
            (60, 72),
            novel_title,
            font=series_font,
            fill=(240, 197, 91, 255),
            stroke_width=2,
            stroke_fill=(12, 10, 8, 220),
        )
        badge_box = (790, 82, 1010, 174)
        draw.rectangle(badge_box, fill=(151, 25, 21, 242))
        badge_bounds = draw.textbbox((0, 0), episode_label, font=badge_font)
        draw.text(
            (
                (badge_box[0] + badge_box[2] - badge_bounds[2]) / 2,
                badge_box[1] + 10,
            ),
            episode_label,
            font=badge_font,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(30, 12, 10, 180),
        )

        clean_title = "".join(art_title.split()) or "本集故事"
        if len(clean_title) <= 4:
            title_lines = [clean_title]
        elif "的" in clean_title[:-1]:
            split = clean_title.index("的") + 1
            title_lines = [clean_title[:split], clean_title[split:]]
        else:
            split = (len(clean_title) + 1) // 2
            title_lines = [clean_title[:split], clean_title[split:]]
        title_lines = title_lines[:2]
        y_positions = (188, 344) if len(title_lines) == 2 else (250,)
        colors = ((255, 255, 255, 255), (240, 183, 47, 255))
        for index, (line, y) in enumerate(zip(title_lines, y_positions, strict=True)):
            max_size = 190 if index else 154
            font_size = min(max_size, max(96, round(760 / max(1, len(line)))))
            font = self._font(font_size)
            bounds = draw.textbbox((0, 0), line, font=font, stroke_width=8)
            text_width = bounds[2] - bounds[0]
            x = 58 if index == 0 else (self.settings.width - text_width) / 2
            draw.text(
                (x + 9, y + 11),
                line,
                font=font,
                fill=(80, 35, 4, 205),
                stroke_width=8,
                stroke_fill=(18, 12, 8, 220),
            )
            draw.text(
                (x, y),
                line,
                font=font,
                fill=colors[min(index, 1)],
                stroke_width=7,
                stroke_fill=(10, 9, 9, 245),
            )
        draw.line((52, 565, 702, 565), fill=(217, 166, 46, 245), width=5)

        output.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(output, "JPEG", quality=96, subsampling=0)
        return output

    def make_card(self, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
        with Image.open(background).convert("RGB") as source:
            image = _fit_cover(source, self.settings.width, self.settings.height)
        image = ImageEnhance.Contrast(image).enhance(0.92)
        blurred = image.filter(ImageFilter.GaussianBlur(1.2))
        image = Image.blend(image, blurred, 0.10)
        draw = ImageDraw.Draw(image, "RGBA")
        # Keep the story artwork visible.  Ending copy is treated as elegant
        # typography, never as text placed inside an opaque UI panel.
        draw.rectangle((0, 0, self.settings.width, self.settings.height), fill=(8, 12, 24, 58))

        y = 245
        for line in _wrap(novel_title, width=10, max_lines=2):
            box = draw.textbbox((0, 0), line, font=self._font(70), stroke_width=4)
            x = (self.settings.width - (box[2] - box[0])) / 2
            draw.text(
                (x, y),
                line,
                font=self._font(70),
                fill=(255, 246, 218),
                stroke_width=4,
                stroke_fill=(24, 20, 18),
            )
            y += 90

        divider_y = max(405, y + 20)
        draw.line((265, divider_y, 815, divider_y), fill=(238, 196, 93, 220), width=3)
        draw.ellipse((250, divider_y - 6, 262, divider_y + 6), fill=(238, 196, 93, 230))
        draw.ellipse((818, divider_y - 6, 830, divider_y + 6), fill=(238, 196, 93, 230))

        box = draw.textbbox((0, 0), label, font=self._font(84), stroke_width=4)
        draw.text(
            ((self.settings.width - (box[2] - box[0])) / 2, divider_y + 48),
            label,
            font=self._font(84),
            fill=(248, 205, 92),
            stroke_width=4,
            stroke_fill=(28, 22, 18),
        )
        if subtitle:
            y = 1500
            draw.line((335, y - 45, 745, y - 45), fill=(238, 196, 93, 185), width=2)
            for line in _wrap(subtitle, width=15, max_lines=2):
                box = draw.textbbox((0, 0), line, font=self._font(50), stroke_width=4)
                draw.text(
                    ((1080 - (box[2] - box[0])) / 2, y),
                    line,
                    font=self._font(50),
                    fill=(255, 248, 228),
                    stroke_width=4,
                    stroke_fill=(20, 18, 18),
                )
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
                # One-pass loudnorm has a long analysis window and can erase
                # sub-two-second dialogue. TTS output is already levelled;
                # preserve it here and normalize only after episode assembly.
                f"[1:a]aresample=48000,"
                f"apad=pad_dur={pause_after:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]"
            ),
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-r", str(self.settings.fps), "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output),
        ])
        return output, media_duration(output)

    @staticmethod
    def _peak_volume_db(path: Path) -> float | None:
        """Measure one locked turn without changing its duration or samples."""

        result = run([
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ])
        match = re.search(r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB", result.stderr)
        if match is None or match.group(1) in {"-inf", "inf"}:
            return None
        return float(match.group(1))

    def compose_visual_group_audio(
        self,
        audios: list[Path],
        output: Path,
        *,
        audible: list[bool] | None = None,
        gap: float = 0.10,
        target_seconds: float = 13.4,
        max_speed: float = 1.0,
    ) -> tuple[Path, float, list[float], float]:
        """Join locked turns for one continuous shot and return scaled turn offsets.

        Neural TTS can preserve the requested emotion while producing very
        different amplitudes between turns.  Peak-normalize only the delivery
        mix, before concatenation, so every spoken turn remains audible.  The
        H3 performance driver (``audible`` is not ``None``) keeps the original
        reference samples and timing.
        """
        if not audios:
            raise ValueError("visual group requires at least one audio file")
        if audible is not None and len(audible) != len(audios):
            raise ValueError("audible flags must match visual-group audio inputs")
        durations = [media_duration(path) for path in audios]
        unscaled = sum(durations) + gap * max(0, len(audios) - 1)
        if max_speed != 1.0:
            raise ValueError("speech speed must be controlled inside the TTS model")
        speed = 1.0
        if unscaled > target_seconds + 0.03:
            raise ValueError(
                f"visual group audio {unscaled:.3f}s cannot fit {target_seconds:.3f}s "
                "without post-processing speed changes"
            )
        filters: list[str] = []
        concat_inputs: list[str] = []
        for index in range(len(audios)):
            mute = "volume=0," if audible is not None and not audible[index] else ""
            delivery_gain = ""
            if audible is None:
                peak_db = self._peak_volume_db(audios[index])
                if peak_db is not None:
                    gain_db = max(-12.0, min(36.0, -3.0 - peak_db))
                    delivery_gain = f"volume={gain_db:.3f}dB,"
            filters.append(
                f"[{index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"{mute}"
                f"{delivery_gain}"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
            concat_inputs.append(f"[a{index}]")
            if index < len(audios) - 1:
                filters.append(
                    f"anullsrc=r=48000:cl=stereo:d={gap:.6f}[g{index}]"
                )
                concat_inputs.append(f"[g{index}]")
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(concat_inputs)}:v=0:a=1[out]"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        command = ["ffmpeg", "-y", "-v", "error"]
        for audio in audios:
            command.extend(["-i", str(audio)])
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(output),
            ]
        )
        run(command)
        offsets: list[float] = []
        cursor = 0.0
        for duration in durations:
            offsets.append(cursor)
            cursor += duration + gap
        return output, media_duration(output), offsets, speed

    def mux_visual_group(
        self,
        visual: Path,
        audio: Path,
        output: Path,
        *,
        pause_after: float = 0.20,
    ) -> tuple[Path, float]:
        """Mux one continuous generated performance with its combined reference audio."""
        duration = media_duration(audio) + pause_after
        output.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-i", str(visual), "-i", str(audio),
            "-filter_complex",
            (
                f"[0:v]scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},fps={self.settings.fps},format=yuv420p,"
                f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={pause_after + 1.0:.3f}[v];"
                f"[1:a]aresample=48000,apad=pad_dur={pause_after:.3f},"
                f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]"
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
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H00000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

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
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H00000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

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
        sequence: list[Path] = []
        if self.settings.intro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    cover, work_dir / "intro.mp4", self.settings.intro_seconds
                )
            )
        sequence.extend(item[0] for item in shot_segments)
        if self.settings.outro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    ending, work_dir / "outro.mp4", self.settings.outro_seconds
                )
            )
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
        sequence: list[Path] = []
        if self.settings.intro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    intro_card, work_dir / "intro.mp4", self.settings.intro_seconds
                )
            )
        sequence.extend(Path(item["segment"]) for item in turn_segments)
        if self.settings.outro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    ending, work_dir / "outro.mp4", self.settings.outro_seconds
                )
            )
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
            local_events = item.get("subtitle_events")
            if local_events is None:
                local_events = [
                    {
                        "unit_id": item["unit_id"],
                        "role": item["role"],
                        **event,
                    }
                    for event in item["alignment"]["events"]
                ]
            for event in local_events:
                events.append(
                    {
                        "unit_id": event["unit_id"],
                        "role": event["role"],
                        "start": cursor + float(event["start"]),
                        "end": cursor + float(event["end"]),
                        "text": str(event["text"]),
                    }
                )
            cursor += float(item["duration"])
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
        run([
            "ffmpeg", "-y", "-i", str(joined), "-vf", f"ass='{escaped_ass}'",
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
