"""Book discovery and content reads for the workbench pages.

The board pages track the configured production novels.  The workbench discovers every book on
disk - an outputs/<dir> holding a profile.json is a book, whether production manages it or not -
and reads the artifacts an episode directory already holds: the script, the prompts, the plans,
the reviews and the rendered film itself.  Everything here is read-only.
"""
from __future__ import annotations
import json
import os
import re
import threading
import time
from pathlib import Path

from novel_manga.application.configuration import pipeline_config

MEDIA_TYPES = {".mp4": "video/mp4", ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp",
               ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}

TEXT_CAP = 400_000      # a text artifact larger than this is cut, with its full size noted
LOG_TAIL = 200          # log files ship their last lines, not the whole run
BOOK_ID = re.compile(r"[A-Za-z0-9._-]+")

_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 30


def _cached(key, build):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = build()
    with _cache_lock:
        _cache[key] = (time.monotonic(), data)
    return data


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path, cap: int = TEXT_CAP):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    size = path.stat().st_size
    if len(text) > cap:
        return {"text": text[:cap], "truncated": True, "size": size}
    return {"text": text, "truncated": False, "size": size}


def _read_tail(path: Path, lines: int = LOG_TAIL):
    read = _read_text(path, cap=600_000)
    if read is None:
        return None
    tail = read["text"].splitlines()[-lines:]
    return {"text": "\n".join(tail), "tail_of": read["size"]}


def book_dir(root, book_id: str) -> Path:
    """The outputs directory of a discovered book; anything else is not addressed by these pages."""
    if not BOOK_ID.fullmatch(book_id):
        raise KeyError(book_id)
    directory = Path(root) / "outputs" / book_id
    if not (directory / "profile.json").is_file():
        raise KeyError(book_id)
    return directory


def _episode_dirs(directory: Path) -> list[Path]:
    try:
        with os.scandir(directory) as entries:
            return sorted((Path(e.path) for e in entries if e.is_dir()
                           and e.name.startswith(directory.name + "_")
                           and e.name.rsplit("_", 1)[-1].isdigit()),
                          key=lambda p: int(p.name.rsplit("_", 1)[-1]))
    except OSError:
        return []


def books(root) -> dict:
    """Every book on disk: the pipeline's managed ones first, then anything with a profile.json."""
    root = Path(root)
    configured = pipeline_config(root).get("novels", [])
    indexed = sorted(enumerate(configured), key=lambda item: item[1].get("display_order", item[0]))
    order = {n["id"]: i for i, (_, n) in enumerate(indexed)}
    titles = {n["id"]: n.get("title", n["id"]) for n in configured}
    outputs = root / "outputs"
    rows = []
    try:
        with os.scandir(outputs) as entries:
            dirs = sorted(Path(e.path) for e in entries if e.is_dir() and not e.name.startswith("_"))
    except OSError:
        dirs = []
    for directory in dirs:
        if not (directory / "profile.json").is_file():
            continue
        book_id = directory.name
        profile = _read_json(directory / "profile.json") or {}
        novel = _read_json(directory / "novel.json") or {}
        delivery = _read_json(directory / "delivery.json") or {}
        assets_dir = directory / "series_assets"
        rows.append({
            "id": book_id,
            "title": novel.get("title") or titles.get(book_id) or book_id,
            "managed": book_id in titles,
            "backend": profile.get("planning_backend", "local"),
            "style": profile.get("style"), "frame": profile.get("frame"), "genre": profile.get("genre"),
            "episodes": len(_episode_dirs(directory)),
            "deliverable": delivery.get("deliverable"), "total": delivery.get("total"),
            "has_assets": assets_dir.is_dir(),
        })
    rows.sort(key=lambda r: (r["id"] not in titles, order.get(r["id"], 999), r["id"]))
    return {"now": time.strftime("%F %T"), "books": rows}


def episodes(root, book_id: str) -> dict:
    directory = book_dir(root, book_id)

    def build():
        rows = []
        for episode_dir in _episode_dirs(directory):
            try:
                with os.scandir(episode_dir) as entries:
                    names = {e.name for e in entries}
            except OSError:
                continue
            number = int(episode_dir.name.rsplit("_", 1)[-1])
            rows.append({
                "episode": number,
                "video": f"{episode_dir.name}.mp4" in names,
                "script": "chapter_script.md" in names,
                "plan": "clip_plan.md" in names,
                "review": "episode_review.json" in names,
                "qc": "media_qc_report.json" in names,
                "repair_candidate": "repair_candidate" in names,
                "mtime": episode_dir.stat().st_mtime,
            })
        return {"book": book_id, "episodes": rows}

    return _cached(("episodes", str(directory)), build)


def _clip_rows(plan: dict | None) -> list[dict]:
    """One row per clip for the jump list: index, kind, seconds and a short label."""
    if not isinstance(plan, dict):
        return []
    rows = []
    offset = 0.0
    for index, clip in enumerate(plan.get("clips") or []):
        if not isinstance(clip, dict):
            continue
        seconds = clip.get("request_seconds") or clip.get("estimated_seconds") or 0
        label = ""
        for field in ("text", "prompt", "event", "narration", "description"):
            if clip.get(field):
                label = str(clip[field])[:80]
                break
        rows.append({"n": index + 1, "kind": clip.get("kind"), "seconds": seconds,
                     "offset": round(offset, 2), "label": label})
        offset += float(seconds or 0)
    return rows


def _agent_storyboard(directory: Path) -> dict | None:
    """The sandbox agent's own storyboard for this episode, when one was imported: the import
    record, and the sheet itself read by the same workbook reader the import uses."""
    record_path = directory / "agent_storyboard.json"
    if not record_path.is_file():
        return None
    from novel_manga.planning.storyboard import read_workbook
    result = {"record": _read_json(record_path) or {}, "sheets": []}
    sheet_dir = directory / "agent_storyboard"
    if sheet_dir.is_dir():
        for xlsx in sorted(sheet_dir.glob("*.xlsx")):
            try:
                for sheet in read_workbook(xlsx):
                    result["sheets"].append({"name": sheet.name, "file": xlsx.name,
                                             "notes": list(sheet.notes),
                                             "rows": [dict(row) for row in sheet.rows]})
            except Exception as error:  # noqa: BLE001 - a broken sheet must not break the page
                result["sheets"].append({"name": xlsx.stem, "file": xlsx.name, "error": str(error)})
    return result


def episode(root, book_id: str, number: int) -> dict:
    directory = book_dir(root, book_id) / f"{book_id}_{number}"
    if not directory.is_dir():
        raise KeyError(number)
    name = directory.name
    profile = _read_json(directory.parent / "profile.json") or {}
    plan = _read_json(directory / "clip_plan.json")
    prompts = []
    for path in sorted(directory.glob("request_attempt_*.json")) + sorted(directory.glob("analysis_attempt_*.txt")):
        body = _read_json(path) if path.suffix == ".json" else None
        prompts.append({"name": path.name,
                        "json": body if body is not None else None,
                        "text": None if body is not None else _read_text(path)})
    logs = {}
    for log in ("plan.log", "render.log", "h3_prompts.log"):
        tail = _read_tail(directory / log)
        if tail is not None:
            logs[log] = tail
    media = lambda fn: f"{name}/{fn}" if (directory / fn).is_file() else None
    previous = directory / "repair_history" / "previous_final.mp4"
    return {
        "book": book_id, "episode": number,
        "backend": profile.get("planning_backend", "local"),
        "agent_storyboard": _agent_storyboard(directory),
        "video": media(f"{name}.mp4"),
        "cover": media(f"{name}_cover.jpeg"),
        "ending": media(f"{name}_ending.jpeg"),
        "previous_video": f"{name}/repair_history/previous_final.mp4" if previous.is_file() else None,
        "script_md": _read_text(directory / "chapter_script.md"),
        "plan_md": _read_text(directory / "clip_plan.md"),
        "clips": _clip_rows(plan),
        "review": _read_json(directory / "episode_review.json"),
        "qc": _read_json(directory / "media_qc_report.json"),
        "report": _read_json(directory / "chapter_script_report.json"),
        "prompts": prompts,
        "logs": logs,
    }


def _asset_dirs(directory: Path) -> list[dict]:
    rows = []
    try:
        with os.scandir(directory) as entries:
            dirs = sorted(Path(e.path) for e in entries if e.is_dir())
    except OSError:
        return rows
    for asset in dirs:
        spec = _read_json(asset / "spec.json") or {}
        try:
            with os.scandir(asset) as entries:
                images = sorted(e.name for e in entries
                                if e.is_file() and Path(e.name).suffix.lower() in MEDIA_TYPES
                                and MEDIA_TYPES[Path(e.name).suffix.lower()].startswith("image/"))
        except OSError:
            images = []
        rows.append({"id": asset.name, "spec": spec, "images": images})
    return rows


def assets(root, book_id: str) -> dict:
    directory = book_dir(root, book_id) / "series_assets"
    if not directory.is_dir():
        return {"book": book_id, "characters": [], "locations": [], "voices": [], "avatars": []}

    def flat(kind: str) -> list[dict]:
        """Media files that sit directly under the kind directory (voices, avatars)."""
        target = directory / kind
        rows = []
        try:
            with os.scandir(target) as entries:
                for e in sorted(entries, key=lambda x: x.name):
                    suffix = Path(e.name).suffix.lower()
                    if e.is_file() and suffix in MEDIA_TYPES:
                        rows.append({"name": e.name, "type": MEDIA_TYPES[suffix]})
        except OSError:
            pass
        return rows

    return _cached(("assets", str(directory)), lambda: {
        "book": book_id,
        "characters": _asset_dirs(directory / "characters"),
        "locations": _asset_dirs(directory / "locations"),
        "voices": flat("voices"),
        "avatars": flat("avatars"),
    })


def experiments(root) -> dict:
    """The research shelf: every outputs/experiments/<dir>, plus which books an agent plans.

    An experiment registers itself with a manifest.json (the benchmark harness already writes
    one); a directory without one is still listed, marked 未登记, so work done in another session
    shows up either way.  Status is inferred from what landed: a final summary means finished,
    recent file activity means running.
    """
    root = Path(root)
    directory = root / "outputs" / "experiments"
    rows = []
    try:
        with os.scandir(directory) as entries:
            dirs = sorted(Path(e.path) for e in entries if e.is_dir())
    except OSError:
        dirs = []
    for experiment in dirs:
        manifest = _read_json(experiment / "manifest.json") or {}
        results, latest = [], 0.0
        try:
            with os.scandir(experiment) as entries:
                for e in entries:
                    latest = max(latest, e.stat().st_mtime)
                    if e.name == "manifest.json":
                        continue
                    if (e.is_file() and (e.name.endswith(".json") or e.name == "report.md")) or e.name == "evaluation":
                        results.append(e.name)
        except OSError:
            pass
        results = sorted(results)[:8]
        if (experiment / "final-summary.json").is_file():
            status = "完成"
        elif latest and time.time() - latest < 2 * 3600:
            status = "活跃"
        else:
            status = "存档"
        rows.append({
            "name": experiment.name,
            "registered": bool(manifest),
            "created_at": manifest.get("created_at"),
            "comparison": manifest.get("comparison"),
            "model": manifest.get("model"), "head": manifest.get("head"),
            "endpoints": manifest.get("endpoints"),
            "status": status, "results": results,
            "latest": latest,
        })
    rows.sort(key=lambda r: r["latest"], reverse=True)

    shelf = root / "outputs" / "agent-test"
    agent_test = []
    try:
        with os.scandir(shelf) as entries:
            for e in sorted(entries, key=lambda x: x.name):
                if e.is_dir():
                    with os.scandir(e.path) as inner:
                        count = sum(1 for _ in inner)
                    agent_test.append({"name": e.name, "files": count})
                elif e.is_file():
                    agent_test.append({"name": e.name, "files": None})
    except OSError:
        pass
    agent_books = [{"id": b["id"], "title": b["title"], "backend": b["backend"]}
                   for b in books(root)["books"] if b["backend"] != "local"]
    return {"now": time.strftime("%F %T"), "experiments": rows,
            "agents": {"books": agent_books, "agent_test": agent_test}}


def samples(root) -> dict:
    """The same character drawn in several styles, as a character-by-style matrix.

    Style sampling writes outputs/_sample-<who>-<style>/ and outputs/_style-sample-<style>/;
    each is a tiny book with its own series_assets.  Character names come from spec.json, so a
    renamed character still lands in one row.
    """
    outputs = Path(root) / "outputs"
    try:
        with os.scandir(outputs) as entries:
            dirs = sorted(Path(e.path) for e in entries if e.is_dir()
                          and (e.name.startswith("_sample-") or e.name.startswith("_style-sample-")))
    except OSError:
        dirs = []
    matrix: dict[str, dict] = {}
    styles = set()
    for directory in dirs:
        style = directory.name.removeprefix("_style-sample-") if directory.name.startswith("_style-sample-") \
            else directory.name.removeprefix("_sample-").split("-", 1)[-1]
        for card in _asset_dirs(directory / "series_assets" / "characters"):
            name = str(card["spec"].get("name") or card["id"])
            matrix.setdefault(name, {})[style] = {
                "dir": directory.name, "id": card["id"], "images": card["images"],
                "spec": card["spec"],
            }
            styles.add(style)
    return {"now": time.strftime("%F %T"), "styles": sorted(styles),
            "characters": [{"name": name, "cells": cells} for name, cells in sorted(matrix.items())]}


def recent(root, limit: int = 50) -> dict:
    """The newest episodes across every discovered book, with their gate outcomes.

    What ran while you were elsewhere, and whether it held: plan status, QC pass, review flags,
    the film itself.  Only the shown rows earn artifact reads; the scan is directory metadata.
    """
    root = Path(root)

    def build():
        rows = []
        for book in books(root)["books"]:
            directory = root / "outputs" / book["id"]
            for episode_dir in _episode_dirs(directory):
                try:
                    mtime = episode_dir.stat().st_mtime
                except OSError:
                    continue
                rows.append({"book": book["id"], "title": book["title"], "mtime": mtime,
                             "episode": int(episode_dir.name.rsplit("_", 1)[-1])})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        shown = rows[:limit]
        for row in shown:
            directory = root / "outputs" / row["book"] / f"{row['book']}_{row['episode']}"
            report = _read_json(directory / "chapter_script_report.json") or {}
            qc = _read_json(directory / "media_qc_report.json") or {}
            review = _read_json(directory / "episode_review.json") or {}
            row.update({
                "video": (directory / f"{directory.name}.mp4").is_file(),
                "plan_status": report.get("status"),
                "model": report.get("model"),
                "qc_passed": qc.get("passed"),
                "review_flags": len(review.get("flags") or []),
            })
        experiments_dir = root / "outputs" / "experiments"
        experiments = []
        try:
            with os.scandir(experiments_dir) as entries:
                for e in entries:
                    if e.is_dir():
                        experiments.append({"name": e.name, "mtime": e.stat().st_mtime})
        except OSError:
            pass
        experiments.sort(key=lambda r: r["mtime"], reverse=True)
        return {"now": time.strftime("%F %T"), "episodes": shown, "experiments": experiments[:8]}

    return _cached(("recent", str(root)), build)


def health(root, book_id: str) -> dict:
    """Every episode's production state as a clickable list, not just counts.

    The judgement is the production lane's own: inventory._episode_state reads exactly what
    thin_batch and the conductor read (status, review state, retry budget), so this page can
    never disagree with what the scheduler will do.  Rows needing a human come first.
    """
    directory = book_dir(root, book_id)

    def build():
        from novel_manga.application.dashboard import config as dconfig
        from novel_manga.application.dashboard import inventory
        from novel_manga.application.dashboard.store import scan_book
        keys = dconfig._lane_keys().get(book_id, [])
        h3_lane = bool(keys) and all(k.get("base_url") for k in keys)
        scan = scan_book(directory)
        rows = []
        for episode_dir in _episode_dirs(directory):
            state = inventory._episode_state(episode_dir, h3_lane, scan=scan)
            rows.append({
                "episode": int(episode_dir.name.rsplit("_", 1)[-1]),
                "status": state["status"], "review": state["review"],
                "blocked": state["blocked"], "runs": state["runs"],
                "reason": inventory.attention_reason(state, h3_lane),
                "mtime": state["final_mtime"] or 0,
            })
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        attention = [r for r in rows if r["reason"]]
        attention.sort(key=lambda r: (not r["blocked"], r["episode"]))
        return {"book": book_id, "h3_lane": h3_lane, "counts": counts,
                "attention": attention, "rows": rows}

    return _cached(("health", str(directory)), build)


def bible(root, book_id: str) -> dict:
    """What the reading produced, as one page: cast, places, volumes, growth, and the pairs
    that look or sound alike.  All files are optional - a young book shows what it has."""
    directory = book_dir(root, book_id)
    story = _read_json(directory / "story_bible.json") or {}
    growth = _read_json(directory / "bible_growth.json") or {}
    growth_rows = [{"chapter": int(ch),
                    "characters": sorted(entry.get("characters") or []),
                    "locations": sorted(entry.get("locations") or []),
                    "needs_human": bool(entry.get("needs_human"))}
                   for ch, entry in growth.items() if str(ch).isdigit() and isinstance(entry, dict)]
    growth_rows.sort(key=lambda r: r["chapter"])
    md = _read_text(directory / "story_bible.md")
    return {
        "book": book_id,
        "meta": {"title": story.get("novel_title"), "genre": story.get("genre"),
                 "visual_style": story.get("visual_style"), "palette": story.get("palette"),
                 "typography": story.get("typography"),
                 "style_fingerprint": story.get("style_fingerprint")},
        "characters": story.get("characters") or [],
        "locations": story.get("locations") or [],
        "continuity_rules": story.get("continuity_rules") or [],
        "aliases": _read_json(directory / "bible_aliases.json") or {},
        "cast": _read_json(directory / "reading_cast.json"),
        "volumes": _read_json(directory / "volumes.json") or [],
        "growth": growth_rows,
        "confusable": _read_json(directory / "confusable_pairs.json"),
        "lookalikes": _read_json(directory / "card_lookalikes.json"),
        "story_bible_md": md,
    }


def media_file(root, book_id: str, relative: str) -> Path:
    """A whitelisted media file inside the book's own directory; nothing else is served.

    The check is lexical - a '..' anywhere in the relative path is refused - and deliberately
    not resolved: books here share assets through symlinks (zhutian-card's series_assets is a
    link into zhutian-fast's), and resolving would place the real file outside the book.
    """
    directory = book_dir(root, book_id)
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        raise KeyError(relative)
    target = directory.joinpath(*parts)
    if target.suffix.lower() not in MEDIA_TYPES or not target.is_file():
        raise KeyError(relative)
    return target


def thumbnail(root, book_id: str, relative: str, width: int = 520) -> Path:
    """A downscaled JPEG of an image asset, cached under outputs/.dashboard/thumbs.

    Cards are 3-5 MB at 1152x2048 and a gallery page shows hundreds; the browser only needs a
    few hundred pixels.  The cache key carries the source mtime, so a redrawn card earns a new
    thumbnail, and the file lands by rename so a half-written one is never served.
    """
    source = media_file(root, book_id, relative)
    if not MEDIA_TYPES[source.suffix.lower()].startswith("image/"):
        raise KeyError(relative)
    width = max(64, min(int(width), 1600))
    stat = source.stat()
    import hashlib
    key = hashlib.sha1(f"{book_id}/{relative}:{stat.st_mtime_ns}:{width}".encode()).hexdigest()[:20]
    target = Path(root) / "outputs" / ".dashboard" / "thumbs" / book_id / f"{key}.jpg"
    if target.is_file():
        return target
    from PIL import Image
    with Image.open(source) as image:
        image = image.convert("RGB")
        if image.width > width:
            image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".tmp")
        image.save(staging, "JPEG", quality=82)
        os.replace(staging, target)
    return target
