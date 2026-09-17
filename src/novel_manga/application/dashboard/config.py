"""dashboard_config_thin responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
from pathlib import Path
import json
import re
from novel_manga.application.configuration import RuntimePaths, dashboard_novels


ROOT = project_root()


TMP = RuntimePaths(ROOT).temporary


CACHE_SECONDS = 20


PORT = 18900


UI_VERSION = "batch-control-20260916"


NOVELS = dashboard_novels(ROOT)


MODEL_NAMES = {
    "Qwen3.8-Flash-Next": "Flash-Next", "DeepSeek-V4-Flash-Vision-Exp": "DeepSeek-V4",
    "Qwen3.8-27B-Project": "本地 Qwen3.8",
}


HOST_NAMES = {"local": "gpu16"}  # the night shift's id for this box; everyone calls it gpu16


TITLES = {novel['id']: novel['title'] for novel in NOVELS}


TICK = re.compile(r"tick:\s*(.+)")


RANGE = re.compile(r"--chapters\s+(\S+)")


NOVEL_ARG = re.compile(r"--novel-dir\s+(\S+)")


STAGE = re.compile(r"--stage\s+(\w+)")


EPISODE_ARG = re.compile(r"--episode\s+(\S+)")


INDEX_ARG = re.compile(r"--episode-index\s+(\d+)")


ASSETS_ARG = re.compile(r"--assets\s+(\S+)")


NOVEL_ID_ARG = re.compile(r"--novel-id\s+(\S+)")


LOG_TS = re.compile(r"^(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})")


WARNING_KINDS = [
    (re.compile(r"(?<!\d)429(?!\d)"), "限流", "warn"),
    (re.compile(r"Traceback"), "异常", "bad"),
    (re.compile(r"auth", re.I), "鉴权", "bad"),
    (re.compile(r"FAILED"), "失败", "bad"),
    (re.compile(r"stopped"), "停止", "warn"),
    (re.compile(r"park"), "暂停", "warn"),
]


WARNING_WINDOW_SECONDS = 6 * 3600


REVIEW_CATS = [
    ("identity_ok", False, "身份"), ("location_ok", False, "场景"),
    ("time_of_day_ok", False, "时段"), ("text_or_watermark", True, "水印/文字"),
    ("chat_text_ok", False, "聊天文字"), ("visual_defects", True, "画面缺陷"),
]


VIDEO_MODEL = re.compile(r'"video_model":\s*"([^"]+)"')


BOARD_SECONDS = 300


def _lane_keys() -> dict:
    """Which video keys each novel renders with: from the shared pipeline file if it is there,
    otherwise from the per-novel conductor configs."""
    out: dict[str, list] = {}
    pipeline = ROOT / "configs" / "pipeline.json"
    if pipeline.is_file():
        try:
            cfg = json.loads(pipeline.read_text(encoding="utf-8"))
            video = cfg.get("resources", {}).get("video_keys", {})
            for novel in cfg.get("novels", []):
                names = [n for n in novel.get("render_keys", []) if n in video]
                if names:
                    out[novel["id"]] = [{"name": n, **video[n]} for n in names]
            return out
        except (OSError, ValueError):
            pass
    for config in sorted((ROOT / "configs").glob("conductor.*.json")):
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        novel_id = Path(str(cfg.get("novel_dir", ""))).name
        if cfg.get("keys"):
            out[novel_id] = cfg["keys"]
    return out
