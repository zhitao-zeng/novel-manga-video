from __future__ import annotations

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

    def mux_visual_group(
        self,
        visual: Path,
        audio: Path,
        output: Path,
    ) -> tuple[Path, float]:
        """Mux native audio without changing the generated clip's duration."""
        duration = media_duration(visual)
        audio_padding = max(0.0, duration - media_duration(audio))
        output.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-i", str(visual), "-i", str(audio),
            "-filter_complex",
            (
                f"[0:v]scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},fps={self.settings.fps},format=yuv420p,"
                "setpts=PTS-STARTPTS[v];"
                f"[1:a]aresample=48000,apad=pad_dur={audio_padding:.3f},"
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

    def _join_with_crossfade(
        self,
        sequence: list[Path],
        durations: list[float],
        output: Path,
        *,
        crossfade_seconds: float = 0.15,
    ) -> list[float]:
        """Join matching A/V transitions and return each source start time."""

        if len(sequence) != len(durations) or not sequence:
            raise ValueError("crossfade join requires one duration per segment")
        command = ["ffmpeg", "-y", "-v", "error"]
        for path in sequence:
            command.extend(["-i", str(path)])
        filters = []
        for index in range(len(sequence)):
            filters.extend(
                [
                    f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
                    f"fps={self.settings.fps},format=yuv420p[v{index}]",
                    f"[{index}:a]aresample=48000,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{index}]",
                ]
            )
        offsets = [0.0]
        video_label = "v0"
        audio_label = "a0"
        cumulative = durations[0]
        for index in range(1, len(sequence)):
            fade = min(
                crossfade_seconds,
                max(0.01, durations[index - 1] / 4),
                max(0.01, durations[index] / 4),
            )
            start = cumulative - fade
            filters.append(
                f"[{video_label}][v{index}]xfade=transition=fade:duration={fade:.3f}:"
                f"offset={start:.6f}[vx{index}]"
            )
            filters.append(
                f"[{audio_label}][a{index}]acrossfade=d={fade:.3f}:c1=tri:c2=tri[ax{index}]"
            )
            video_label = f"vx{index}"
            audio_label = f"ax{index}"
            offsets.append(start)
            cumulative = start + durations[index]
        output.parent.mkdir(parents=True, exist_ok=True)
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{video_label}]",
                "-map",
                f"[{audio_label}]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-r",
                str(self.settings.fps),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        run(command)
        return offsets

    def assemble_production(
        self,
        intro_card: Path,
        ending: Path,
        turn_segments: list[dict],
        output: Path,
        work_dir: Path,
    ) -> tuple[Path, Path, Path, list[dict]]:
        sequence: list[Path] = []
        sequence_durations: list[float] = []
        if self.settings.intro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    intro_card, work_dir / "intro.mp4", self.settings.intro_seconds
                )
            )
            sequence_durations.append(self.settings.intro_seconds)
        sequence.extend(Path(item["segment"]) for item in turn_segments)
        sequence_durations.extend(float(item["duration"]) for item in turn_segments)
        if self.settings.outro_seconds > 0:
            sequence.append(
                self._silent_card_segment(
                    ending, work_dir / "outro.mp4", self.settings.outro_seconds
                )
            )
            sequence_durations.append(self.settings.outro_seconds)
        joined = work_dir / "joined.mp4"
        sequence_offsets = self._join_with_crossfade(
            sequence,
            sequence_durations,
            joined,
            crossfade_seconds=0.15,
        )

        events: list[dict] = []
        story_sequence_offset = 1 if self.settings.intro_seconds > 0 else 0
        for item_index, item in enumerate(turn_segments):
            sequence_index = story_sequence_offset + item_index
            event_base = sequence_offsets[sequence_index]
            next_cut = (
                sequence_offsets[sequence_index + 1]
                if sequence_index + 1 < len(sequence_offsets)
                else None
            )
            local_events = item.get("subtitle_events")
            if local_events is None:
                raise ValueError("native dialogue assembly requires ASR subtitle events")
            for event in local_events:
                event_start = event_base + float(event["start"])
                event_end = event_base + float(event["end"])
                if next_cut is not None:
                    event_end = min(event_end, next_cut - 0.01)
                event_end = max(event_start + 0.05, event_end)
                events.append(
                    {
                        "unit_id": event["unit_id"],
                        "role": event["role"],
                        "start": event_start,
                        "end": event_end,
                        "text": str(event["text"]),
                        "subtitle_source": event.get(
                            "subtitle_source",
                            "native_audio_asr",
                        ),
                    }
                )
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
        if self.settings.bgm_path:
            run([
                "ffmpeg", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(self.settings.bgm_path),
                "-vf", f"ass='{escaped_ass}'",
                "-filter_complex",
                "[0:a]volume=1.0[voice];[1:a]volume=0.06[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix];"
                "[mix]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", str(self.settings.fps), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-shortest",
                "-movflags", "+faststart", str(output),
            ])
        else:
            run([
                "ffmpeg", "-y", "-i", str(joined), "-vf", f"ass='{escaped_ass}'",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", str(self.settings.fps), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(output),
            ])
        return output, ass, joined, events
