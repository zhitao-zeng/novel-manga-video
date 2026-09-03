#!/usr/bin/env python3
"""Thin media runner: clip_plan.json -> assets -> Seedance 2.5 clips -> ASR -> episode.

  1. build series assets only up to the highest character/location the clips
     reference (asset ids follow StoryBible order, so ids stay stable)
  2. one Seedance request per clip: official-layout prompt + ordered reference images
  3. per clip: native audio, speech chunking (ffmpeg silencedetect), SenseVoice ASR
     per chunk, protected-name correction, hard gate (voice energy, CER <= 0.5),
     one regeneration on failure
  4. subtitles from ASR chunks, normalise to 1080x1920/25fps, crossfade join,
     ASS burn-in, loudnorm; cover and ending cards from real frames; media QC
Every remote task keeps its .task.json sidecar and every step skips work whose
output already exists, so a rerun resumes instead of paying again.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from novel_manga.config import Settings
from novel_manga.models import StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.production_runtime import EpisodeProductionRuntime
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.qc import inspect_media
from novel_manga.render import Renderer
from novel_manga.runtime_backends import correct_protected_lexicon, edit_distance, normalize_text
from novel_manga.sd_dialogue import timed_subtitle_pages
from novel_manga.util import atomic_write_json, media_duration, run
from novel_manga.render import _fit_cover
from dataclasses import replace as dc_replace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import frame_spec, load_profile, styled_bible

POLICY = "thin-media-v10.4-privacy-repair"
ASSET_BUILD_ROUNDS = 6
ASSET_RETRY_SECONDS = 90
MIN_LINE_SIMILARITY = 0.5
MAX_HOLD_SECONDS = 6.0
MAX_CER = 0.5
MIN_PEAK_DB = -35.0
PRIVACY_MARKER = "InputImageSensitiveContentDetected"
STYLIZE_PROMPT = (
    "以参考图为唯一身份依据，把这张角色卡重绘成一眼可辨的中国3D国漫动画角色，不是真人：保持同一人的脸型、年龄段、发型、"
    "胡须、服装款式与配色、站姿和构图完全不变；眼睛略大、五官简化概括、皮肤光滑无毛孔无老年斑、皱纹用动画化的少量线条表现，"
    "布料和头发是干净的三维建模材质，柔和体积光；纯色简洁背景；禁止真人照片质感、真实人物肖像、写实皮肤纹理、文字、Logo或水印。"
)
RETRY_SUFFIX = "\n【质量重试】上一次生成的对白听不清或不完整。保持以上全部内容不变重新生成，每句台词都必须清晰完整地说出。"
SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")


class FramedPhanRouter(PhanRouterMediaProvider):
    """PhanRouter provider whose video ratio and location-card aspect follow the frame."""

    def __init__(self, settings: Settings, frame: dict):
        super().__init__(settings)
        self.frame = frame

    def _video_payload(self, *args, **kwargs):
        payload = super()._video_payload(*args, **kwargs)
        payload["ratio"] = self.frame["video_ratio"]
        return payload

    def create_image(self, prompt, output, reference=None, additional_references=()):
        # Character cards stay portrait (identity references); scene cards
        # take the frame's aspect so a landscape episode gets a landscape set.
        ratio = self.frame["image_ratio"] if Path(output).name.startswith("establishing") else "9:16"
        original_post = self.client.post

        def post(url, *a, **kw):
            body = kw.get("json")
            if isinstance(body, dict) and "aspectRatio" in body:
                kw = {**kw, "json": {**body, "aspectRatio": ratio}}
            return original_post(url, *a, **kw)

        self.client.post = post
        try:
            if additional_references:
                return super().create_image(prompt, output, reference=reference, additional_references=additional_references)
            return super().create_image(prompt, output, reference=reference)
        finally:
            self.client.post = original_post


class FramedAssetFactory(SeriesAssetFactory):
    """Asset factory whose scene-card prompt names the frame instead of 9:16."""

    frame_text = "竖屏9:16"

    def _location_prompt(self, bible, location):  # type: ignore[override]
        prompt = SeriesAssetFactory._location_prompt(bible, location)
        return prompt.replace("9:16", self.frame_text.split("屏")[-1]).replace("竖屏", self.frame_text[:2]) if self.frame_text != "竖屏9:16" else prompt


def sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def asset_index(asset_id: str) -> int:
    return int(asset_id.rsplit("_", 1)[1])


def speech_chunks(wav: Path, *, noise_db: float = -30.0, min_silence: float = 0.35, min_chunk: float = 0.4, pad: float = 0.15) -> list[list[float]]:
    duration = media_duration(wav)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    events = [(kind, float(value)) for kind, value in SILENCE_EVENT.findall(result.stderr)]
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in events:
        if kind == "start":
            start = value
        elif kind == "end" and start is not None:
            silences.append((start, value))
            start = None
    if start is not None:
        silences.append((start, duration))
    chunks: list[list[float]] = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        if silence_start - cursor >= min_chunk:
            chunks.append([cursor, silence_start])
        cursor = silence_end
    if duration - cursor >= min_chunk:
        chunks.append([cursor, duration])
    if not chunks:
        return [[0.0, duration]]
    merged: list[list[float]] = []
    for chunk in chunks:
        if merged and chunk[0] - merged[-1][1] < 0.3:
            merged[-1][1] = chunk[1]
        else:
            merged.append(chunk)
    return [[round(max(0.0, s - pad), 3), round(min(duration, e + pad), 3)] for s, e in merged]


def lexicon_aliases() -> dict[str, str]:
    raw = os.getenv("NOVEL_ASR_PROTECTED_LEXICON_JSON", "{}")
    try:
        table = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    aliases: dict[str, str] = {}
    if isinstance(table, dict):
        for canonical, values in table.items():
            if isinstance(values, list):
                for alias in values:
                    aliases[str(alias)] = str(canonical)
    return aliases


class ThinMediaRunner:
    def __init__(self, *, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None):
        self.novel_dir = novel_dir
        self.episode_dir = episode_dir
        self.profile = profile or load_profile(novel_dir)
        # Named frame_spec: ``self.frame`` is the frame-extraction method.
        self.frame_spec = frame_spec(self.profile)
        # Canvas follows the frame; everything downstream (mux scale/crop, ASS
        # PlayRes, QC resolution) reads settings.width/height.
        self.settings = dc_replace(settings, width=self.frame_spec["width"], height=self.frame_spec["height"])
        self.bible = styled_bible(bible, self.profile) if (novel_dir / "profile.json").is_file() else bible
        self.workers = workers
        self.max_attempts = max_attempts
        self.provider = FramedPhanRouter(self.settings, self.frame_spec)
        self.renderer = Renderer(self.settings)
        self.work = episode_dir / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self.clip_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
        self.script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
        asr_command = os.environ.get("NOVEL_ASR_COMMAND", "")
        self.asr_python = shlex.split(asr_command)[0] if asr_command else sys.executable
        self.asr_helper = Path(__file__).resolve().parent / "thin_asr_segments.py"
        self.protected_terms = list(dict.fromkeys([*(c.name for c in bible.characters), *(l.split("：", 1)[0] for l in bible.locations)]))
        self.aliases = lexicon_aliases()

    # ---- assets ----
    def build_assets(self):
        needed_characters = 0
        needed_locations = 0
        for clip in self.clip_plan["clips"]:
            for ref in clip.get("references", []):
                if ref["role"] == "character":
                    needed_characters = max(needed_characters, asset_index(ref["asset_id"]))
                else:
                    needed_locations = max(needed_locations, asset_index(ref["asset_id"]))
        filtered = self.bible.model_copy(update={
            "characters": self.bible.characters[:needed_characters],
            "locations": self.bible.locations[:needed_locations],
        })
        log(f"assets: {needed_characters} characters x2 images + {needed_locations} locations")
        factory = FramedAssetFactory(self.settings, self.provider)
        factory.frame_text = self.frame_spec["text"]
        log(f"profile: style={self.profile['style']} frame={self.profile['frame']} canvas={self.settings.width}x{self.settings.height}")
        # Purge unreadable images BEFORE the factory runs.  A corrupt card is
        # not just a bad output: the factory feeds a character turnaround in as
        # the reference for its expression card, so one truncated download makes
        # every dependent request fail with an unrelated-looking error.
        self.purge_unreadable(self.novel_dir / "series_assets")
        manifest = None
        for attempt in range(1, ASSET_BUILD_ROUNDS + 1):
            try:
                manifest = factory.build(self.novel_dir / "series_assets", filtered)
            except (RuntimeError, TimeoutError, OSError) as error:
                # The hosted image service returns "图片生成失败，请稍后重试" during
                # its own incidents.  That is transient, so back off instead of
                # losing the whole chapter.
                if attempt == ASSET_BUILD_ROUNDS:
                    raise
                log(f"assets: round {attempt} failed ({type(error).__name__}: {str(error)[:110]}); retrying in {ASSET_RETRY_SECONDS}s")
                time.sleep(ASSET_RETRY_SECONDS)
                continue
            broken = self.broken_assets(manifest)
            if not broken:
                break
            # The hosted image CDN can serve an HTML notice or a truncated body;
            # `_download` stores those bytes verbatim, and reuse-existing-assets
            # would then lock the bad file in forever.  Drop it and regenerate.
            for path in broken:
                log(f"assets: {path.name} in {path.parent.name} is not a readable image, regenerating")
                for sidecar in (path, path.with_suffix(path.suffix + ".request.json"), path.with_suffix(path.suffix + ".task.json")):
                    sidecar.unlink(missing_ok=True)
            if attempt == ASSET_BUILD_ROUNDS:
                raise RuntimeError(f"asset images still unreadable after {ASSET_BUILD_ROUNDS} rounds: {[str(p) for p in broken]}")
        for clip in self.clip_plan["clips"]:
            for ref in clip.get("references", []):
                path = self.novel_dir / ref["path"]
                if not path.is_file():
                    raise RuntimeError(f"reference image missing after asset build: {path}")
        log("assets ready")
        return manifest

    @staticmethod
    def purge_unreadable(root: Path) -> list[Path]:
        """Delete asset images that are missing bytes or are not images at all."""
        removed: list[Path] = []
        for path in sorted(root.rglob("*.jpeg")):
            try:
                if path.stat().st_size < 20000:
                    raise ValueError("too small to be a generated card")
                with Image.open(path) as image:
                    image.load()
                continue
            except Exception as error:
                log(f"assets: discarding unreadable {path.parent.name}/{path.name} ({type(error).__name__})")
                for sidecar in (path, path.with_suffix(path.suffix + ".request.json"), path.with_suffix(path.suffix + ".task.json")):
                    sidecar.unlink(missing_ok=True)
                removed.append(path)
        return removed

    def broken_assets(self, manifest) -> list[Path]:
        """Return asset images that are missing or that PIL cannot fully decode."""
        paths: list[Path] = []
        for record in [*manifest.characters, *manifest.locations]:
            for value in (record.primary_image, getattr(record, "secondary_image", None)):
                if value:
                    paths.append(self.novel_dir / value)
        broken: list[Path] = []
        for path in dict.fromkeys(paths):
            if not path.is_file() or path.stat().st_size < 20000:
                broken.append(path)
                continue
            try:
                with Image.open(path) as image:
                    image.load()
            except Exception:
                broken.append(path)
        return broken

    # ---- one clip ----
    def generate_clip(self, clip: dict, attempt: int) -> Path:
        directory = self.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "clip.mp4"
        prompt = clip["prompt"] + (RETRY_SUFFIX if attempt > 1 else "")
        references = tuple(self.novel_dir / ref["path"] for ref in clip.get("references", []))
        request = {
            "clip_id": clip["clip_id"], "attempt": attempt, "duration": clip["request_seconds"],
            "prompt": prompt, "references": [str(p) for p in references], "workflow": "thin-seedance-native-dialogue-v1",
        }
        if (directory / "request.json").is_file() and output.is_file() and output.stat().st_size > 0:
            # Reuse only a clip generated from this exact prompt and references.
            # Keying on the path alone silently served a stale clip after the
            # chapter was re-planned.
            saved = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            if saved.get("prompt") == prompt and saved.get("references") == [str(p) for p in references] and int(saved.get("duration", 0)) == int(clip["request_seconds"]):
                log(f"{clip['clip_id']} attempt {attempt}: clip matches this request, skipping generation")
                return output
            # Move the clip AND its provider task sidecar aside together: the
            # provider refuses to reuse a task whose request hash differs, and
            # a leftover sidecar would make every changed clip fail at submit.
            for name in ("clip.mp4", "clip.mp4.task.json", "clip.mp4.partial", "native.wav", "asr.json", "asr_raw.json", "chunks.json"):
                source = directory / name
                if source.exists():
                    target = directory / name.replace("clip.mp4", "clip.stale.mp4").replace("native.wav", "native.stale.wav").replace("asr", "stale_asr").replace("chunks", "stale_chunks")
                    target.unlink(missing_ok=True)
                    source.rename(target)
            log(f"{clip['clip_id']} attempt {attempt}: request changed since the cached clip, regenerating")
        atomic_write_json(directory / "request.json", request)
        log(f"{clip['clip_id']} attempt {attempt}: requesting {clip['request_seconds']}s video with {len(references)} references")
        started = time.monotonic()
        self.provider.create_video(prompt, None, output, duration=float(clip["request_seconds"]), additional_images=references)
        log(f"{clip['clip_id']} attempt {attempt}: video ready in {time.monotonic() - started:.0f}s ({media_duration(output):.1f}s long)")
        return output

    def analyse_clip(self, clip: dict, video: Path) -> dict:
        directory = video.parent
        wav = directory / "native.wav"
        if not wav.is_file():
            run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)])
        asr_path = directory / "asr.json"
        reference = clip.get("spoken_text", "")
        if asr_path.is_file():
            return json.loads(asr_path.read_text(encoding="utf-8"))
        mean_db, peak_db = EpisodeProductionRuntime._audio_levels(wav)
        chunks = speech_chunks(wav) if reference else []
        rows: list[dict] = []
        if chunks:
            segments_path = directory / "chunks.json"
            segments_path.write_text(json.dumps(chunks), encoding="utf-8")
            raw_out = directory / "asr_raw.json"
            subprocess.run([self.asr_python, str(self.asr_helper), "--audio", str(wav), "--segments", str(segments_path), "--output", str(raw_out)], check=True, capture_output=True, text=True)
            for row in json.loads(raw_out.read_text(encoding="utf-8"))["segments"]:
                corrected, corrections = correct_protected_lexicon(row["hypothesis"], reference, self.protected_terms, self.aliases)
                rows.append({**row, "raw_hypothesis": row["hypothesis"], "hypothesis": corrected, "corrections": corrections})
        hypothesis = "".join(row["hypothesis"] for row in rows)
        reference_key = normalize_text(reference)
        hypothesis_key = normalize_text(hypothesis)
        cer = round(edit_distance(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
        issues = []
        if reference_key:
            if not hypothesis_key or peak_db is None or peak_db < MIN_PEAK_DB:
                issues.append("voice_energy_missing")
            if cer > MAX_CER:
                issues.append(f"cer_{cer}_over_{MAX_CER}")
        result = {
            "clip_id": clip["clip_id"], "video": str(video), "duration": round(media_duration(video), 3),
            "reference": reference, "hypothesis": hypothesis, "cer": cer, "mean_volume_db": mean_db, "max_volume_db": peak_db,
            "chunks": rows, "issues": issues, "passed": not issues,
        }
        atomic_write_json(asr_path, result)
        return result

    def repair_privacy_cards(self, clip: dict) -> list[str]:
        """Redraw the character cards unique to a rejected clip as clearly animated.

        Seedance's privacy detector treats a near-photoreal CG face as a real
        person.  Cards (individual views) already used by a clip that generated
        fine are exempt; when every card of the clip is exempt the rejection
        must come from their combination, so all of them are redrawn.
        """
        exempt = set(getattr(self, "_ok_assets", set()))
        cards = [ref for ref in clip.get("references", []) if ref["role"] == "character"]
        candidates = [ref for ref in cards if ref["path"] not in exempt] or cards
        repaired: list[str] = []
        for ref in candidates:
            path = self.novel_dir / ref["path"]
            if not path.is_file() or str(path) in repaired:
                continue
            backup = path.with_suffix(".photoreal-rejected.jpeg")
            if not backup.exists():
                path.rename(backup)
            # The provider refuses a new prompt against an old task sidecar;
            # park the sidecars with the backup so the redraw is a fresh task.
            for suffix in (".task.json", ".request.json"):
                sidecar = path.with_suffix(path.suffix + suffix)
                if sidecar.exists():
                    sidecar.rename(backup.with_suffix(backup.suffix + suffix))
            log(f"privacy repair: redrawing {ref['asset_id']}/{path.name} as stylized 3D from {backup.name}")
            self.provider.create_image(STYLIZE_PROMPT, path, reference=backup)
            with Image.open(path) as image:
                image.load()
            atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {
                "origin": "privacy-stylized-redraw", "source": backup.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT),
                "request_sha256": sha256_text(STYLIZE_PROMPT + backup.name), "reason": "Seedance InputImageSensitiveContentDetected",
            })
            repaired.append(str(path))
        return [f"{ref['asset_id']}/{Path(ref['path']).name}" for ref in candidates if str(self.novel_dir / ref["path"]) in repaired]

    def process_clip(self, clip: dict) -> dict:
        attempts: list[dict] = []
        privacy_repairs = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                video = self.generate_clip(clip, attempt)
            except RuntimeError as error:
                if PRIVACY_MARKER in str(error) and privacy_repairs == 0:
                    privacy_repairs += 1
                    repaired = self.repair_privacy_cards(clip)
                    log(f"{clip['clip_id']}: reference rejected as a real person; redrew {repaired or 'nothing'}; retrying")
                    if repaired:
                        continue
                raise
            analysis = self.analyse_clip(clip, video)
            if not hasattr(self, "_ok_assets"):
                self._ok_assets = set()
            self._ok_assets.update(ref["path"] for ref in clip.get("references", []))
            attempts.append(analysis)
            log(f"{clip['clip_id']} attempt {attempt}: cer={analysis['cer']} peak={analysis['max_volume_db']} dB issues={analysis['issues']}")
            if analysis["passed"]:
                break
        selected = next((row for row in attempts if row["passed"]), attempts[-1])
        return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": selected}

    # ---- assembly ----
    def script_lines(self, clip_id: str) -> list[str]:
        clip = next((c for c in self.clip_plan["clips"] if c["clip_id"] == clip_id), None)
        return [line["text"] for line in (clip or {}).get("lines", []) if normalize_text(line["text"])]

    @staticmethod
    def align_chunks(lines: list[str], chunks: list[dict], threshold: float = MIN_LINE_SIMILARITY) -> list[tuple[dict, str | None, float]]:
        """Map ASR chunks onto script text by sliding a window over the lines.

        The script is flattened to one normalised character sequence with a
        pointer back to the original text.  Each chunk may match a fragment of
        a line or run across two lines; a chunk that resembles nothing near the
        cursor is ad-lib noise and gets no subtitle.  Returns
        (chunk, matched original text or None, score).
        """
        flat: list[tuple[str, int, int]] = []  # (normalised char, line index, original index)
        for line_index, line in enumerate(lines):
            for original_index, character in enumerate(line):
                if normalize_text(character):
                    flat.append((normalize_text(character), line_index, original_index))
        keys = "".join(item[0] for item in flat)
        line_starts = {}
        for position, (_, line_index, _) in enumerate(flat):
            line_starts.setdefault(line_index, position)
        cursor = 0
        results: list[tuple[dict, str | None, float]] = []

        def original_span(start: int, end: int) -> str:
            pieces = []
            current_line = None
            for position in range(start, end):
                _, line_index, original_index = flat[position]
                if line_index != current_line:
                    current_line = line_index
                    pieces.append([line_index, original_index, original_index])
                pieces[-1][2] = original_index
            text = ""
            for line_index, first, last in pieces:
                line = lines[line_index]
                stop = last + 1
                while stop < len(line) and not normalize_text(line[stop]):
                    stop += 1
                text += line[first:stop]
            return text

        for chunk in chunks:
            hypothesis = normalize_text(chunk["hypothesis"])
            if not hypothesis:
                results.append((chunk, None, 0.0))
                continue
            candidates = {cursor}
            current_line = flat[cursor][1] if cursor < len(flat) else None
            if current_line is not None:
                for line_index in (current_line + 1, current_line + 2):
                    if line_index in line_starts:
                        candidates.add(line_starts[line_index])
            best = (0.0, None, None)
            for start in sorted(candidates):
                low = max(2, int(len(hypothesis) * 0.6))
                high = min(len(keys) - start, int(len(hypothesis) * 1.5) + 2)
                for width in range(low, high + 1):
                    span = keys[start:start + width]
                    score = 1.0 - edit_distance(span, hypothesis) / max(len(span), len(hypothesis))
                    if score > best[0]:
                        best = (score, start, width)
            score, start, width = best
            if start is None or score < threshold:
                results.append((chunk, None, round(score, 3)))
                continue
            results.append((chunk, original_span(start, start + width), round(score, 3)))
            cursor = start + width
        return results

    def subtitle_events(self, clip_id: str, analysis: dict) -> list[dict]:
        """Time subtitles by ASR chunks but print the script wording.

        Seedance adds unscripted crowd noises; raw ASR of those becomes garbage
        text on screen, so only chunks that align with script text get subtitles.
        """
        events = []
        for row, text, score in self.align_chunks(self.script_lines(clip_id), analysis["chunks"]):
            row["match_score"] = score
            row["matched_lines"] = text or ""
            if not text:
                row["subtitle"] = "dropped_adlib" if normalize_text(row["hypothesis"]) else "silent"
                continue
            row["subtitle"] = "script_span"
            for page in timed_subtitle_pages(text, float(row["start"]), float(row["end"])):
                events.append({"unit_id": clip_id, "role": "dialogue", "start": float(page["start"]), "end": float(page["end"]), "text": str(page["text"]), "subtitle_source": "native_audio_asr"})
        return events

    def _font(self, size: int):
        from PIL import ImageFont
        return ImageFont.truetype(str(self.settings.font_path), size)

    def landscape_cover(self, background: Path, output: Path, novel_title: str, art_title: str, label: str) -> Path:
        from PIL import ImageDraw
        W, H = self.settings.width, self.settings.height
        with Image.open(background).convert("RGB") as source:
            image = _fit_cover(source, W, H).convert("RGBA")
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(shade)
        for x in range(0, W // 2):
            draw.line((x, 0, x, H), fill=(6, 10, 20, int(190 * (1 - x / (W / 2)))))
        image = Image.alpha_composite(image, shade)
        draw = ImageDraw.Draw(image)
        draw.text((90, 70), novel_title, font=self._font(48), fill=(240, 197, 91), stroke_width=2, stroke_fill=(10, 8, 6))
        y = 150
        for line in [art_title[i:i + 6] for i in range(0, len(art_title), 6)][:2]:
            draw.text((86, y), line, font=self._font(132), fill=(255, 250, 235), stroke_width=6, stroke_fill=(14, 10, 8))
            y += 150
        draw.line((92, y + 10, 92 + 520, y + 10), fill=(238, 196, 93, 220), width=4)
        box = (W - 300, 64, W - 80, 156)
        draw.rounded_rectangle(box, radius=14, fill=(150, 26, 24))
        text_w = draw.textbbox((0, 0), label, font=self._font(52))[2]
        draw.text(((box[0] + box[2] - text_w) / 2, 74), label, font=self._font(52), fill=(255, 246, 218))
        output.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
        return output

    def landscape_card(self, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
        from PIL import ImageDraw
        W, H = self.settings.width, self.settings.height
        with Image.open(background).convert("RGB") as source:
            image = _fit_cover(source, W, H).convert("RGBA")
        image = Image.alpha_composite(image, Image.new("RGBA", (W, H), (8, 12, 24, 150)))
        draw = ImageDraw.Draw(image)
        title_w = draw.textbbox((0, 0), novel_title, font=self._font(56))[2]
        draw.text(((W - title_w) / 2, H * 0.30), novel_title, font=self._font(56), fill=(255, 246, 218))
        label_w = draw.textbbox((0, 0), label, font=self._font(140))[2]
        draw.text(((W - label_w) / 2, H * 0.40), label, font=self._font(140), fill=(248, 205, 92), stroke_width=6, stroke_fill=(14, 10, 8))
        draw.line((W / 2 - 260, H * 0.72, W / 2 + 260, H * 0.72), fill=(238, 196, 93, 200), width=3)
        sub_w = draw.textbbox((0, 0), subtitle, font=self._font(48))[2]
        draw.text(((W - sub_w) / 2, H * 0.72 + 30), subtitle, font=self._font(48), fill=(255, 248, 228))
        output.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(output, "JPEG", quality=95, subsampling=0)
        return output

    def frame(self, video: Path, second: float, output: Path) -> Path:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{second:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])
        return output

    def assemble(self, results: list[dict]) -> dict:
        video_id = self.episode_dir.name
        turn_segments = []
        for record in results:
            selected = record["selected"]
            clip_video = Path(selected["video"])
            wav = clip_video.parent / "native.wav"
            segment, duration = self.renderer.mux_visual_group(clip_video, wav, self.work / "segments" / f"{record['clip_id']}.mp4")
            turn_segments.append({"unit_id": record["clip_id"], "role": "dialogue", "segment": str(segment), "duration": duration, "audio_source": "native_dialogue", "subtitle_events": self.subtitle_events(record["clip_id"], selected)})
        first_video = Path(results[0]["selected"]["video"])
        last_video = Path(results[-1]["selected"]["video"])
        cover_frame = self.frame(first_video, min(1.5, max(0.1, media_duration(first_video) - 0.2)), self.work / "cover_frame.jpeg")
        ending_frame = self.frame(last_video, max(0.1, media_duration(last_video) - 0.6), self.work / "ending_frame.jpeg")
        chapter_title = self.script.get("video_title") or self.bible.novel_title
        art_title = EpisodeProductionRuntime._cover_title(self.script.get("source_title") or "", chapter_title)
        cover = self.episode_dir / f"{video_id}_cover.jpeg"
        ending = self.episode_dir / f"{video_id}_ending.jpeg"
        # Episode number from the directory name: "<novel>_3" or "<novel>_1-grammar".
        number_match = re.search(r"_(\d+)(?:-[A-Za-z0-9]+)?$", video_id)
        episode_number = int(number_match.group(1)) if number_match else 1
        if self.settings.width > self.settings.height:
            self.landscape_cover(cover_frame, cover, self.bible.novel_title, art_title, f"第{episode_number:02d}集")
            self.landscape_card(ending_frame, ending, self.bible.novel_title, "未完待续", "敬请期待下一集")
        else:
            self.renderer.make_cover(cover_frame, cover, novel_title=self.bible.novel_title, art_title=art_title, episode_label=f"第{episode_number:02d}集")
            self.renderer.make_card(ending_frame, ending, self.bible.novel_title, "未完待续", "敬请期待下一集")
        final_video = self.episode_dir / f"{video_id}.mp4"
        # FFmpeg 4.4 on this host freezes an input at its first frame inside
        # multi-input xfade chains (the renderer only avoids that above 16
        # segments).  Short drama cuts hard anyway, so join with concat.
        original_join = self.renderer._join_with_crossfade

        def hard_cut_join(sequence, durations, output, *, crossfade_seconds=0.15):
            return original_join(sequence, durations, output, crossfade_seconds=0.0)

        self.renderer._join_with_crossfade = hard_cut_join
        final, ass, joined, events = self.renderer.assemble_production(cover, ending, turn_segments, final_video, self.work)
        qc = inspect_media(final, cover, ending, ass, self.settings, self.episode_dir / "media_qc_report.json")
        freeze = float(qc.get("checks", {}).get("long_freeze", {}).get("detail", {}).get("max_freeze_seconds", 0.0))
        other_checks = [v.get("passed") for k, v in qc.get("checks", {}).items() if k != "long_freeze"]
        thin_passed = all(other_checks) and freeze <= MAX_HOLD_SECONDS
        return {"final_video": str(final), "cover": str(cover), "ending": str(ending), "ass": str(ass), "duration": round(media_duration(final), 3), "subtitle_events": len(events), "media_qc_passed": bool(qc.get("passed")), "max_hold_seconds": freeze, "thin_passed": thin_passed, "media_qc": qc}

    def run(self) -> dict:
        started = time.monotonic()
        self.build_assets()
        clips = [clip for clip in self.clip_plan["clips"] if clip["kind"] == "video"]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(self.process_clip, clips))
        failed = [r["clip_id"] for r in results if not r["selected"]["passed"]]
        assembly = self.assemble(results)
        report = {
            "policy": POLICY, "episode": self.episode_dir.name, "elapsed_seconds": round(time.monotonic() - started, 1),
            "clips": results, "gate_failed_clips": failed, "assembly": assembly,
        }
        atomic_write_json(self.episode_dir / "thin_media_report.json", report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode", required=True, help="episode dir name under novel dir, e.g. fentian-thin-v4_1")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.episode
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    profile = load_profile(novel_dir, style=args.style, frame=args.frame)
    runner = ThinMediaRunner(novel_dir=novel_dir, episode_dir=episode_dir, settings=settings, bible=bible, workers=args.workers, max_attempts=args.max_attempts, profile=profile)
    clips = [c for c in runner.clip_plan["clips"] if c["kind"] == "video"]
    summary = {
        "profile": runner.profile, "canvas": f"{runner.settings.width}x{runner.settings.height}",
        "settings": {"provider": settings.provider, "image_model": settings.image_model, "video_model": settings.video_model, "admission_mode": settings.admission_mode, "poll_timeout": settings.poll_timeout, "outro_seconds": settings.outro_seconds, "font": str(settings.font_path)},
        "asr_python": runner.asr_python, "asr_helper": str(runner.asr_helper), "protected_terms": runner.protected_terms, "alias_count": len(runner.aliases),
        "clips": [{"clip_id": c["clip_id"], "seconds": c["request_seconds"], "references": [r["path"] for r in c["references"]], "spoken_chars": len(normalize_text(c.get("spoken_text", "")))} for c in clips],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    report = runner.run()
    print(json.dumps({k: report[k] for k in ("elapsed_seconds", "gate_failed_clips")} | {"assembly": {k: v for k, v in report["assembly"].items() if k != "media_qc"}} | {"clips": [{"clip_id": r["clip_id"], "attempts": len(r["attempts"]), "cer": r["selected"]["cer"], "peak_db": r["selected"]["max_volume_db"], "duration": r["selected"]["duration"]} for r in report["clips"]]}, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["assembly"]["thin_passed"] and not report["gate_failed_clips"] else 2


if __name__ == "__main__":
    sys.exit(main())
