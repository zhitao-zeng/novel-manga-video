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
import fcntl
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import wave
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from novel_manga.config import Settings
from novel_manga.models import StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.production_models import AssetRecord, SeriesAssetManifest
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
import chat_card
from thin_profile import frame_spec, is_fast, load_genre, load_profile, plan_fingerprint, styled_bible

POLICY = "thin-media-v22-coverage-gate"
ASSET_BUILD_ROUNDS = 6
ASSET_RETRY_SECONDS = 90
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them
VOICE_BUDGET_SECONDS = 29.0  # Seedance 2.5 caps reference audio at 30.2 s per request
VOICE_BUDGET_SHORT_SECONDS = 15.0  # Seedance 2.0 (the 15 s lane) caps it at 15.2 s


def voice_budget_seconds() -> float:
    """The reference-audio budget of this lane, from its clip length cap."""
    try:
        cap = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30") or 30)
    except ValueError:
        cap = 30.0
    return VOICE_BUDGET_SHORT_SECONDS if cap <= 15 else VOICE_BUDGET_SECONDS


def trimmed_voice(path: Path, seconds: float) -> Path:
    """A copy of the voice sample cut to `seconds`, cached next to the bank."""
    out = path.parent / ".trim" / f"{path.stem}.{seconds:g}s.wav"
    if not out.is_file() or out.stat().st_mtime < path.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_suffix(".partial.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", f"{seconds:.2f}", "-c:a", "pcm_s16le", str(partial)])
        os.replace(partial, out)
    return out
MIN_LINE_SIMILARITY = 0.35  # order-constrained match; was 0.5 with a two-line lookahead

# ---- subtitle helpers (v16) ----
CAPTION_LINE_CHARS = 18
CAPTION_TRAILING_PUNCT = "，、。；：,.;:"
CLAUSE_PUNCT = "，。！？；：、…,.!?;:"
DIGIT_NAMES = "零一二三四五六七八九"
MIN_ASR_SECONDS = 0.8   # shorter than this is a murmur, not a line
MIN_ASR_CHARS = 6       # ...and so is a block the recogniser barely heard
SECONDS_PER_CHAR_CAP = 0.45   # a caption never stays longer than 0.8 s + this per character
SECONDS_PER_CHAR_FLOOR = 0.2  # ...and never shorter than this per character (bounded by the next caption)


def subsequence_overlap(reference: str, hypothesis: str) -> int:
    """Length of the longest common subsequence: script characters heard in order.

    Insertions in the hypothesis cost nothing, so an ad-lib or a read-out stage
    direction does not count against the clip; only script text that never
    appears does.
    """
    if not reference or not hypothesis:
        return 0
    previous = [0] * (len(hypothesis) + 1)
    for r in reference:
        current = [0]
        for j, h in enumerate(hypothesis, start=1):
            current.append(previous[j - 1] + 1 if r == h else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def spoken_integer(digits: str) -> str:
    """Read an integer the way it is said in Chinese: 1000 → 一千, 19800 → 一万九千八百, 10500 → 一万零五百."""
    n = int(digits)
    if n == 0:
        return "零"
    units, bigs = ("", "十", "百", "千"), ("", "万", "亿", "万亿")
    groups = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            continue
        s, pending_zero = "", False
        for i in (3, 2, 1, 0):
            d = (g // 10 ** i) % 10
            if d:
                if pending_zero:
                    s += "零"
                s += DIGIT_NAMES[d] + units[i]
                pending_zero = False
            elif s:
                pending_zero = True
        if gi < len(groups) - 1 and g < 1000:
            s = "零" + s
        parts.append(s + bigs[gi])
    text = "".join(parts)
    return text[1:] if text.startswith("一十") else text


def speakable(text: str) -> str:
    """Replace Arabic numbers by their spoken form so script and ASR text compare on equal terms."""
    def read(match: re.Match) -> str:
        token = match.group(0)
        following = text[match.end():match.end() + 1]
        if "." in token:
            whole, _, fraction = token.partition(".")
            return spoken_integer(whole) + "点" + "".join(DIGIT_NAMES[int(d)] for d in fraction)
        if following == "年" or len(token) >= 7 or (token.startswith("0") and len(token) > 1):
            return "".join(DIGIT_NAMES[int(d)] for d in token)
        return spoken_integer(token)
    return re.sub(r"\d+(?:\.\d+)?", read, text)


def match_key(text: str) -> str:
    """Normalised comparison text: spoken numbers, 两 read as 二, no punctuation."""
    return normalize_text(speakable(text)).replace("两", "二")


def balanced_split(text: str, width: int = CAPTION_LINE_CHARS) -> list[str]:
    """Split one caption into two lines near the middle, at a clause boundary when one is close enough."""
    if len(text) <= width:
        return [text]
    middle = len(text) / 2
    candidates = [i for i in range(2, len(text) - 1) if text[i - 1] in CLAUSE_PUNCT and i <= width and len(text) - i <= width]
    if candidates:
        cut = min(candidates, key=lambda i: abs(i - middle))
    else:
        cut = max(1, min(len(text) - 1, round(middle)))
    return [text[:cut], text[cut:]]


def tidy_page(page: str) -> str:
    """Rebalance orphaned second lines and drop trailing commas/periods at line ends."""
    lines = [line.strip() for line in page.split(r"\N") if line.strip()]
    if len(lines) == 2 and (len(normalize_text(lines[0])) <= 2 or len(normalize_text(lines[1])) <= 2):
        lines = balanced_split(lines[0] + lines[1])
    lines = [line.rstrip(CAPTION_TRAILING_PUNCT) for line in lines]
    lines = [line for line in lines if normalize_text(line)]
    return r"\N".join(lines) if lines else page.rstrip(CAPTION_TRAILING_PUNCT)


def classify_unmatched(heard: str, seconds: float) -> str:
    """Decide what to do with speech that matches no script line.

    Only objective properties are used: how long the block is and how much text
    the recogniser produced.  Short murmurs stay silent; anything longer is shown
    as heard, ad-libs and the occasional stage direction the model read aloud
    alike.
    """
    key = normalize_text(heard)
    if not key:
        return "silent"
    if seconds < MIN_ASR_SECONDS or len(key) < MIN_ASR_CHARS:
        return "dropped_murmur"
    return "asr_text"


MAX_HOLD_SECONDS = 6.0
MAX_CER = 0.5          # kept in the report; no longer gates
MAX_MISSING = 0.5      # more than half of the script characters never spoken -> retry once
MIN_PEAK_DB = -35.0
PRIVACY_MARKER = "InputImageSensitiveContentDetected"
REDRAW_ORIGIN = "privacy-stylized-redraw"
MODERATION_MARKERS = ("violate", "usage policy", "content policy", "sensitive", "moderation", "safety", "违规", "敏感", "审核")
SCRUB_WORDS = re.compile(r"妩媚|性感|曼妙|露肩|低胸|大腿|俗气|轻浮|挑逗|妖艳|夸张")
SAFE_SUFFIX = "。整体端庄得体，衣着完整，表情自然温和，普通站姿，无任何性暗示、暴力或血腥"


class ModerationRejected(RuntimeError):
    """The image service refused a card even after the prompt was toned down."""


def moderation_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in MODERATION_MARKERS)
REDRAW_WAIT_SECONDS = 180  # a stylised redraw normally lands in 60-100 s; past this the photoreal backup is used
REPAIR_LOCK = threading.Lock()  # one card redraw at a time; parallel repairs of the same card raced
PRIVACY_OK_FILE = "series_assets/.privacy_ok.json"  # cards used by clips that generated fine, shared across runs
CARD_STYLE_SUFFIX_3D = (
    "。整体必须是一眼可辨的风格化三维动画角色（国漫/皮克斯式概括造型）：眼睛略大、五官简化、皮肤光滑无毛孔、"
    "干净的三维建模材质与柔和体积光；绝不是真人照片、真实人物肖像或写实渲染"
)
LOCATION_EMPTY_SUFFIX = "。画面中绝对不出现任何人物、人影、人形剪影或车内乘客，只有空无一人的场景"
STYLIZE_STRONGER = "；比上一版更强的卡通化：头身比略夸张、眼睛明显更大、脸型圆润、皮肤为纯色平光、完全没有真人质感"
STYLIZE_PROMPT = (
    "以参考图为唯一身份依据，把这张角色卡重绘成一眼可辨的中国3D国漫动画角色，不是真人：保持同一人的脸型、年龄段、发型、"
    "胡须、服装款式与配色、站姿和构图完全不变；眼睛略大、五官简化概括、皮肤光滑无毛孔无老年斑、皱纹用动画化的少量线条表现，"
    "布料和头发是干净的三维建模材质，柔和体积光；纯色简洁背景；禁止真人照片质感、真实人物肖像、写实皮肤纹理、文字、Logo或水印。"
)
RETRY_SUFFIX = "\n【质量重试】上一次生成的对白听不清或不完整。保持以上全部内容不变重新生成，每句台词都必须清晰完整地说出。"
OUTPUT_MODERATION_MARKERS = ("OutputVideoSensitiveContentDetected", "OutputAudioSensitiveContentDetected")
RATE_LIMIT_RE = re.compile(r"HTTP (429|502|503|504)\b|Too Many Requests|rate ?limit|concurren|QuotaExceeded|RequestLimit|ServerOverloaded", re.I)
INPUT_TEXT_MARKER = "InputTextSensitiveContentDetected"
SOFTEN = [  # stage descriptions only get milder wording on a text-moderation refusal; spoken lines stay
    (re.compile(r"打死|弄死|杀死|杀了|杀掉|干掉"), "打倒"), (re.compile(r"鲜血|血迹|血液|流血|血"), "伤痕"), (re.compile(r"尸体|死尸"), "倒下的人"),
    (re.compile(r"砍|捅|刺"), "挥"), (re.compile(r"手枪|枪"), "棍棒"), (re.compile(r"毒品|吸毒"), "违禁品"), (re.compile(r"强奸|轮奸|猥亵"), "欺负"),
    (re.compile(r"赌博|赌钱|赌"), "比试"), (re.compile(r"废了你|打断.{0,2}腿|弄残"), "教训你"), (re.compile(r"威胁"), "警告"), (re.compile(r"自杀|上吊|跳楼"), "轻生"),
]


def soften_prompt(prompt: str) -> str:
    for pattern, replacement in SOFTEN:
        prompt = pattern.sub(replacement, prompt)
    return prompt + COMPLIANCE_SUFFIX
SUBMIT_BACKOFF_SECONDS = (30, 60, 90, 120, 180, 240, 300)  # ~17 min of patience when the video service throttles
COMPLIANCE_SUFFIX = "\n【合规】画面健康、日常、无任何暴力、血腥、色情、赌博或违规内容；人物衣着完整；屏幕上的文字仅为剧情中的普通聊天内容；声音只有普通对白、环境音效和无歌词的哼唱，不含任何已有歌曲、歌词或背景音乐。"
FEEDBACK_FILE = "review_feedback.json"  # {clip_id: 导演修正}, written by the automatic episode review
SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")


class FramedPhanRouter(PhanRouterMediaProvider):
    """PhanRouter provider whose video ratio and location-card aspect follow the frame."""

    def __init__(self, settings: Settings, frame: dict, resolution: str = "720p"):
        super().__init__(settings)
        self.frame = frame
        self.resolution = resolution
        self._tls = threading.local()
        original_post = self.client.post

        def post(url, *a, **kw):
            # Installed once for the shared HTTP client.  Only an image request
            # issued by this thread's create_image carries an aspect override;
            # video submissions from other threads pass through untouched.
            ratio = getattr(self._tls, "ratio", None)
            body = kw.get("json")
            if ratio and isinstance(body, dict) and "aspectRatio" in body:
                kw = {**kw, "json": {**body, "aspectRatio": ratio}}
            return original_post(url, *a, **kw)

        self.client.post = post

    def _video_payload(self, *args, **kwargs):
        payload = super()._video_payload(*args, **kwargs)
        payload["ratio"] = self.frame["video_ratio"]
        payload["resolution"] = self.resolution
        return payload

    def create_image(self, prompt, output, reference=None, additional_references=()):
        # Character cards stay portrait (identity references); scene cards
        # take the frame's aspect so a landscape episode gets a landscape set.
        self._tls.ratio = self.frame["image_ratio"] if Path(output).name.startswith("establishing") else "9:16"
        try:
            if additional_references:
                return super().create_image(prompt, output, reference=reference, additional_references=additional_references)
            return super().create_image(prompt, output, reference=reference)
        finally:
            self._tls.ratio = None


class FramedAssetFactory(SeriesAssetFactory):
    """Asset factory whose scene-card prompt names the frame instead of 9:16."""

    frame_text = "竖屏9:16"

    def _location_prompt(self, bible, location):  # type: ignore[override]
        prompt = SeriesAssetFactory._location_prompt(bible, location)
        return prompt.replace("9:16", self.frame_text.split("屏")[-1]).replace("竖屏", self.frame_text[:2]) if self.frame_text != "竖屏9:16" else prompt

    def ensure_card(self, prompt: str, output: Path, *, reference=None):
        """_ensure_image, and on a content-moderation refusal one retry with a
        toned-down prompt; a second refusal is final (no point in more rounds)."""
        try:
            return self._ensure_image(prompt, output, reference=reference)
        except RuntimeError as error:
            if not moderation_error(error):
                raise
            safe = SCRUB_WORDS.sub("", prompt) + SAFE_SUFFIX
            log(f"assets: {output.parent.name}/{output.name} refused by content moderation; retrying with a toned-down prompt")
            try:
                return self._ensure_image(safe, output, reference=reference)
            except RuntimeError as again:
                if moderation_error(again):
                    raise ModerationRejected(f"{output.parent.name}/{output.name}: {str(again)[:200]}") from again
                raise

    def build_selected(self, root: Path, bible: StoryBible, character_ids: set[str], location_ids: set[str], expressions: bool = True) -> SeriesAssetManifest:
        """Build (or reuse) only the listed assets; ids stay the bible positions.

        The base ``build`` renders every character and location in the bible.
        A long novel's bible grows to hundreds of entries, so an episode only
        pays for the cards it references; records are merged into the manifest.
        """
        root.mkdir(parents=True, exist_ok=True)
        style_master = self.settings.style_master_path
        guard = (
            "【系列母版继承】参考图只锁定线稿粗细、二维平涂、赛璐璐阴影、色彩亮度、"
            "光影方向和整体动画制作规格；不得照抄参考图人物身份、脸型、发型、服装、姿势、"
            "场景结构或具体构图，必须严格按当前资产描述重新设计。"
            if style_master is not None else ""
        )
        manifest_path = root / "manifest.json"
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        characters = {row["asset_id"]: row for row in existing.get("characters", [])}
        locations = {row["asset_id"]: row for row in existing.get("locations", [])}
        voices = dict(existing.get("voice_assignments") or {"narrator": "native:narrator"})
        for index, character in enumerate(bible.characters, start=1):
            asset_id = f"character_{index:03d}"
            if asset_id not in character_ids:
                continue
            directory = root / "characters" / asset_id
            prompt = self._character_prompt(
                bible, character.name, character.appearance, character.base_costume or character.wardrobe,
                visual_archetype=character.visual_archetype, face_anchors=character.face_anchors, silhouette=character.silhouette,
                hair=character.hair, palette=character.palette, motion_signature=character.motion_signature,
            ) + guard
            if "3D" in bible.visual_style or "三维" in bible.visual_style:
                # Modern-dress 3D cards came out near-photoreal and were then
                # redrawn by the review; ask for the animated look up front.
                prompt += CARD_STYLE_SUFFIX_3D
            invariants = [value for value in (character.appearance, *character.face_anchors, character.silhouette, character.hair) if value]
            state = {"costume": character.base_costume or character.wardrobe, "injury": "none unless changed by source events", "carried_prop": character.signature_prop or "none"}
            scope = {"inherit": ["identity", "hair", "costume", "2d_rendering"], "exclude": ["pose", "composition", "camera", "background", "lighting"]}
            atomic_write_json(directory / "spec.json", {
                "asset_id": asset_id, "name": character.name, "role": character.role, "gender": character.gender, "age": character.age,
                "appearance": character.appearance, "wardrobe": character.wardrobe, "visual_archetype": character.visual_archetype,
                "face_anchors": character.face_anchors, "silhouette": character.silhouette, "hair": character.hair, "palette": character.palette,
                "base_costume": character.base_costume, "episode_costumes": character.episode_costumes, "signature_prop": character.signature_prop,
                "expression_profile": character.expression_profile, "motion_signature": character.motion_signature, "voice_profile_id": character.voice_profile_id,
                "version": "v001", "identity_invariants": invariants, "state_variables": state, "reference_scope": scope,
                "style_fingerprint": bible.style_fingerprint, "prompt": prompt,
            })
            primary = self.ensure_card(prompt, directory / "turnaround.jpeg", reference=style_master)
            expression_prompt = self._expression_prompt(bible, character.name, character.expression_profile)
            # The fast tier references one card per character, so its expression
            # card would be paid for and never used.
            secondary = self.ensure_card(expression_prompt, directory / "expressions.jpeg", reference=primary.path) if expressions or (directory / "expressions.jpeg").is_file() else None
            characters[asset_id] = AssetRecord(
                asset_id=asset_id, kind="character", name=character.name, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(primary.path.relative_to(root.parent)),
                secondary_image=str(secondary.path.relative_to(root.parent)) if secondary else None, prompt_sha256=sha256_text(prompt + expression_prompt),
            ).model_dump(mode="json")
            voices[character.name] = character.voice_profile_id or f"native:{asset_id}"
        for index, location in enumerate(dict.fromkeys(bible.locations), start=1):
            asset_id = f"location_{index:03d}"
            if asset_id not in location_ids:
                continue
            directory = root / "locations" / asset_id
            prompt = self._location_prompt(bible, location) + guard + LOCATION_EMPTY_SUFFIX
            invariants = [f"{location}固定建筑、出入口和空间层级"]
            state = {"time_of_day": "approved_reference_state", "weather": "approved_reference_state", "damage": "none unless changed by source events"}
            scope = {"inherit": ["architecture", "space", "color", "lighting", "2d_rendering"], "exclude": ["composition", "camera", "temporary_people", "text"]}
            atomic_write_json(directory / "spec.json", {
                "asset_id": asset_id, "name": location, "style_fingerprint": bible.style_fingerprint,
                "continuity": "固定空间布局、物品锚点、天气、时间、光线方向", "version": "v001",
                "identity_invariants": invariants, "state_variables": state, "reference_scope": scope, "prompt": prompt,
            })
            image = self.ensure_card(prompt, directory / "establishing.jpeg", reference=style_master)
            locations[asset_id] = AssetRecord(
                asset_id=asset_id, kind="location", name=location, identity_invariants=invariants, state_variables=state, reference_scope=scope,
                spec_path=str((directory / "spec.json").relative_to(root.parent)), primary_image=str(image.path.relative_to(root.parent)),
                prompt_sha256=sha256_text(prompt),
            ).model_dump(mode="json")
        manifest = SeriesAssetManifest(
            style_fingerprint=bible.style_fingerprint,
            characters=[AssetRecord(**characters[key]) for key in sorted(characters)],
            locations=[AssetRecord(**locations[key]) for key in sorted(locations)],
            voice_assignments=voices,
        )
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest


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



def load_privacy_ok(novel_dir: Path) -> set[str]:
    try:
        return set(json.loads((novel_dir / PRIVACY_OK_FILE).read_text(encoding="utf-8")).get("paths", []))
    except (OSError, ValueError):
        return set()


def record_privacy_ok(novel_dir: Path, paths) -> None:
    """Remember cards that Seedance accepted, so a later run (or a parallel one)
    never redraws a proven card just because it was the first thing rejected.
    The read-modify-write is guarded by a file lock: episodes render in
    parallel processes and finish clips at the same moment."""
    target = novel_dir / PRIVACY_OK_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with REPAIR_LOCK, open(target.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = load_privacy_ok(novel_dir) | {str(p) for p in paths}
        atomic_write_json(target, {"paths": sorted(merged)})


def cards_sheet(novel_dir: Path, output: Path, height: int = 300) -> Path | None:
    """One JPEG with every character card (turnaround + expressions) and every
    location card, for the look-before-you-pay review of a new novel."""
    rows: list[list[Path]] = []
    for card_dir in sorted((novel_dir / "series_assets" / "characters").glob("character_*")):
        views = [card_dir / name for name in ("turnaround.jpeg", "expressions.jpeg") if (card_dir / name).is_file()]
        if views:
            rows.append(views)
    locations = sorted((novel_dir / "series_assets" / "locations").glob("location_*/establishing.jpeg"))
    for index in range(0, len(locations), 4):
        rows.append(locations[index:index + 4])
    if not rows:
        return None
    thumbs: list[list[Image.Image]] = []
    for row in rows:
        thumbs.append([])
        for path in row:
            with Image.open(path) as image:
                image = image.convert("RGB")
                thumbs[-1].append(image.resize((max(1, round(image.width * height / image.height)), height)))
    width = max(sum(t.width for t in row) + 8 * (len(row) + 1) for row in thumbs)
    sheet = Image.new("RGB", (width, len(thumbs) * (height + 8) + 8), (24, 24, 24))
    y = 8
    for row in thumbs:
        x = 8
        for thumb in row:
            sheet.paste(thumb, (x, y))
            x += thumb.width + 8
        y += height + 8
    sheet.save(output, quality=85)
    return output


PRESCREEN_RISK = 0.6


def apply_genre(genre: dict) -> None:
    """Genre preset → card style cue, location-card policy, extra softening pairs."""
    global CARD_STYLE_SUFFIX_3D, LOCATION_EMPTY_SUFFIX, SOFTEN
    if genre.get("card_style_suffix_3d"):
        CARD_STYLE_SUFFIX_3D = genre["card_style_suffix_3d"]
    if genre.get("location_policy") == "sparse":
        LOCATION_EMPTY_SUFFIX = "。主体空无一人：近景和中景不出现任何人物或人形剪影，远处允许少量模糊的背景行人"
    SOFTEN = SOFTEN + [(re.compile(pattern), replacement) for pattern, replacement in genre.get("soften", [])]


def prescreen_prompt(prompt: str) -> float:
    """Ask the local Qwen whether the prompt is likely to trip the video service's
    content filter (violence, gore, sexual content, gambling, drugs, politics)."""
    try:
        from thin_review import ask_json, obj
        verdict = ask_json([{"type": "text", "text": (
            "下面是一段给视频生成模型的提示词。判断它被平台内容审核拒绝的可能性（暴力打斗细节、血腥伤势、色情或挑逗台词、赌博、毒品、自残、敏感政治），"
            "risk 为 0 到 1，reason 一句话。只输出JSON。\n\n" + prompt[:6000])}], obj({"risk": {"type": "number"}, "reason": {"type": "string"}}), name="prescreen", max_tokens=120)
        return float(verdict.get("risk", 0.0))
    except Exception as error:  # noqa: BLE001 - the prescreen is advisory
        log(f"prescreen skipped ({type(error).__name__})")
        return 0.0


def acquire_inflight_slot(novel_dir: Path, limit: int):
    """A cross-process counting semaphore made of lock files: every runner of the
    novel competes for the same `limit` slots, so the clips in flight stay at a
    constant number no matter how many episodes render at once."""
    if limit <= 0:
        return None
    # A second lane (another model behind another key, with its own concurrency
    # allowance) names its pool with NOVEL_INFLIGHT_POOL so the two caps do not
    # share one set of slots.
    pool = os.environ.get("NOVEL_INFLIGHT_POOL", "").strip()
    directory = novel_dir / (f".inflight-{pool}" if pool else ".inflight")
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        # A `limit` file in the pool (the conductor's AIMD on 429s) lowers the
        # cap live; --inflight stays the ceiling.
        effective = limit
        try:
            effective = max(1, min(limit, int((directory / "limit").read_text(encoding="utf-8").strip() or limit)))
        except (OSError, ValueError):
            pass
        for index in range(effective):
            handle = open(directory / f"slot_{index:02d}.lock", "w")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError:
                handle.close()
        time.sleep(3)


def release_inflight_slot(handle) -> None:
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def stylize_card(provider, path: Path) -> Path:
    """Park a near-photoreal card (with its sidecars) and redraw it as clearly animated 3D."""
    backup = path.with_suffix(".photoreal-rejected.jpeg")
    for suffix in ("", ".task.json", ".request.json"):
        source = path.with_suffix(path.suffix + suffix)
        if source.exists():
            target = backup.with_suffix(backup.suffix + suffix)
            target.unlink(missing_ok=True)
            source.rename(target)
    provider.create_image(STYLIZE_PROMPT, path, reference=backup)
    with Image.open(path) as image:
        image.load()
    atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {
        "origin": REDRAW_ORIGIN, "source": backup.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT),
        "request_sha256": sha256_text(STYLIZE_PROMPT + backup.name), "reason": "near-photoreal card",
    })
    return path


def wait_for_inflight_redraws(paths, timeout: float = REDRAW_WAIT_SECONDS) -> list[Path]:
    """A card missing while its .photoreal-rejected.jpeg backup exists is being
    redrawn by another thread or process; wait for it instead of failing (or,
    in the asset factory's case, regenerating a photoreal card in its place)."""
    waited: list[Path] = []
    deadline = time.monotonic() + timeout
    for path in paths:
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        if (path.parent / f".regenerated.{path.name}").exists():
            continue  # deliberately deleted by the card review; the factory will rebuild it
        while not path.is_file() and backup.exists():
            if time.monotonic() > deadline:
                # The redraw is late or keeps failing (an nsfw refusal, say).
                # A photoreal card beats no card and a failed episode: put the
                # backup in place and mark the fix as tried so nobody retries it;
                # a redraw that still lands later simply replaces the file.
                shutil.copy2(backup, path)
                (path.parent / f".regenerated.{path.stem}.txt").touch()
                log(f"redraw of {path.parent.name}/{path.name} did not finish within {timeout:.0f}s; using the photoreal backup")
                break
            if path not in waited:
                waited.append(path)
                log(f"waiting for in-flight redraw of {path.parent.name}/{path.name}")
            time.sleep(5)
    return waited

class ThinMediaRunner:
    def __init__(self, *, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None, inflight: int = 0, prescreen: bool = False):
        self.novel_dir = novel_dir
        self.inflight = inflight  # global cap on clips in flight across every runner of this novel (0 = none)
        self.prescreen = prescreen
        self.episode_dir = episode_dir
        self.profile = profile or load_profile(novel_dir)
        # Named frame_spec: ``self.frame`` is the frame-extraction method.
        self.frame_spec = frame_spec(self.profile)
        # Canvas follows the frame; everything downstream (mux scale/crop, ASS
        # PlayRes, QC resolution) reads settings.width/height.
        self.settings = dc_replace(settings, width=self.frame_spec["width"], height=self.frame_spec["height"])
        self.bible = styled_bible(bible, self.profile) if (novel_dir / "profile.json").is_file() else bible
        self.fast = is_fast(self.profile)
        apply_genre(load_genre(self.profile))
        self._workers_arg = workers  # resolved after clip_plan is loaded (0 = one slot per clip)
        # Fast tier still gets a second attempt, but only when the first one
        # failed the speech gate (the retry loop runs on gate failures alone):
        # a line the model did not speak costs the line and its subtitles.
        self.max_attempts = 2 if self.fast else max_attempts
        self.provider = FramedPhanRouter(self.settings, self.frame_spec, resolution="480p" if self.fast else "720p")
        self.renderer = Renderer(self.settings)
        self.work = episode_dir / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        chat_screen_path = novel_dir / "chat_screen.json"
        self.chat_screen = json.loads(chat_screen_path.read_text(encoding="utf-8")) if chat_screen_path.is_file() else {}
        self.clip_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
        self.script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
        video_clips = [c for c in self.clip_plan["clips"] if c["kind"] == "video"]
        self.workers = self._workers_arg if self._workers_arg > 0 else max(1, len(video_clips))  # no second wave
        # Director corrections from the automatic episode review stay part of the
        # episode's state: they change the prompt (hence the cache key) of the
        # clips they name, so a corrected clip is regenerated exactly once.
        feedback_path = episode_dir / FEEDBACK_FILE
        self.feedback = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.is_file() else {}
        asr_command = os.environ.get("NOVEL_ASR_COMMAND", "")
        self.asr_python = shlex.split(asr_command)[0] if asr_command else sys.executable
        self.asr_helper = Path(__file__).resolve().parent / "thin_asr_segments.py"
        self.protected_terms = list(dict.fromkeys([*(c.name for c in bible.characters), *(l.split("：", 1)[0] for l in bible.locations)]))
        self.aliases = lexicon_aliases()

    # ---- assets ----
    def build_assets(self):
        character_ids = {ref["asset_id"] for clip in self.clip_plan["clips"] for ref in clip.get("references", []) if ref["role"] == "character"}
        location_ids = {ref["asset_id"] for clip in self.clip_plan["clips"] for ref in clip.get("references", []) if ref["role"] == "location"}
        wanted = character_ids | location_ids
        log(f"assets: {len(character_ids)} characters x2 images + {len(location_ids)} locations (only what this episode references)")
        factory = FramedAssetFactory(self.settings, self.provider)
        factory.frame_text = self.frame_spec["text"]
        log(f"profile: style={self.profile['style']} frame={self.profile['frame']} canvas={self.settings.width}x{self.settings.height}")
        # Purge unreadable images BEFORE the factory runs.  A corrupt card is
        # not just a bad output: the factory feeds a character turnaround in as
        # the reference for its expression card, so one truncated download makes
        # every dependent request fail with an unrelated-looking error.
        self.purge_unreadable(self.novel_dir / "series_assets")
        wait_for_inflight_redraws([
            backup.with_name(backup.name.replace(".photoreal-rejected.jpeg", ".jpeg"))
            for backup in sorted((self.novel_dir / "series_assets" / "characters").glob("*/*.photoreal-rejected.jpeg"))
        ])
        manifest = None
        for attempt in range(1, ASSET_BUILD_ROUNDS + 1):
            try:
                manifest = factory.build_selected(self.novel_dir / "series_assets", self.bible, character_ids, location_ids, expressions=not self.fast)
            except ModerationRejected:
                raise
            except (RuntimeError, TimeoutError, OSError) as error:
                # The hosted image service returns "图片生成失败，请稍后重试" during
                # its own incidents.  That is transient, so back off instead of
                # losing the whole chapter.
                if attempt == ASSET_BUILD_ROUNDS:
                    raise
                log(f"assets: round {attempt} failed ({type(error).__name__}: {str(error)[:110]}); retrying in {ASSET_RETRY_SECONDS}s")
                time.sleep(ASSET_RETRY_SECONDS)
                continue
            broken = [path for path in self.broken_assets(manifest) if path.parent.name in wanted]
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
            if path.name.startswith("."):
                continue  # review markers and other dotfiles are not cards
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
    def clip_prompt(self, clip: dict) -> str:
        note = str(self.feedback.get(clip["clip_id"], "")).strip()
        prompt = clip["prompt"] + (f"\n【导演修正】{note}" if note else "")
        return soften_prompt(prompt) if clip.get("_softened") else prompt

    def reference_voices(self, clip: dict) -> tuple[Path, ...]:
        """The clip's reference voices, kept under the service's 30 s total.

        Seedance 2.5 refuses a request whose reference audio adds up to more
        than 30.2 s.  Speakers with more lines in this clip come first, and a
        voice that would push the total over the budget is left out (logged),
        so a two- or three-hander still ships with the voices that matter most.
        """
        spoken: dict[str, int] = {}
        for line in clip.get("lines", []):
            spoken[line.get("speaker_name", "")] = spoken.get(line.get("speaker_name", ""), 0) + len(str(line.get("text", "")))
        candidates = [ref for ref in clip.get("references", []) if ref.get("role") == "voice" and (self.novel_dir / ref["path"]).is_file()]
        candidates.sort(key=lambda ref: -spoken.get(ref.get("name", ""), 0))
        budget = voice_budget_seconds()
        # A tight budget (the 15 s lane) is shared by the two main speakers as
        # trimmed samples rather than spent on one of them.
        share = budget if budget >= VOICE_BUDGET_SECONDS or len(candidates) < 2 else round(budget / 2, 1)
        chosen, total, dropped = [], 0.0, []
        for ref in candidates:
            path = self.novel_dir / ref["path"]
            try:
                with wave.open(str(path), "rb") as handle:
                    seconds = handle.getnframes() / float(handle.getframerate() or 16000)
            except (wave.Error, OSError):
                seconds = budget  # unreadable header: assume it fills the budget
            if seconds > share + 0.05:
                try:
                    path = trimmed_voice(path, share)
                    seconds = share
                except Exception as error:  # noqa: BLE001 - fall back to the budget check on the full sample
                    log(f"{clip['clip_id']}: could not trim {path.name}: {type(error).__name__}")
            if total + seconds > budget:
                dropped.append(f"{ref.get('name')}({seconds:.0f}s)")
                continue
            chosen.append(path)
            total += seconds
        if chosen or dropped:
            log(f"{clip['clip_id']}: reference voices {[p.stem for p in chosen]} ({total:.0f}s)" + (f", over budget: {dropped}" if dropped else ""))
        return tuple(chosen)

    def generate_clip(self, clip: dict, attempt: int) -> Path:
        directory = self.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "clip.mp4"
        prompt = self.clip_prompt(clip) + (RETRY_SUFFIX if attempt > 1 else "") + (COMPLIANCE_SUFFIX if clip.get("_compliance") else "")
        # image references only; the voice references travel separately as reference_audio
        references = tuple(self.novel_dir / ref["path"] for ref in clip.get("references", []) if ref.get("role") != "voice")
        request = {
            "clip_id": clip["clip_id"], "attempt": attempt, "duration": clip["request_seconds"],
            "prompt": prompt, "references": [str(p) for p in references], "workflow": "thin-seedance-native-dialogue-v1",
        }
        if (directory / "request.json").is_file() and output.is_file() and output.stat().st_size > 0:
            # Reuse only a clip generated from this exact prompt and references.
            # Keying on the path alone silently served a stale clip after the
            # chapter was re-planned.
            saved = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            base = clip["prompt"] + (f"\n【导演修正】{self.feedback[clip['clip_id']]}" if str(self.feedback.get(clip["clip_id"], "")).strip() else "")
            acceptable = {prompt, base + (RETRY_SUFFIX if attempt > 1 else ""), soften_prompt(base) + (RETRY_SUFFIX if attempt > 1 else "")}
            # A clip generated from the softened wording (prescreen or moderation
            # retry) is the same clip: do not pay again because a later run made
            # the other choice.
            if saved.get("prompt") in acceptable and saved.get("references") == [str(p) for p in references] and int(saved.get("duration", 0)) == int(clip["request_seconds"]):
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
        if self.prescreen and not clip.get("_softened") and not clip.get("_prescreened"):
            clip["_prescreened"] = True
            risk = prescreen_prompt(prompt)
            if risk >= PRESCREEN_RISK:
                clip["_softened"] = True
                log(f"{clip['clip_id']}: prescreen risk {risk:.2f}; softening the wording before the first submission")
                prompt = self.clip_prompt(clip) + (RETRY_SUFFIX if attempt > 1 else "")
                request["prompt"] = prompt
        # Earlier runs (or the pre-v11 privacy retry) may hold the matching video
        # under another attempt directory; use it rather than paying again.
        for other in sorted((self.work / "clips" / clip["clip_id"]).glob("attempt_*")):
            other_video = other / "clip.mp4"
            if other == directory or not (other / "request.json").is_file() or not other_video.is_file() or other_video.stat().st_size == 0:
                continue
            saved = json.loads((other / "request.json").read_text(encoding="utf-8"))
            if saved.get("prompt", "").removesuffix(RETRY_SUFFIX) == self.clip_prompt(clip) and saved.get("references") == [str(p) for p in references] and int(saved.get("duration", 0)) == int(clip["request_seconds"]):
                # A retry exists to replace a clip that failed the speech gate;
                # reusing that same clip would just fail it again.  Only a video
                # that passed (or was never judged - a resumed run) is reused.
                if attempt > 1 and (other / "asr.json").is_file():
                    try:
                        if not json.loads((other / "asr.json").read_text(encoding="utf-8")).get("passed", True):
                            continue
                    except (OSError, ValueError):
                        pass
                log(f"{clip['clip_id']} attempt {attempt}: reusing the matching video from {other.name}")
                return other_video
        wait_for_inflight_redraws(references)
        atomic_write_json(directory / "request.json", request)
        log(f"{clip['clip_id']} attempt {attempt}: requesting {clip['request_seconds']}s video with {len(references)} references")
        started = time.monotonic()
        slot = acquire_inflight_slot(self.novel_dir, self.inflight)
        try:
          for wait in (*SUBMIT_BACKOFF_SECONDS, None):
            try:
                voices = self.reference_voices(clip)
                self.provider.create_video(prompt, None, output, duration=float(clip["request_seconds"]), additional_images=references, reference_audios=voices)
                break
            except RuntimeError as error:
                # Throttled at submission (higher --parallel): wait and resubmit
                # instead of failing the clip; content errors propagate at once.
                if wait is None or "SensitiveContentDetected" in str(error) or not RATE_LIMIT_RE.search(str(error)):
                    raise
                log(f"{clip['clip_id']} attempt {attempt}: video service throttled ({str(error)[:80]}); retrying in {wait}s")
                time.sleep(wait)
        finally:
            release_inflight_slot(slot)
        log(f"{clip['clip_id']} attempt {attempt}: video ready in {time.monotonic() - started:.0f}s ({media_duration(output):.1f}s long)")
        return output

    def analyse_clip(self, clip: dict, video: Path) -> dict:
        directory = video.parent
        wav = directory / "native.wav"
        asr_path = directory / "asr.json"
        if wav.is_file():
            # A run killed mid-extraction leaves a short wav; trust it only when
            # it is as long as the clip, otherwise redo the extraction and ASR.
            try:
                wav_seconds = media_duration(wav)
            except Exception:  # noqa: BLE001 - unreadable header counts as truncated
                wav_seconds = -1.0
            if abs(wav_seconds - media_duration(video)) > 0.5:
                log(f"{clip['clip_id']}: native.wav is {wav_seconds:.1f}s for a {media_duration(video):.1f}s clip; re-extracting")
                for name in ("native.wav", "asr.json", "asr_raw.json", "chunks.json"):
                    (directory / name).unlink(missing_ok=True)
        if not wav.is_file():
            partial = directory / "native.partial.wav"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(partial)])
            partial.replace(wav)
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
        reference_key = match_key(reference)    # numbers in spoken form: 50万 and 五十万 agree
        hypothesis_key = match_key(hypothesis)
        cer = round(edit_distance(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
        # CER punishes what the model ADDED (an ad-lib, a chuckle, a stage direction
        # it read out) as much as what it dropped, and 12 of 14 gate failures in the
        # first 46 episodes were of that kind - the lines were spoken.  The gate
        # judges the share of the script that was never heard, in order.
        missing = round(1.0 - subsequence_overlap(reference_key, hypothesis_key) / max(1, len(reference_key)), 4) if reference_key else 0.0
        issues = []
        if reference_key:
            if not hypothesis_key or peak_db is None or peak_db < MIN_PEAK_DB:
                issues.append("voice_energy_missing")
            if missing > MAX_MISSING:
                issues.append(f"missing_{missing}_over_{MAX_MISSING}")
        result = {
            "clip_id": clip["clip_id"], "video": str(video), "duration": round(media_duration(video), 3),
            "reference": reference, "hypothesis": hypothesis, "cer": cer, "missing": missing, "mean_volume_db": mean_db, "max_volume_db": peak_db,
            "chunks": rows, "issues": issues, "passed": not issues,
        }
        atomic_write_json(asr_path, result)
        return result

    def repair_rejected_reference(self, clip: dict, index: int) -> list[str]:
        """Seedance named the offending image (content[N]); fix exactly that one.

        A location card that shows people is rebuilt from its own spec prompt
        with the empty-scene line; a character card is stylized, or stylized
        harder if it was stylized once already.  Each step happens once.
        """
        references = clip.get("references", [])
        if not 0 <= index < len(references):
            return []
        ref = references[index]
        if ref.get("role") == "voice":
            return []  # a refused reference voice has no card to repair
        path = self.novel_dir / ref["path"]
        label = f"{ref['asset_id']}/{path.name}"
        with REPAIR_LOCK:
            if ref["role"] != "character":
                marker = path.parent / ".emptied.txt"
                if marker.exists() or not path.is_file():
                    return []
                spec = json.loads((path.parent / "spec.json").read_text(encoding="utf-8")) if (path.parent / "spec.json").is_file() else {}
                prompt = str(spec.get("prompt") or "") + LOCATION_EMPTY_SUFFIX
                for suffix in ("", ".task.json", ".request.json"):
                    source = path.with_suffix(path.suffix + suffix)
                    if source.exists():
                        target = path.with_suffix(".with-people" + path.suffix + suffix)
                        target.unlink(missing_ok=True)
                        source.rename(target)
                log(f"privacy repair: rebuilding {label} as an empty scene (people were read as a real person)")
                self.provider.create_image(prompt, path)
                with Image.open(path) as image:
                    image.load()
                marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
                return [label]
            backup = path.with_suffix(".photoreal-rejected.jpeg")
            second = path.with_suffix(".photoreal-rejected2.jpeg")
            if not path.is_file() or second.exists():
                return []
            if not backup.exists():
                log(f"privacy repair: redrawing {label} as stylized 3D")
                stylize_card(self.provider, path)
                return [label]
            log(f"privacy repair: {label} was stylized once and still read as a real person; stylizing harder")
            for suffix in ("", ".task.json", ".request.json"):
                source = path.with_suffix(path.suffix + suffix)
                if source.exists():
                    target = second.with_suffix(second.suffix + suffix)
                    target.unlink(missing_ok=True)
                    source.rename(target)
            self.provider.create_image(STYLIZE_PROMPT + STYLIZE_STRONGER, path, reference=second)
            with Image.open(path) as image:
                image.load()
            atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {"origin": REDRAW_ORIGIN, "source": second.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT + STYLIZE_STRONGER), "request_sha256": sha256_text(STYLIZE_PROMPT + STYLIZE_STRONGER + second.name), "reason": "second privacy rejection"})
            return [label]

    def repair_privacy_cards(self, clip: dict) -> list[str]:
        """Redraw the character cards unique to a rejected clip as clearly animated.

        Seedance's privacy detector treats a near-photoreal CG face as a real
        person.  Cards (individual views) already used by a clip that generated
        fine are exempt; when every card of the clip is exempt the rejection
        must come from their combination, so all of them are candidates.  A
        card is redrawn at most once: one already stylized (by this run, a
        parallel thread or another process) just earns the clip its retry.
        """
        exempt = set(getattr(self, "_ok_assets", set())) | load_privacy_ok(self.novel_dir)
        cards = [ref for ref in clip.get("references", []) if ref["role"] == "character"]
        candidates = [ref for ref in cards if ref["path"] not in exempt] or cards
        repaired: list[str] = []
        with REPAIR_LOCK:
            for ref in candidates:
                path = self.novel_dir / ref["path"]
                label = f"{ref['asset_id']}/{path.name}"
                backup = path.with_suffix(".photoreal-rejected.jpeg")
                if not path.is_file() and backup.exists():
                    wait_for_inflight_redraws([path])
                    repaired.append(label)
                    continue
                if not path.is_file():
                    continue
                if backup.exists():
                    # Live card next to a parked original = already stylized
                    # once (the factory of a later run may have overwritten the
                    # request.json marker, the backup file it cannot touch).
                    repaired.append(label)
                    continue
                log(f"privacy repair: redrawing {label} as stylized 3D from {backup.name}")
                stylize_card(self.provider, path)
                repaired.append(label)
        return repaired

    def process_clip(self, clip: dict) -> dict:
        """Generate, gate and (once) regenerate one clip.

        A privacy rejection is retried inside the same attempt: the cache key
        stays the one a later run looks for first, and the quality-retry
        suffix (about unclear speech) is not appended to a clip that never
        rendered.  Any other failure is reported per clip instead of taking
        the whole episode down; the clips in flight still finish and cache.
        """
        attempts: list[dict] = []
        privacy_repairs = 0
        attempt = 1
        try:
            while attempt <= self.max_attempts:
                try:
                    video = self.generate_clip(clip, attempt)
                except RuntimeError as error:
                    if PRIVACY_MARKER in str(error) and privacy_repairs < 2:
                        privacy_repairs += 1
                        index = re.search(r"content\[(\d+)\]", str(error))
                        # content[0] is the prompt text; content[N] is the N-th reference image (1-based).
                        repaired = self.repair_rejected_reference(clip, int(index.group(1)) - 1) if index else []
                        if not repaired and privacy_repairs == 1:
                            repaired = self.repair_privacy_cards(clip)
                        log(f"{clip['clip_id']}: reference rejected as a real person (image {index.group(1) if index else '?'}); fixed {repaired or 'nothing'}; retrying")
                        if repaired:
                            continue
                    if INPUT_TEXT_MARKER in str(error) and not clip.get("_softened"):
                        # The prompt text itself was refused: one retry with milder
                        # stage wording (lines untouched) and the compliance line.
                        clip["_softened"] = True
                        log(f"{clip['clip_id']}: prompt text refused by input moderation; retrying once with softened wording")
                        continue
                    if any(marker in str(error) for marker in OUTPUT_MODERATION_MARKERS) and not clip.get("_compliance"):
                        # The generated video tripped the service's output filter;
                        # one retry with an explicit compliance line, same attempt.
                        clip["_compliance"] = True
                        log(f"{clip['clip_id']}: generated video rejected by output moderation; retrying once with a compliance line")
                        continue
                    raise
                analysis = self.analyse_clip(clip, video)
                if not hasattr(self, "_ok_assets"):
                    self._ok_assets = set()
                self._ok_assets.update(ref["path"] for ref in clip.get("references", []))
                record_privacy_ok(self.novel_dir, (ref["path"] for ref in clip.get("references", []) if ref["role"] == "character"))
                attempts.append(analysis)
                log(f"{clip['clip_id']} attempt {attempt}: cer={analysis['cer']} peak={analysis['max_volume_db']} dB issues={analysis['issues']}")
                if analysis["passed"]:
                    break
                attempt += 1
        except Exception as error:  # noqa: BLE001 - one clip must not sink the episode
            message = f"{type(error).__name__}: {str(error)[:600]}"
            log(f"{clip['clip_id']}: FAILED {message[:200]}")
            return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": attempts[-1] if attempts else None, "error": message}
        selected = next((row for row in attempts if row["passed"]), attempts[-1])
        return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": selected}

    # ---- assembly ----
    def script_lines(self, clip_id: str) -> list[str]:
        clip = next((c for c in self.clip_plan["clips"] if c["clip_id"] == clip_id), None)
        return [line["text"] for line in (clip or {}).get("lines", []) if normalize_text(line["text"])]

    @staticmethod
    def align_chunks(lines: list[str], chunks: list[dict], threshold: float = MIN_LINE_SIMILARITY) -> list[tuple[dict, str | None, float, list[tuple[int, str]]]]:
        """Map ASR chunks onto script text, in order, over the whole remaining script.

        The script is flattened to one normalised character sequence (numbers in
        their spoken form) with a pointer back to the original text.  For every
        chunk the best span is searched from the cursor to the end of the script
        (skipping ahead costs a small penalty per skipped line), so one noisy
        chunk cannot derail the lines that follow, and the order constraint lets
        the acceptance threshold sit well below a free-text match.  A span end
        that lands within three characters of a clause boundary snaps to it, so
        captions do not start mid-word.  Returns (chunk, matched original text
        or None, score, pieces) where pieces splits the matched text per script
        line - one line is one speaker's turn, so captions never mix speakers.
        """
        flat: list[tuple[str, int, int, int]] = []  # (key char, line index, first original index, last original index)
        for line_index, line in enumerate(lines):
            for match in re.finditer(r"\d+(?:\.\d+)?|.", line, re.S):
                token = match.group(0)
                if token[0].isdigit():
                    for key_char in match_key(line[match.start():match.end() + 1] if line[match.end():match.end() + 1] == "年" else token).replace("年", ""):
                        flat.append((key_char, line_index, match.start(), match.end() - 1))
                else:
                    key = normalize_text(token).replace("两", "二")
                    if key:
                        flat.append((key, line_index, match.start(), match.start()))
        keys = "".join(item[0] for item in flat)
        boundary_after: list[bool] = []  # True when a clause ends right after this flat position
        for position, (_, line_index, _, last) in enumerate(flat):
            following = flat[position + 1] if position + 1 < len(flat) else None
            if following is None or following[1] != line_index:
                boundary_after.append(True)
                continue
            between = lines[line_index][last + 1:following[2]]
            boundary_after.append(any(ch in CLAUSE_PUNCT for ch in between))
        line_starts: dict[int, int] = {}
        for position, (_, line_index, _, _) in enumerate(flat):
            line_starts.setdefault(line_index, position)
        cursor = 0
        results: list[tuple[dict, str | None, float, list[tuple[int, str]]]] = []

        def original_pieces(start: int, end: int) -> list[tuple[int, str]]:
            pieces: list[list[int]] = []
            for position in range(start, end):
                _, line_index, first, last = flat[position]
                if not pieces or pieces[-1][0] != line_index:
                    pieces.append([line_index, first, last])
                pieces[-1][1] = min(pieces[-1][1], first)
                pieces[-1][2] = max(pieces[-1][2], last)
            out = []
            for line_index, first, last in pieces:
                line = lines[line_index]
                stop = last + 1
                while stop < len(line) and not normalize_text(line[stop]):
                    stop += 1
                out.append((line_index, line[first:stop]))
            return out

        for chunk in chunks:
            hypothesis = match_key(str(chunk.get("hypothesis", "")))
            if not hypothesis or cursor >= len(keys):
                results.append((chunk, None, 0.0, []))
                continue
            current_line = flat[cursor][1]
            candidates = [(cursor, 0)] + [(pos, li - current_line) for li, pos in line_starts.items() if pos > cursor]
            best = (0.0, None, None)
            for start, skipped in candidates:
                low = max(2, int(len(hypothesis) * 0.6))
                high = min(len(keys) - start, int(len(hypothesis) * 1.5) + 2)
                for width in range(low, high + 1):
                    span = keys[start:start + width]
                    score = 1.0 - edit_distance(span, hypothesis) / max(len(span), len(hypothesis)) - 0.04 * skipped
                    if score > best[0]:
                        best = (score, start, width)
            score, start, width = best
            if start is None or score < threshold:
                results.append((chunk, None, round(max(score, 0.0), 3), []))
                continue
            end = start + width
            for delta in (0, -1, 1, -2, 2, -3, 3):
                candidate = end + delta
                if start < candidate <= len(keys) and boundary_after[candidate - 1]:
                    end = candidate
                    break
            pieces = original_pieces(start, end)
            results.append((chunk, "".join(text for _, text in pieces), round(score, 3), pieces))
            cursor = end
        return results

    def subtitle_events(self, clip_id: str, analysis: dict) -> list[dict]:
        """Time subtitles by ASR chunks but print the script wording.

        One caption per script line (so two speakers never share a caption),
        timed by character count inside the chunk, then capped and floored by
        reading speed so a caption neither lingers over the next speaker nor
        flashes by.  Speech that matches no line is shown as heard whenever the
        block is long enough to be a line at all; shorter murmurs stay silent.
        """
        rows: list[list] = []  # [start, end, text, source]
        for row, text, score, pieces in self.align_chunks(self.script_lines(clip_id), analysis["chunks"]):
            row["match_score"] = score
            row["matched_lines"] = text or ""
            start, end = float(row["start"]), float(row["end"])
            if not text:
                heard = str(row.get("hypothesis", "")).strip()
                verdict = classify_unmatched(heard, end - start)
                row["subtitle"] = verdict
                if verdict == "asr_text":
                    rows.append([start, end, heard, "asr_text"])
                continue
            row["subtitle"] = "script_span"
            weights = [max(1, len(normalize_text(piece))) for _, piece in pieces]
            total = sum(weights)
            cursor = start
            for index, ((_, piece), weight) in enumerate(zip(pieces, weights)):
                piece_end = end if index == len(pieces) - 1 else cursor + (end - start) * weight / total
                rows.append([cursor, piece_end, piece, "native_audio_asr"])
                cursor = piece_end
        clip_seconds = float(analysis.get("duration") or 0.0) or (rows[-1][1] if rows else 0.0)
        for index, item in enumerate(rows):
            chars = max(1, len(normalize_text(item[2])))
            next_start = rows[index + 1][0] if index + 1 < len(rows) else clip_seconds
            item[1] = min(item[1], item[0] + 0.8 + SECONDS_PER_CHAR_CAP * chars)
            wanted = item[0] + SECONDS_PER_CHAR_FLOOR * chars
            item[1] = max(item[1], min(wanted, max(item[0] + 0.3, next_start - 0.05)))
        events = []
        for start, end, text, source in rows:
            for page in timed_subtitle_pages(text, start, end):
                events.append({"unit_id": clip_id, "role": "dialogue", "start": float(page["start"]), "end": float(page["end"]), "text": tidy_page(str(page["text"])), "subtitle_source": source})
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

    def chat_history(self, clip_id: str) -> dict[str, list[dict]]:
        """Earlier messages per conversation: this episode's previous clips, then
        the previous episodes, newest last.  Keyed like chat_card.channels()."""
        history: dict[str, list[dict]] = {}

        def absorb(plan: dict, stop_at: str | None) -> None:
            for other in plan.get("clips", []):
                if stop_at and other["clip_id"] == stop_at:
                    break
                for run in chat_card.channels(other.get("chat_lines") or [], str(self.chat_screen.get("self_name", ""))):
                    history.setdefault(run["key"], []).extend(run["messages"])

        try:
            index = int(self.episode_dir.name.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            index = None
        if index is not None:
            for previous in range(max(1, index - CHAT_HISTORY_EPISODES), index):
                plan_path = self.novel_dir / f"{self.novel_dir.name}_{previous}" / "clip_plan.json"
                if plan_path.is_file():
                    try:
                        absorb(json.loads(plan_path.read_text(encoding="utf-8")), None)
                    except (OSError, ValueError):
                        pass
        absorb(self.clip_plan, clip_id)
        return history

    def chat_segments(self, clip_id: str, clip_video: Path) -> list[dict]:
        """Phone-screen cards for this clip, drawn here instead of by the video model.

        The card is cut in just before the clip, so the messages land (one chime
        each) and the film then shows the character reading them.  Chat text is
        on screen, so these segments carry no subtitles.
        """
        if str(self.chat_screen.get("render", "card")) != "card":
            return []
        clip = next((c for c in self.clip_plan["clips"] if c["clip_id"] == clip_id), {})
        runs = chat_card.channels(clip.get("chat_lines") or [], str(self.chat_screen.get("self_name", "")))
        if not runs:
            return []
        history = self.chat_history(clip_id)
        out_dir = self.work / "chat"
        out_dir.mkdir(parents=True, exist_ok=True)
        background = None
        try:
            background = self.frame(clip_video, 0.4, out_dir / f"{clip_id}_plate.jpeg")
        except Exception as error:  # noqa: BLE001 - a missing plate only costs the blurred backdrop
            log(f"{clip_id}: chat card backdrop unavailable ({type(error).__name__})")
        segments = []
        card = 0
        for run in runs:
            names = [str(m.get("speaker_name", "")) for m in run["messages"]] + [str(self.chat_screen.get("self_name", "")), run["target"]]
            avatars = chat_card.load_avatars(self.novel_dir, [name for name in names if name])
            # A card that opens on an empty screen looks wrong; seed it with the
            # last messages of the same conversation so the new ones land below them.
            context = history.get(run["key"], [])[-CHAT_CONTEXT_MESSAGES:]
            messages = context + run["messages"]
            for window in chat_card.windows(len(run["messages"])):
                card += 1
                window = (window[0] + len(context), window[1] + len(context))
                path, seconds = chat_card.build_segment(
                    messages, out_dir / f"{clip_id}_chat_{card:02d}.mp4",
                    title=run["target"] or str(self.chat_screen.get("group_name", "群聊")),
                    self_name=str(self.chat_screen.get("self_name", "")), group=not run["target"], avatars=avatars,
                    width=self.settings.width, height=self.settings.height, fps=self.settings.fps, background=background,
                    window=window,
                )
                log(f"{clip_id}: chat card {card} (messages {window[0] + 1}-{window[1]}, {seconds:.1f}s)")
                segments.append({"unit_id": f"{clip_id}_chat{card}", "role": "chat", "segment": str(path),
                                 "duration": seconds, "audio_source": "chat_card", "subtitle_events": []})
        return segments

    def assemble(self, results: list[dict]) -> dict:
        video_id = self.episode_dir.name
        turn_segments = []
        for record in results:
            selected = record["selected"]
            clip_video = Path(selected["video"])
            wav = clip_video.parent / "native.wav"
            segment, duration = self.renderer.mux_visual_group(clip_video, wav, self.work / "segments" / f"{record['clip_id']}.mp4")
            turn_segments.extend(self.chat_segments(record["clip_id"], clip_video))
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
        # FFmpeg 4.4 on this host freezes inputs inside multi-input filter
        # graphs (xfade chains, and on re-muxed segments the concat filter too:
        # ep10 came back with a 12 s hold after a re-assembly).  Short drama
        # cuts hard anyway: normalise every segment to constant frame rate and
        # uniform audio, then join with the concat demuxer, no filter graph.
        fps, width, height = self.settings.fps, self.settings.width, self.settings.height

        def hard_cut_join(sequence, durations, output, *, crossfade_seconds=0.15):
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

        self.renderer._join_with_crossfade = hard_cut_join
        # The renderer's subtitle style keeps a 310 px bottom margin (tuned for
        # 9:16 so platform UI does not cover the line); on a 1080-high landscape
        # frame that lands the subtitles a third of the way up.  Scale it: ~8%
        # of the frame height, like a normal bottom caption.
        original_write_ass = self.renderer.write_ass_pages
        margin_v = 310 if height > width else max(60, round(height * 0.08))

        def write_ass_pages(path, subtitles):
            result = original_write_ass(path, subtitles)
            text = Path(result).read_text(encoding="utf-8").replace(",2,90,90,310,1", f",2,90,90,{margin_v},1", 1)
            Path(result).write_text(text, encoding="utf-8")
            return result

        self.renderer.write_ass_pages = write_ass_pages
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
        errored = [r["clip_id"] for r in results if r.get("error") or not r.get("selected")]
        failed = [r["clip_id"] for r in results if r.get("selected") and not r["selected"]["passed"]]
        report = {
            "policy": POLICY, "episode": self.episode_dir.name,
            # Batch drivers compare this with the current clip_plan.json to tell
            # a finished episode from one whose plan changed since.
            "clip_plan_fingerprint": plan_fingerprint(self.clip_plan),
            "review_feedback": self.feedback,
            "clips": results, "failed_clips": errored, "gate_failed_clips": failed,
        }
        if errored:
            log(f"{len(errored)} clip(s) have no video ({', '.join(errored)}); episode not assembled, re-run to retry only those")
            report.update({"status": "clips_failed", "assembly": None, "elapsed_seconds": round(time.monotonic() - started, 1)})
            atomic_write_json(self.episode_dir / "thin_media_report.json", report)
            return report
        assembly = self.assemble(results)
        report.update({"status": "assembled", "assembly": assembly, "elapsed_seconds": round(time.monotonic() - started, 1)})
        atomic_write_json(self.episode_dir / "thin_media_report.json", report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode", required=True, help="episode dir name under novel dir, e.g. fentian-thin-v4_1")
    parser.add_argument("--workers", type=int, default=4, help="clips submitted at once for this episode; 0 = one per clip")
    parser.add_argument("--inflight", type=int, default=0, help="global cap on clips in flight across all runners of the novel (lock-file semaphore); 0 = none")
    parser.add_argument("--prescreen", action="store_true", help="ask the local Qwen for content-filter risk and soften risky prompts before the first submission")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets-only", action="store_true", help="build the cards this episode needs, write series_assets/cards_sheet.jpg for review, and stop before any video")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.episode
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    runner = ThinMediaRunner(novel_dir=novel_dir, episode_dir=episode_dir, settings=settings, bible=bible, workers=args.workers, max_attempts=args.max_attempts, profile=profile, inflight=args.inflight, prescreen=args.prescreen)
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
    if args.assets_only:
        runner.build_assets()
        sheet = cards_sheet(novel_dir, novel_dir / "series_assets" / "cards_sheet.jpg")
        print(json.dumps({"assets": "ready", "cards_sheet": str(sheet) if sheet else None}, ensure_ascii=False), flush=True)
        return 0
    report = runner.run()
    clip_rows = [{"clip_id": r["clip_id"], "attempts": len(r["attempts"]), "error": r.get("error"), **({"cer": r["selected"]["cer"], "peak_db": r["selected"]["max_volume_db"], "duration": r["selected"]["duration"]} if r.get("selected") else {})} for r in report["clips"]]
    assembly = {k: v for k, v in (report.get("assembly") or {}).items() if k != "media_qc"} or None
    print(json.dumps({"status": report["status"], "elapsed_seconds": report["elapsed_seconds"], "failed_clips": report["failed_clips"], "gate_failed_clips": report["gate_failed_clips"], "assembly": assembly, "clips": clip_rows}, ensure_ascii=False, indent=2), flush=True)
    if report["failed_clips"]:
        return 3
    return 0 if report["assembly"]["thin_passed"] and not report["gate_failed_clips"] else 2


if __name__ == "__main__":
    sys.exit(main())
