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
import hashlib
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
from novel_manga.providers.local_h3 import LocalH3MediaProvider
from novel_manga.providers.phanrouter import VIDEO_MODEL_LIMITS
from novel_manga.models import StoryBible
from novel_manga.media.asset_factory import SeriesAssetFactory
from novel_manga.production_models import AssetRecord, SeriesAssetManifest
from novel_manga.media.common import audio_levels, cover_title, sha256_text, log, reference_digests
from novel_manga.media.adapters import FramedPhanRouter, FramedLocalH3
from novel_manga.media.assets import FramedAssetFactory, ModerationRejected, asset_index, cards_sheet, load_privacy_ok, moderation_error, record_privacy_ok, stylize_card, wait_for_inflight_redraws
from novel_manga.media import assets as media_assets
from novel_manga.media import generation, cache, postprocess, subtitles
from novel_manga.media import analysis as media_analysis
from novel_manga.media.subtitles import balanced_split, classify_unmatched, match_key, speakable, spoken_integer, subsequence_overlap, tidy_page
from novel_manga.media.analysis import speech_chunks
from novel_manga.media.postprocess import BatchRenderer
from novel_manga.media.cache import CacheMiss
from novel_manga.media.generation import voice_budget_seconds, trimmed_voice, renumber_audio
from novel_manga.media.policy import takes_past_cache, soften_prompt, resubmittable
from novel_manga.providers.phanrouter import PhanRouterMediaProvider, SubmissionUncertain
from novel_manga.providers.h3_pool import PoolUnavailable
from novel_manga.qc import inspect_media
from novel_manga.render import Renderer
from novel_manga.runtime_backends import correct_protected_lexicon, edit_distance, normalize_text
from novel_manga.sd_dialogue import timed_subtitle_pages
from novel_manga.util import atomic_write_json, media_duration, run
import moderation_repair
from novel_manga.render import _fit_cover
from dataclasses import replace as dc_replace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_manga.media import chat_card
from thin_profile import frame_spec, h3_prompt_fingerprint, is_fast, load_genre, load_profile, plan_fingerprint, styled_bible
from thin_profile import MAX_HOLD_SECONDS, media_qc_ignores

POLICY = "thin-media-v22-coverage-gate"
ASSET_BUILD_ROUNDS = 6
ASSET_RETRY_SECONDS = 90
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them
VOICE_BUDGET_SECONDS = 29.0  # Seedance 2.5 caps reference audio at 30.2 s per request
VOICE_BUDGET_SHORT_SECONDS = 15.0  # Seedance 2.0 (the 15 s lane) caps it at 15.2 s






AUDIO_TAG_H3 = re.compile(r"<Audio (\d+)>")




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
















MAX_CER = 0.5          # kept in the report; no longer gates
MAX_MISSING = 0.5      # more than half of the script characters never spoken -> retry once
MIN_PEAK_DB = -35.0
PRIVACY_MARKER = "InputImageSensitiveContentDetected"
REDRAW_ORIGIN = "privacy-stylized-redraw"
MODERATION_MARKERS = ("violate", "usage policy", "content policy", "sensitive", "moderation", "safety", "违规", "敏感", "审核")
SCRUB_WORDS = re.compile(r"妩媚|性感|曼妙|露肩|低胸|大腿|俗气|轻浮|挑逗|妖艳|夸张")
SAFE_SUFFIX = "。整体端庄得体，衣着完整，表情自然温和，普通站姿，无任何性暗示、暴力或血腥"








REDRAW_WAIT_SECONDS = 180  # a stylised redraw normally lands in 60-100 s; past this the photoreal backup is used
REPAIR_LOCK = threading.Lock()  # one card redraw at a time; parallel repairs of the same card raced
PLAN_WRITE_LOCK = threading.Lock()
MANIFEST_LOCK = threading.Lock()  # series_assets/manifest.json is merged under this and a file lock
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
# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.
RETRY_SUFFIX_H3 = ("\n\nretake_note:\nThe previous take dropped or slurred some of the lines. Keep everything "
                   "above unchanged, and have every <d> line spoken clearly and completely.")
RETRY_TAIL = re.compile("(?:" + re.escape(RETRY_SUFFIX) + "|" + re.escape(RETRY_SUFFIX_H3) + r")(?: This is take \d+\.|（第\d+次）)?\Z")
RETRY_TAIL_H3 = re.compile(re.escape(RETRY_SUFFIX_H3) + r"(?: This is take \d+\.)?\Z")  # an English prompt's own retake note
MAX_ATTEMPTS_FREE = 8  # a free lane retakes a failing clip past the usual two attempts, but not without end
OUTPUT_MODERATION_MARKERS = ("OutputVideoSensitiveContentDetected", "OutputAudioSensitiveContentDetected")
RATE_LIMIT_RE = re.compile(r"HTTP (429|502|503|504)\b|Too Many Requests|rate ?limit|concurren|QuotaExceeded|RequestLimit|ServerOverloaded", re.I)
INPUT_TEXT_MARKER = "InputTextSensitiveContentDetected"
SOFTEN = [  # stage descriptions only get milder wording on a text-moderation refusal; spoken lines stay
    (re.compile(r"打死|弄死|杀死|杀了|杀掉|干掉"), "打倒"), (re.compile(r"鲜血|血迹|血液|流血|血"), "伤痕"), (re.compile(r"尸体|死尸"), "倒下的人"),
    (re.compile(r"砍|捅|刺"), "挥"), (re.compile(r"手枪|枪"), "棍棒"), (re.compile(r"毒品|吸毒"), "违禁品"), (re.compile(r"强奸|轮奸|猥亵"), "欺负"),
    (re.compile(r"赌博|赌钱|赌"), "比试"), (re.compile(r"废了你|打断.{0,2}腿|弄残"), "教训你"), (re.compile(r"威胁"), "警告"), (re.compile(r"自杀|上吊|跳楼"), "轻生"),
]


SUBMIT_BACKOFF_SECONDS = (30, 60, 90, 120, 180, 240, 300)  # ~17 min of patience when the video service throttles
COMPLIANCE_SUFFIX = "\n【合规】画面健康、日常、无任何暴力、血腥、色情、赌博或违规内容；人物衣着完整；屏幕上的文字仅为剧情中的普通聊天内容；声音只有普通对白、环境音效和无歌词的哼唱，不含任何已有歌曲、歌词或背景音乐。"
FEEDBACK_FILE = "review_feedback.json"  # {clip_id: 导演修正}, written by the automatic episode review
SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")


















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









PRESCREEN_RISK = 0.6


def apply_genre(genre: dict) -> None:
    """Genre preset → card style cue, location-card policy, extra softening pairs."""
    media_assets.apply_genre(genre)
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
    # NOVEL_INFLIGHT_DIR makes the pool belong to the API key rather than to this novel, so
    # two novels rendering on one key share a single ceiling instead of one each.
    shared = os.environ.get("NOVEL_INFLIGHT_DIR", "").strip()
    directory = Path(shared) if shared else novel_dir / (f".inflight-{pool}" if pool else ".inflight")
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





class ThinMediaRunner:
    def __init__(self, *, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None, inflight: int = 0, prescreen: bool = False, moderation_repair: bool = True, cache_only: bool = False, retake_failed: bool = False):
        self.cache_only = cache_only  # rebuild from clips already rendered; never generate
        self.novel_dir = novel_dir
        self.inflight = inflight  # global cap on clips in flight across every runner of this novel (0 = none)
        self.prescreen = prescreen
        self.moderation_repair = moderation_repair
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
        self.softening_rules = list(SOFTEN)
        self.voice_budget = generation.voice_budget_seconds()
        self._workers_arg = workers  # resolved after clip_plan is loaded (0 = one slot per clip)
        # Fast tier still gets a second attempt, but only when the first one
        # failed the speech gate (the retry loop runs on gate failures alone):
        # a line the model did not speak costs the line and its subtitles.
        self.max_attempts = 2 if self.fast else max_attempts
        # On a free lane (local H3) a clip whose cached takes all failed gets fresh ones on a later run,
        # not the same verdict again (process_clip); a paid lane leaves further takes to a person.
        self.free_retries = takes_past_cache(self.settings, retake_failed, cache_only)
        resolution = "480p" if self.fast else "720p"
        local = self.settings.local_h3_base_url
        self.provider = (FramedLocalH3(self.settings, self.frame_spec, local, resolution=resolution)
                         if local else FramedPhanRouter(self.settings, self.frame_spec, resolution=resolution))
        self.provider.prompt_aliases = {str(k): str(v) for k, v in (self.profile.get("prompt_aliases") or {}).items()}
        self.renderer = BatchRenderer(self.settings)
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
        from repair_history import load as repair_history_load
        from managed_repair_thin import generated_counts, generation_limit
        history_record=repair_history_load(episode_dir)
        counts=generated_counts(history_record)
        managed={cid for trial in history_record.get('trials',[]) if trial.get('managed')
                 and trial['expected_plan']==plan_fingerprint(self.clip_plan)
                 and trial['expected_notes']==self.feedback for cid in trial['clips']}
        self._managed_remaining={cid:max(0,generation_limit(episode_dir,cid)-counts.get(cid,0)) for cid in managed}
        technical_path = episode_dir / "technical_repair.json"
        self.black_checks = set(json.loads(technical_path.read_text()).get("black_clips", [])) if technical_path.is_file() else set()
        self.speech_checks = set(json.loads(technical_path.read_text()).get("speech_clips", [])) if technical_path.is_file() else set()
        asr_command = os.environ.get("NOVEL_ASR_COMMAND", "")
        self.asr_python = shlex.split(asr_command)[0] if asr_command else sys.executable
        self.asr_helper = Path(__file__).resolve().parent / "thin_asr_segments.py"
        self.protected_terms = list(dict.fromkeys([*(c.name for c in bible.characters), *(l.split("：", 1)[0] for l in bible.locations)]))
        self.aliases = lexicon_aliases()
        alias_path = novel_dir / 'asr_aliases.json'
        if alias_path.is_file():
            self.aliases.update(json.loads(alias_path.read_text()))

    # ---- assets ----
    def build_assets(self, clips=None):
        clips = self.clip_plan["clips"] if clips is None else clips
        character_ids = {ref["asset_id"] for clip in clips for ref in clip.get("references", []) if ref["role"] == "character"}
        location_ids = {ref["asset_id"] for clip in clips for ref in clip.get("references", []) if ref["role"] == "location"}
        required_images = {self.novel_dir / ref["path"] for clip in clips for ref in clip.get("references", [])
                           if ref.get("role") in {"character", "location"}}
        # Quality-mode construction may use/build the second view even if this
        # particular clip references only the primary. Fast production never does.
        if not self.fast:
            built_characters = {f"character_{i:03d}" for i, _ in enumerate(self.bible.characters, 1)}
            required_images.update(self.novel_dir / "series_assets" / "characters" / asset / name
                                   for asset in character_ids & built_characters for name in ("turnaround.jpeg", "expressions.jpeg"))
        log(f"assets: {len(character_ids)} character cards + {len(location_ids)} locations")
        factory = FramedAssetFactory(self.settings, self.provider)
        factory.frame_text = self.frame_spec["text"]
        log(f"profile: style={self.profile['style']} frame={self.profile['frame']} canvas={self.settings.width}x{self.settings.height}")
        # Purge unreadable images BEFORE the factory runs.  A corrupt card is
        # not just a bad output: the factory feeds a character turnaround in as
        # the reference for its expression card, so one truncated download makes
        # every dependent request fail with an unrelated-looking error.
        self.purge_unreadable(self.novel_dir / "series_assets", paths=required_images)
        wait_for_inflight_redraws(sorted(required_images))
        manifest = None
        for attempt in range(1, ASSET_BUILD_ROUNDS + 1):
            try:
                manifest = factory.build_selected(self.novel_dir / "series_assets", self.bible, character_ids, location_ids, expressions=not self.fast)
            except ModerationRejected:
                raise
            except (RuntimeError, TimeoutError, OSError) as error:
                if isinstance(error, SubmissionUncertain):
                    raise  # held until someone checks the bill: waiting out more rounds cannot release it
                # The hosted image service returns "图片生成失败，请稍后重试" during
                # its own incidents.  That is transient, so back off instead of
                # losing the whole chapter.
                if attempt == ASSET_BUILD_ROUNDS:
                    raise
                log(f"assets: round {attempt} failed ({type(error).__name__}: {str(error)[:110]}); retrying in {ASSET_RETRY_SECONDS}s")
                time.sleep(ASSET_RETRY_SECONDS)
                continue
            broken = self.broken_assets(manifest, paths=required_images)
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
        for clip in clips:
            for ref in clip.get("references", []):
                path = self.novel_dir / ref["path"]
                if not path.is_file():
                    raise RuntimeError(f"reference image missing after asset build: {path}")
        log("assets ready")
        return manifest

    @staticmethod
    def purge_unreadable(root: Path, *, paths=None) -> list[Path]:
        """Delete asset images that are missing bytes or are not images at all."""
        removed: list[Path] = []
        for path in sorted(set(root.rglob("*.jpeg") if paths is None else paths)):
            if not path.is_file():
                continue  # a missing selected image is built or awaited below
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

    def broken_assets(self, manifest, *, paths=None) -> list[Path]:
        """Return asset images that are missing or that PIL cannot fully decode."""
        if paths is None:
            paths = [self.novel_dir / value for record in [*manifest.characters, *manifest.locations]
                     for value in (record.primary_image, getattr(record, "secondary_image", None)) if value]
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

    def save_clip_plan(self) -> None:
        """Write the plan back without the runner's own bookkeeping keys."""
        with PLAN_WRITE_LOCK:
            plan = {**self.clip_plan, "clips": [{k: v for k, v in clip.items() if not k.startswith("_")} for clip in self.clip_plan["clips"]]}
            atomic_write_json(self.episode_dir / "clip_plan.json", plan)

    def repair_refused_prompt(self, clip: dict, attempt: int) -> bool:
        """Rewrite a prompt the text filter refuses, verified before it is paid for."""
        def assemble(base: str) -> str:
            saved = clip["prompt"]
            clip["prompt"] = base
            try:
                return self.clip_prompt(clip) + self.retry_suffix(clip, attempt) + (COMPLIANCE_SUFFIX if clip.get("_compliance") else "")
            finally:
                clip["prompt"] = saved

        # A guest character may be renamed to get past the filter (the card binding
        # is by image index, so the picture does not change), but the series leads
        # may not: their names run through every episode's plan and review.
        leads = tuple(c.name for c in self.bible.characters if str(getattr(c, "role", "")) in {"主角", "女主角", "男主角"})
        repaired = moderation_repair.repair(
            clip["prompt"], self.settings, assemble=assemble, protect=leads,
            log=lambda message: log(f"{clip['clip_id']}: {message}"))
        if not repaired:
            return False
        text, line_edits = repaired
        clip.setdefault("prompt_before_repair", clip["prompt"])
        clip["prompt"] = text
        # Subtitles and the speech gate read clip["lines"], so a line the rewrite
        # had to change is changed there too - otherwise the episode would caption
        # words nobody says.
        for edit in line_edits:
            for line in clip.get("lines", []):
                if edit["old"] in str(line.get("text", "")):
                    line["text"] = str(line["text"]).replace(edit["old"], edit["new"])
        if line_edits:
            log(f"{clip['clip_id']}: repaired lines {[e['new'] for e in line_edits]}")
        # The plan is the cache key of a clip: keeping the accepted wording there
        # means a later run reuses this video instead of paying for it again.
        self.save_clip_plan()
        return True

    # ---- one clip ----
    def uses_h3_prompt(self, clip: dict) -> bool:
        return generation.uses_h3_prompt(self, clip)

    def clip_base(self, clip: dict) -> str:
        return generation.clip_base(self, clip)

    def retry_suffix(self, clip: dict, attempt: int) -> str:
        return generation.retry_suffix(self, clip, attempt)

    def english_correction_for_new_take(self, clip: dict) -> bool:
        """Keep old passed caches; a new corrected H3 take must use its English version."""
        note = str(self.feedback.get(clip['clip_id']) or '').strip()
        if not self.settings.local_h3_base_url or not note:
            return False
        from thin_profile import h3_prompt_outdated
        if h3_prompt_outdated(clip, note):
            raise RuntimeError('H3 correction needs a current English translation before generating a new take')
        if self.uses_h3_prompt(clip):
            return False
        clip.pop('prompt_h3_skip', None)
        self.save_clip_plan()
        return True

    @staticmethod
    def without_retry(prompt: str) -> str:
        return cache.without_retry(prompt)

    @staticmethod
    def references_match(saved: dict, references, digests: list[str]) -> bool:
        return cache.references_match(saved, references, digests)

    def request_matches(self, clip: dict, saved: dict, references, digests: list[str]) -> bool:
        return cache.request_matches(self, clip, saved, references, digests)

    def cached_take(self, clip: dict, attempt: int) -> bool:
        return cache.cached_take(self, clip, attempt)

    def approved_cached_take(self, clip: dict) -> dict | None:
        """Reuse proof from an existing take, never issue a new over-budget request."""
        from thin_profile import h3_prompt_outdated
        if self.settings.local_h3_base_url and h3_prompt_outdated(clip, str(self.feedback.get(clip["clip_id"], ""))):
            return None
        for path in sorted((self.work / "clips" / clip["clip_id"]).glob("attempt_*/asr.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                attempt = int(path.parent.name.split("_")[-1])
                if self.cached_take(clip, attempt):
                    record = self.recheck_speech(clip, record, path.with_name('clip.mp4'))
                    record = self.check_clip_black(clip, record, path.with_name("clip.mp4"))
                    if record["passed"]:
                        return {**record, "clip_id": clip["clip_id"], "video": str(path.with_name("clip.mp4"))}
            except (OSError, ValueError):
                continue
        return None

    def prescreens(self, clip: dict) -> bool:
        """Whether the clip's wording is scored for content-filter risk, and softened, before its first submission.
        Not an English (H3) prompt: the local model has no filter to get past, and the softening - Chinese
        substitutions and a Chinese compliance paragraph - rewrote its <d> lines and was read out as dialogue (雾月,
        2026-09-11/12: 329 clips in finals carried it, 30 of them audibly)."""
        return (self.prescreen and not self.cache_only and not self.uses_h3_prompt(clip)
                and not clip.get("_softened") and not clip.get("_prescreened"))

    def clip_prompt(self, clip: dict) -> str:
        return generation.clip_prompt(self, clip)

    def chosen_voices(self, clip: dict) -> tuple[list[tuple[int, Path]], list[str], float]:
        return generation.chosen_voices(self, clip)

    def reference_voices(self, clip: dict) -> tuple[Path, ...]:
        return generation.reference_voices(self, clip)

    def generate_clip(self, clip: dict, attempt: int) -> Path:
        if not self.cache_only:
            from clip_readiness import reference_issues
            reasons = getattr(self, "_blocked_clips", {}).get(clip["clip_id"], []) or reference_issues(clip, self.novel_dir)
            if reasons:
                if not hasattr(self, "_blocked_clips"):
                    self._blocked_clips = {}
                self._blocked_clips[clip["clip_id"]] = reasons
                raise RuntimeError("request blocked before generation: " + "; ".join(reasons))
        clip["_generated"] = False  # set once a new video is actually made: process_clip counts only those
        directory = self.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "clip.mp4"
        request, references, digests, retry = generation.build_request(self, clip, attempt)
        prompt = request['prompt']
        import repair_history
        cached = cache.current_video(self, clip, attempt, directory, references, digests, repair_history)
        if cached is not None:
            return cached
        if self.prescreens(clip):
            clip["_prescreened"] = True
            risk = prescreen_prompt(prompt)
            if risk >= PRESCREEN_RISK:
                clip["_softened"] = True
                log(f"{clip['clip_id']}: prescreen risk {risk:.2f}; softening the wording before the first submission")
                prompt = self.clip_prompt(clip) + retry
                request["prompt"] = prompt
        # Earlier runs (or the pre-v11 privacy retry) may hold the matching video
        # under another attempt directory; use it rather than paying again.
        cached = cache.other_video(self, clip, attempt, directory, references, digests)
        if cached is not None:
            return cached
        if self.cache_only:
            raise CacheMiss(f"{clip['clip_id']} attempt {attempt}: not in the cache")
        if self.english_correction_for_new_take(clip):
            return self.generate_clip(clip, attempt)
        if self.uses_h3_prompt(clip):
            from h3_request_checks import request_issues
            contradictions = request_issues(clip)
            if contradictions:
                if not hasattr(self,'_blocked_clips'):
                    self._blocked_clips={}
                self._blocked_clips[clip['clip_id']]=['request: '+issue for issue in contradictions]
                raise ValueError('H3 request needs identity repair: '+'; '.join(contradictions))
        remaining=getattr(self,'_managed_remaining',{}).get(clip['clip_id'])
        if remaining is not None and remaining<=0:
            raise ValueError('effective clip retry budget used; current cached take retained')
        wait_for_inflight_redraws(references)
        atomic_write_json(directory / "request.json", request)
        log(f"{clip['clip_id']} attempt {attempt}: requesting {clip['request_seconds']}s video with {len(references)} references")
        started = time.monotonic()
        slot = acquire_inflight_slot(self.novel_dir, self.inflight)
        # Everything before this point was waiting for a free slot of the key's
        # concurrency, not the video service working: timing them together hid
        # how long a generation really takes and how long the lane was queueing.
        submitted = time.monotonic()
        try:
          for wait in (*SUBMIT_BACKOFF_SECONDS, None):
            try:
                generation.submit(self, clip, request, output, references)
                break
            except RuntimeError as error:
                # Throttled at submission (higher --parallel): wait and resubmit
                # instead of failing the clip; content errors propagate at once.
                # An unconfirmed submission may already be a paid task: never resent from here.
                if wait is None or not resubmittable(error):
                    raise
                log(f"{clip['clip_id']} attempt {attempt}: video service throttled or H3 pool full ({str(error)[:80]}); retrying in {wait}s")
                time.sleep(wait)
        finally:
            release_inflight_slot(slot)
        clip["_generated"] = True
        if remaining is not None:
            self._managed_remaining[clip['clip_id']]-=1
        finished = time.monotonic()
        log(f"{clip['clip_id']} attempt {attempt}: video ready in {finished - submitted:.0f}s "
            f"(queued {submitted - started:.0f}s, {media_duration(output):.1f}s long)")
        return output

    def analyse_clip(self, clip: dict, video: Path) -> dict:
        from thin_profile import speech_gate_result
        raw = media_analysis.analyse_clip(self, clip, video)
        return speech_gate_result(self.novel_dir, self.check_clip_black(clip, self.recheck_speech(clip, raw, video), video), self.episode_dir)

    def recheck_speech(self, clip: dict, analysis: dict, video: Path) -> dict:
        return media_analysis.recheck_speech(self, clip, analysis, video)

    def check_clip_black(self, clip: dict, analysis: dict, video: Path) -> dict:
        """Targeted black-frame recovery must reject a black take before assembly."""
        if clip['clip_id'] not in getattr(self, 'black_checks', set()) or analysis.get('black_check_policy') == 3:
            return analysis
        from prepare_recovery_thin import black_ranges, brighten_dark_scene
        ranges = black_ranges(video, 0.2)
        # Use the same one-second threshold as final-media QC.
        issues = [issue for issue in analysis.get('issues') or [] if issue != 'black_frames']
        exposure = False
        if ranges and any(end - start >= 1.0 for start, end in ranges):
            exposure = brighten_dark_scene(video, [(a,b) for a,b in ranges if b-a >= 1.0], self.episode_dir, clip['clip_id'])
            if exposure:
                ranges = []
                log(f"{clip['clip_id']}: restored detail in an underexposed scene; original archived, audio unchanged")
        if any(end - start >= 1.0 for start, end in ranges):
            issues.append('black_frames')
        result = {**analysis, 'black_checked': True, 'black_check_policy': 3, 'black_ranges': ranges, 'issues': issues, 'passed': not issues,
                  **({'exposure_gamma': 1.6} if exposure else {})}
        atomic_write_json(video.parent / 'asr.json', result)
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
        if ref["path"] in (set(getattr(self, "_ok_assets", set())) | load_privacy_ok(self.novel_dir)):
            log(f"privacy repair: {label} has rendered fine before; not redrawn, the rejection stands")
            return []
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
        fine are exempt, and when every card of the clip is exempt nothing is
        redrawn: the rejection stands and the clip fails.  The old fallback
        ("then it must be their combination, so all of them are candidates")
        restyled 雾月's protagonist on 2026-09-13 after 1,700 episodes had used
        his card - a changed face is worse than a failed clip.  A card is
        redrawn at most once: one already stylized (by this run, a parallel
        thread or another process) just earns the clip its retry.
        """
        exempt = set(getattr(self, "_ok_assets", set())) | load_privacy_ok(self.novel_dir)
        cards = [ref for ref in clip.get("references", []) if ref["role"] == "character"]
        candidates = [ref for ref in cards if ref["path"] not in exempt]
        if cards and not candidates:
            log(f"privacy repair: every card of {clip.get('clip_id')} has rendered fine before; none is redrawn, the rejection stands")
            return []
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
        """Generate, gate and regenerate one clip: once - or, on a free lane, twice per run past its cached takes.

        A privacy rejection is retried inside the same attempt: the cache key
        stays the one a later run looks for first, and the quality-retry
        suffix (about unclear speech) is not appended to a clip that never
        rendered.  Any other failure is reported per clip instead of taking
        the whole episode down; the clips in flight still finish and cache.
        """
        cached = getattr(self, "_approved_cached", {}).get(clip["clip_id"])
        if not self.cache_only and clip["clip_id"] in getattr(self, "_blocked_clips", {}):
            reasons = self._blocked_clips[clip["clip_id"]]
            log(f"{clip['clip_id']}: waiting for plan/assets: {'; '.join(reasons)}")
            return {"clip_id": clip["clip_id"], "attempts": [], "selected": None,
                    "error": "request blocked before generation", "blocked": reasons}
        attempts: list[dict] = []
        privacy_repairs = 0
        attempt = 1
        limit = self.max_attempts
        try:
            from repair_history import source_accepted_take
            accepted = source_accepted_take(self.work.parent, clip, str(self.feedback.get(clip['clip_id']) or ''))
            if accepted is not None:
                analysis = self.analyse_clip(clip, accepted)
                if analysis['passed']:
                    analysis = {**analysis, 'generated_this_run': False}
                    log(f"{clip['clip_id']}: existing video passed review against corrected source intent; reusing")
                    return {'clip_id':clip['clip_id'],'attempts':[analysis],'selected':analysis}
            if cached:
                # The previous prune may have removed native.wav. The normal
                # analysis path restores it from the video and reuses ASR, so
                # assembly has its audio without another generation or ASR call.
                cached = self.analyse_clip(clip, Path(cached["video"]))
                cached = {**cached, "generated_this_run": False}
                log(f"{clip['clip_id']}: reusing a matching passed take; no new request despite the old duration estimate")
                return {"clip_id": clip["clip_id"], "attempts": [cached], "selected": cached}
            while attempt <= limit:
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
                    if INPUT_TEXT_MARKER in str(error) and self.moderation_repair and not clip.get("_repaired"):
                        # Softening did not help.  Ask the filter itself where the
                        # refusal lives, rewrite that part and verify the rewrite
                        # before another video is paid for.
                        clip["_repaired"] = True
                        log(f"{clip['clip_id']}: still refused after softening; repairing the wording against the filter")
                        if self.repair_refused_prompt(clip, attempt):
                            continue
                    if any(marker in str(error) for marker in OUTPUT_MODERATION_MARKERS) and not clip.get("_compliance"):
                        # The generated video tripped the service's output filter;
                        # one retry with an explicit compliance line, same attempt.
                        clip["_compliance"] = True
                        log(f"{clip['clip_id']}: generated video rejected by output moderation; retrying once with a compliance line")
                        continue
                    raise
                analysis = self.analyse_clip(clip, video)
                analysis = {**analysis, "generated_this_run": bool(clip.get("_generated", False))}
                if not hasattr(self, "_ok_assets"):
                    self._ok_assets = set()
                self._ok_assets.update(ref["path"] for ref in clip.get("references", []))
                record_privacy_ok(self.novel_dir, (ref["path"] for ref in clip.get("references", []) if ref.get("role") != "voice"))
                attempts.append(analysis)
                log(f"{clip['clip_id']} attempt {attempt}: cer={analysis['cer']} peak={analysis['max_volume_db']} dB issues={analysis['issues']}")
                if analysis["passed"]:
                    break
                if not clip.get("_generated", True) and limit < MAX_ATTEMPTS_FREE and (self.free_retries or self.cached_take(clip, attempt + 1)):
                    # A failure served from the cache does not use up this run's takes on a free lane: an episode
                    # taken back for its failed clips gets new takes of them, not the old verdicts over again.  And a
                    # next take already in the cache is looked at on any lane, for free: a rebuild (--cache-only) or
                    # a paid re-render stopped at take 2 and put the failed take in the final over a passing take 3.
                    limit += 1
                attempt += 1
        except Exception as error:  # noqa: BLE001 - one clip must not sink the episode
            message = f"{type(error).__name__}: {str(error)[:600]}"
            if attempts and not isinstance(error, CacheMiss):
                # A further take failed (no instance free, a refusal, a held submission): the clip keeps the takes it
                # has.  Marking the whole clip failed threw an assembled episode back to clips_failed.
                log(f"{clip['clip_id']}: a further take failed ({message[:160]}); keeping the {len(attempts)} it has")
                return {"clip_id": clip["clip_id"], "attempts": attempts, "retake_error": message,
                        "selected": next((row for row in attempts if row["passed"]), attempts[-1])}
            log(f"{clip['clip_id']}: FAILED {message[:200]}")
            return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": attempts[-1] if attempts else None, "error": message}
        selected = next((row for row in attempts if row["passed"]), attempts[-1])
        return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": selected}

    # ---- assembly ----
    def script_lines(self, clip_id: str) -> list[str]:
        return subtitles.script_lines(self, clip_id)

    @staticmethod
    def align_chunks(lines: list[str], chunks: list[dict], threshold: float = MIN_LINE_SIMILARITY) -> list[tuple[dict, str | None, float, list[tuple[int, str]]]]:
        return subtitles.align_chunks(lines, chunks, threshold)

    def subtitle_events(self, clip_id: str, analysis: dict) -> list[dict]:
        return subtitles.subtitle_events(self, clip_id, analysis)

    def _font(self, size: int):
        return postprocess._font(self, size)

    def landscape_cover(self, background: Path, output: Path, novel_title: str, art_title: str, label: str) -> Path:
        return postprocess.landscape_cover(self, background, output, novel_title, art_title, label)

    def landscape_card(self, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
        return postprocess.landscape_card(self, background, output, novel_title, label, subtitle)

    def frame(self, video: Path, second: float, output: Path) -> Path:
        return postprocess.frame(self, video, second, output)

    def chat_history(self, clip_id: str) -> dict[str, list[dict]]:
        return postprocess.chat_history(self, clip_id)

    def chat_segments(self, clip_id: str, clip_video: Path) -> list[dict]:
        return postprocess.chat_segments(self, clip_id, clip_video)

    def title_card_image(self, text: str, background: Path | None, output: Path) -> Path:
        return postprocess.title_card_image(self, text, background, output)

    def title_card_segment(self, clip: dict, background: Path | None) -> dict:
        return postprocess.title_card_segment(self, clip, background)

    def story_segments(self, results: list[dict]) -> list[dict]:
        return postprocess.story_segments(self, results)

    def assemble(self, results: list[dict]) -> dict:
        from repair_history import assembly_directory
        output_dir = assembly_directory(self.episode_dir)
        assembly = postprocess.assemble(self, results, output_dir)
        needs_subtitles = any(c.get('spoken_text') or c.get('lines') for c in self.clip_plan.get('clips', []))
        needs_subtitles |= any(t.get('text') and t.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue', 'singing'}
                               for s in self.script.get('shots', []) for t in s.get('turns', []))
        qc = inspect_media(Path(assembly['final_video']), Path(assembly['cover']), Path(assembly['ending']),
                           Path(assembly['ass']), self.settings, output_dir / 'media_qc_report.json',
                           silent_outro_seconds=assembly['silent_outro_seconds'],
                           ignore_checks=tuple(media_qc_ignores(self.novel_dir,self.episode_dir)),
                           subtitles_required=needs_subtitles or bool(assembly['subtitle_events']))
        freeze = float(qc.get('checks', {}).get('long_freeze', {}).get('detail', {}).get('max_freeze_seconds', 0.0))
        other_checks = [v.get('passed') for k, v in qc.get('checks', {}).items() if k != 'long_freeze']
        return {**assembly, 'media_qc_passed': bool(qc.get('passed')), 'max_hold_seconds': freeze,
                'thin_passed': all(other_checks) and freeze <= MAX_HOLD_SECONDS, 'media_qc': qc}

    def run(self) -> dict:
        started = time.monotonic()
        clips = [clip for clip in self.clip_plan["clips"] if clip["kind"] == "video"]
        self._blocked_clips = {}
        self._approved_cached = {}
        if not self.cache_only:  # cached clips need no cards built (and none redrawn)
            from clip_readiness import plan_issues, reference_issues
            self._blocked_clips = plan_issues(self.clip_plan, self.script)
            for clip in clips:
                reasons = self._blocked_clips.get(clip["clip_id"])
                if reasons and all(r.startswith("duration:") for r in reasons):
                    cached = self.approved_cached_take(clip)
                    if cached:
                        self._approved_cached[clip["clip_id"]] = cached
                        self._blocked_clips.pop(clip["clip_id"])
            eligible = [clip for clip in clips if clip["clip_id"] not in self._blocked_clips and clip["clip_id"] not in self._approved_cached]
            if eligible:
                try:
                    self.build_assets(clips=eligible)
                except (RuntimeError, TimeoutError, OSError) as error:
                    # The card builder already made bounded attempts. One missing
                    # card must not prevent unrelated, fully prepared clips running.
                    log(f"assets: preparation incomplete ({type(error).__name__}); checking each clip's required images")
            for clip in eligible:
                missing = reference_issues(clip, self.novel_dir)
                if missing:
                    self._blocked_clips[clip["clip_id"]] = missing
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(self.process_clip, clips))
        errored = [r["clip_id"] for r in results if r.get("error") or not r.get("selected")]
        failed = [r["clip_id"] for r in results if r.get("selected") and not r["selected"]["passed"]]
        if self.cache_only and errored:
            # Nothing is written: the episode keeps the report and final it had.
            log(f"cache-only: {len(errored)} clip(s) not in the cache ({', '.join(errored)}); episode left as it was")
            return {"status": "cache_miss", "elapsed_seconds": round(time.monotonic() - started, 1), "failed_clips": errored,
                    "gate_failed_clips": failed, "assembly": None, "clips": results}
        if not self.cache_only:
            from clip_readiness import save_check
            save_check(self.episode_dir, self.clip_plan, self._blocked_clips)
        report = {
            "policy": POLICY, "episode": self.episode_dir.name,
            # Batch drivers compare this with the current clip_plan.json to tell
            # a finished episode from one whose plan changed since.
            "clip_plan_fingerprint": plan_fingerprint(self.clip_plan),
            # ...and, on a local-H3 lane, of the English prompts it rendered from (thin_batch.render_status)
            "prompt_h3_fingerprint": h3_prompt_fingerprint(self.clip_plan) if self.settings.local_h3_base_url else None,
            "review_feedback": self.feedback,
            "clips": results, "failed_clips": errored, "gate_failed_clips": failed,
            "blocked_clips": self._blocked_clips,
        }
        if errored:
            log(f"{len(errored)} clip(s) have no video ({', '.join(errored)}); episode not assembled, re-run to retry only those")
            report.update({"status": "clips_failed", "assembly": None, "elapsed_seconds": round(time.monotonic() - started, 1)})
            atomic_write_json(self.episode_dir / "thin_media_report.json", report)
            from repair_history import record_render
            record_render(self.episode_dir, report)
            return report
        assembly = self.assemble(results)
        report.update({"status": "assembled", "assembly": assembly, "elapsed_seconds": round(time.monotonic() - started, 1)})
        atomic_write_json(self.episode_dir / "thin_media_report.json", report)
        from repair_history import record_render
        record_render(self.episode_dir, report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode", required=True, help="episode dir name under novel dir, e.g. fentian-thin-v4_1")
    parser.add_argument("--workers", type=int, default=4, help="clips submitted at once for this episode; 0 = one per clip")
    parser.add_argument("--inflight", type=int, default=0, help="global cap on clips in flight across all runners of the novel (lock-file semaphore); 0 = none")
    parser.add_argument("--prescreen", action="store_true", help="ask the local Qwen for content-filter risk and soften risky prompts before the first submission")
    parser.add_argument("--no-moderation-repair", dest="moderation_repair", action="store_false", default=True, help="do not bisect and rewrite a prompt the text filter keeps refusing")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--cache-only", action="store_true", help="rebuild the episode from clips already rendered; never generate, and write nothing when a clip is missing")
    parser.add_argument("--retake-failed", action="store_true", help="give clips whose cached takes all failed the speech gate fresh takes this run (always so on a local-H3 lane; on a paid one every take is paid for)")
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
    runner = ThinMediaRunner(novel_dir=novel_dir, episode_dir=episode_dir, settings=settings, bible=bible, workers=args.workers, max_attempts=args.max_attempts, profile=profile, inflight=args.inflight, prescreen=args.prescreen, moderation_repair=args.moderation_repair, cache_only=args.cache_only, retake_failed=args.retake_failed)
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
        asset_ids = {ref["asset_id"] for clip in runner.clip_plan["clips"] for ref in clip.get("references", [])
                     if ref.get("role") in {"character", "location"}}
        sheet = cards_sheet(novel_dir, novel_dir / "series_assets" / "cards_sheet.jpg", asset_ids=asset_ids)
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
