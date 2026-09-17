"""Card review reports and existing bounded asset remediation workflow."""
from __future__ import annotations

import json
import time
from pathlib import Path
import novel_manga.llm.client as model_client
from novel_manga.models.bible import StoryBible as review_models_StoryBible
import novel_manga.review.contracts as review_contracts
from novel_manga.util import atomic_write_json
from thin_profile import load_genre, load_profile
import review_judges_thin as review_judges


def time_conflicts(expected: str, seen: str) -> bool:
    if not expected or seen in {"室内不确定"}:
        return False
    wants_night = any(token in expected for token in ("夜", "月", "烛", "灯"))
    wants_day = any(token in expected for token in ("白日", "白天", "日光", "烈日", "正午", "清晨", "晨"))
    if wants_night and not wants_day:
        return seen == "白天"
    if wants_day and not wants_night:
        return seen == "夜晚"
    return False


def review_cards(novel_dir: Path, include_backups: bool = False, only_ids: set[str] | None = None) -> dict:
    location_policy = load_genre(load_profile(novel_dir)).get("location_policy", "empty")
    bible = review_models_StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    grammar_path = novel_dir / "visual_grammar.json"
    location_time = json.loads(grammar_path.read_text(encoding="utf-8")).get("location_time", {}) if grammar_path.is_file() else {}
    assets = novel_dir / "series_assets"
    report = {"policy": review_contracts.POLICY, "characters": {}, "locations": {}, "flags": [], "missing_cards": []}
    for index, character in enumerate(bible.characters, start=1):
        asset_id = f"character_{index:03d}"
        if only_ids is not None and asset_id not in only_ids:
            continue
        card_dir = assets / "characters" / asset_id
        views = [card_dir / name for name in ("turnaround.jpeg", "expressions.jpeg") if (card_dir / name).is_file()]
        if not views:
            if card_dir.is_dir():  # existed before: deleted for regeneration and not rebuilt yet
                report["missing_cards"].append(f"{asset_id} {character.name}")
            continue
        verdict = review_judges.judge_character_cards(character, views)
        actions = []
        if float(verdict.get("photoreal", 0)) >= review_contracts.PHOTOREAL_LIMIT:
            actions.append("redraw_stylized")
        if not verdict.get("same_person", True) or not verdict.get("matches_description", True) or verdict.get("text_or_extra_people"):
            actions.append("regenerate")
        report["characters"][asset_id] = {"name": character.name, "views": [v.name for v in views], **verdict, "actions": actions}
        if actions:
            report["flags"].append(f"{asset_id} {character.name}: {', '.join(actions)} ({verdict.get('mismatch') or verdict.get('note', '')})")
        model_client.log(f"cards: {asset_id} {character.name} photoreal={verdict.get('photoreal')} match={verdict.get('matches_description')} same={verdict.get('same_person')} {'FLAG ' + ','.join(actions) if actions else 'ok'}")
        if include_backups:
            backups = [card_dir / name for name in ("turnaround.photoreal-rejected.jpeg", "expressions.photoreal-rejected.jpeg") if (card_dir / name).is_file()]
            if backups:
                old = review_judges.judge_character_cards(character, backups)
                report["characters"][asset_id]["backups"] = {"views": [b.name for b in backups], **old}
                model_client.log(f"cards: {asset_id} backups photoreal={old.get('photoreal')} (were rejected by Seedance)")
    for index, location in enumerate(bible.locations, start=1):
        asset_id = f"location_{index:03d}"
        if only_ids is not None and asset_id not in only_ids:
            continue
        view = assets / "locations" / asset_id / "establishing.jpeg"
        short = location.split("：", 1)[0].strip()
        if not view.is_file():
            if view.parent.is_dir():
                report["missing_cards"].append(f"{asset_id} {short}")
            continue
        expected = location_time.get(short, "")
        verdict = review_judges.judge_location_card(location, expected, view, location_policy=location_policy)
        actions = []
        if verdict.get("has_people") or verdict.get("text") or time_conflicts(expected, verdict.get("time_of_day", "")):
            actions.append("regenerate")
        report["locations"][asset_id] = {"name": short, "expected_time": expected, **verdict, "actions": actions}
        if actions:
            report["flags"].append(f"{asset_id} {short}: regenerate ({verdict.get('note', '')})")
        model_client.log(f"cards: {asset_id} {short} people={verdict.get('has_people')} time={verdict.get('time_of_day')} text={verdict.get('text')} {'FLAG' if actions else 'ok'}")
    if report["missing_cards"]:
        report["flags"].append("待重建的卡（所属章节建卡时生成）：" + "、".join(report["missing_cards"]))
        model_client.log(f"cards: not rebuilt yet: {report['missing_cards']}")
    stamp = time.time()
    for entry in (*report["characters"].values(), *report["locations"].values()):
        entry["judged_at"] = stamp
    if only_ids is None:
        atomic_write_json(assets / "cards_review.json", report)
    else:
        # The factory judges each card as it is built: that verdict joins the
        # series-wide report rather than replacing it.
        atomic_write_json(assets / "cards_review.json", merge_card_report(assets / "cards_review.json", report))
    return report


def card_flag(asset_id: str, entry: dict) -> str:
    return f"{asset_id} {entry.get('name', '')}: {', '.join(entry.get('actions') or [])} ({entry.get('mismatch') or entry.get('note', '')})"


def merge_card_report(path: Path, partial: dict) -> dict:
    """The on-disk report with this call's verdicts written over the matching entries."""
    try:
        merged = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        merged = {}
    merged.setdefault("characters", {}).update(partial.get("characters", {}))
    merged.setdefault("locations", {}).update(partial.get("locations", {}))
    merged["policy"] = partial.get("policy")
    merged["missing_cards"] = sorted(set(merged.get("missing_cards", [])) | set(partial.get("missing_cards", [])))
    merged["flags"] = [card_flag(asset_id, entry) for section in ("characters", "locations")
                       for asset_id, entry in sorted(merged[section].items()) if entry.get("actions")]
    return merged


def card_verdict_current(novel_dir: Path, asset_id: str) -> dict | None:
    """The stored verdict for a card whose images have not changed since it was
    judged, shaped like a review_cards() report for that one card; None when
    the card must be judged."""
    assets = novel_dir / "series_assets"
    path = assets / "cards_review.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    section = "characters" if asset_id.startswith("character_") else "locations"
    entry = (report.get(section) or {}).get(asset_id)
    if not entry or not entry.get("judged_at"):
        return None
    card_dir = assets / ("characters" if section == "characters" else "locations") / asset_id
    images = [p for p in card_dir.glob("*.jpeg") if not p.name.endswith("-rejected.jpeg")]
    if not images or max(p.stat().st_mtime for p in images) > float(entry["judged_at"]):
        return None
    return {"policy": report.get("policy"), "characters": {}, "locations": {}, "missing_cards": [],
            section: {asset_id: entry}, "flags": [card_flag(asset_id, entry)] if entry.get("actions") else []}


def remediate_cards(novel_dir: Path, report: dict) -> dict:
    """Stylized redraw for near-photoreal cards, deletion (regenerated by the next
    ``--assets-only`` run) for cards that do not match their description.  Each
    card is touched at most once: a parked backup or a marker file means the
    fix was already tried and the flag stays for the delivery report."""
    from novel_manga.config import Settings
    from novel_manga.media.adapters import FramedPhanRouter
    from novel_manga.media.asset_records import load_privacy_ok
    from novel_manga.media.asset_repair import stylize_card
    from thin_profile import frame_spec, load_profile
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    provider = FramedPhanRouter(settings, frame_spec(load_profile(novel_dir)))
    assets = novel_dir / "series_assets"
    done = {"stylized": [], "deleted": [], "already_tried": [], "in_use": []}
    # Cards that clips have already rendered with (render_clips_thin records them) are never
    # touched: redrawing one changes a face the audience has seen for hundreds of episodes.
    in_use = load_privacy_ok(novel_dir)

    def used(path: Path) -> bool:
        try:
            return str(path.relative_to(novel_dir)) in in_use
        except ValueError:
            return False

    def marker_for(path: Path) -> Path:
        # Not *.jpeg: the runner's purge of unreadable images deleted the old
        # marker names and the one-fix-per-card guard silently vanished.
        return path.parent / f".regenerated.{path.stem}.txt"

    def tried_before(path: Path) -> bool:
        # One fix per card for the whole series: a parked backup (stylized) or a
        # regeneration marker means the card was touched already, whatever the
        # reason this time; changing it again would make the character look
        # different from episode to episode.
        return path.with_suffix(".photoreal-rejected.jpeg").exists() or marker_for(path).exists() or (path.parent / f".regenerated.{path.name}").exists()

    def delete_for_regeneration(path: Path, label: str) -> None:
        marker = marker_for(path)
        if used(path):
            done["in_use"].append(label)
            return
        if tried_before(path):
            done["already_tried"].append(label)
            return
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        for suffix in ("", ".task.json", ".request.json"):
            path.with_suffix(path.suffix + suffix).unlink(missing_ok=True)
            # A leftover stylize backup next to a deliberately deleted card reads
            # as "redraw in flight" to the runner, which would wait for it.
            backup.with_suffix(backup.suffix + suffix).unlink(missing_ok=True)
        marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        done["deleted"].append(label)

    for asset_id, row in report.get("characters", {}).items():
        card_dir = assets / "characters" / asset_id
        for view in row.get("views", []):
            path = card_dir / view
            label = f"{asset_id}/{view}"
            if not path.is_file():
                continue
            if "redraw_stylized" in row["actions"]:
                if used(path):
                    done["in_use"].append(label)
                    continue
                if tried_before(path):
                    done["already_tried"].append(label)
                    continue
                model_client.log(f"cards: stylizing {label}")
                stylize_card(provider, path)
                done["stylized"].append(label)
            elif "regenerate" in row["actions"]:
                delete_for_regeneration(path, label)
    for asset_id, row in report.get("locations", {}).items():
        if "regenerate" in row.get("actions", []):
            delete_for_regeneration(assets / "locations" / asset_id / "establishing.jpeg", f"{asset_id}/establishing.jpeg")
    model_client.log(f"cards: stylized {done['stylized'] or 'none'}, deleted for regeneration {done['deleted'] or 'none'}, "
        f"already tried {done['already_tried'] or 'none'}, left alone because clips use them {done['in_use'] or 'none'}")
    return done
