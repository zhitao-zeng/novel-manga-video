"""Existing phase/body/voice reference selection and numbering for a clip."""
from __future__ import annotations

from pathlib import Path
import json
import os
from novel_manga.models import StoryBible
from novel_manga.story.compilation import anchor_of, compact
from thin_phases import load_phases, phase_for, phased
from packing_context_thin import compiler_options

LEAD_ROLES = {"主角", "女主角", "男主角"}


_BODIES_CACHE: dict = {}


def bodies_for(novel_dir, chapter) -> dict[str, tuple[str, str]]:
    """name -> (body's name, body's card) for cast members the entity ledger says act through someone else's body in
    this chapter (occupies_body / impersonates accepted for the book): the picture shows that body, so the clip
    references that card and describes that look.  Empty without a ledger or a card for the body."""
    key = (str(novel_dir), int(chapter or 0))
    if key in _BODIES_CACHE:
        return _BODIES_CACHE[key]
    out: dict[str, tuple[str, str]] = {}
    try:
        from ledger_views_thin import snapshot
        sheet = snapshot(Path(novel_dir), int(chapter))
        raw = json.loads((Path(novel_dir) / "entity" / "entities.json").read_text(encoding="utf-8"))
        entities = raw if isinstance(raw, dict) else {e["id"]: e for e in raw}
        for row in sheet.get("cast", []):
            if row.get("acts_through_other_body") and row.get("card"):
                out[row["name"]] = (str(entities.get(row["body"], {}).get("canonical") or row["body"]), str(row["card"]))
    except Exception:  # noqa: BLE001 - no ledger, chapter not read, or an old ledger layout: nothing changes
        out = {}
    _BODIES_CACHE[key] = out
    return out


def build_references(cast: list[str], location_short: str, bible: StoryBible, location_map: dict[str, str], speakers: tuple[str, ...] = (), novel_dir: Path | None = None, chapter: int | None = None, *, settings=None, identity_data=None) -> tuple[list[dict], list[str], str]:
    settings = settings or compiler_options()
    character_index = {character.name: index for index, character in enumerate(bible.characters, start=1)}
    location_index = {full.split("：", 1)[0].strip(): index for index, full in enumerate(bible.locations, start=1)}
    # The leads carry the story and were the most often face- or costume-swapped
    # in review; they always get their expressions sheet as a second view when
    # it exists on disk, whatever the tier.
    leads = {character.name for character in bible.characters if str(character.role or "") in LEAD_ROLES}
    references: list[dict] = []
    bindings: list[str] = []
    count = 0
    # Two views per actor sharpen identity, but a crowded shot would then carry
    # ten reference images and the model starts blending faces.  Past two named
    # actors, give each one its turnaround only.
    # Off unless asked for: the second view never proved itself and doubled the reference count; the fast
    # tier renders from the turnaround alone (NOVEL_TWO_VIEWS=1 restores the old behaviour for new plans).
    two_views = len(cast) <= settings.two_view_cast_limit and os.environ.get("NOVEL_TWO_VIEWS", "").strip() == "1"
    # A character with phases (series_assets/phases.json) references the card of the phase this chapter is in,
    # and the anchor describes that look - 沈玄川 is white-haired from ch1406, his base card is not.
    phases = identity_data.catalog.phases if identity_data is not None else load_phases(novel_dir) if novel_dir is not None else {}
    by_name = {character.name: character for character in bible.characters}
    bodies = bodies_for(novel_dir, chapter) if novel_dir is not None and chapter else {}
    for name in cast:
        phase = phase_for(phases, name, chapter)
        asset = str(phase["asset_id"]) if phase else f"character_{character_index[name]:03d}"
        look = phased(by_name[name], phase)
        host = bodies.get(name)
        body_note = ""
        if host:
            # 艾琳娜 in 薇奥拉's body is drawn as 薇奥拉: her card, her look, and the prompt says so
            asset = host[1]
            if host[0] in by_name:
                look = phased(by_name[host[0]], phase_for(phases, host[0], chapter))
            body_note = f"（此时在{host[0]}的身体里，外形完全是{host[0]}的样子）"
        changed = tuple(f for f in ("hair", "appearance", "silhouette", "palette") if phase and phase.get(f))
        count += 1
        first = count
        references.append({"tag": f"@图片{first}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/turnaround.jpeg",
                           **({"phase": str(phase.get("label", ""))} if phase else {})})
        sheet = novel_dir is not None and (novel_dir / "series_assets" / "characters" / asset / "expressions.jpeg").is_file()
        lead_sheet = settings.two_view_cast_limit > 0 and sheet and name in leads and os.environ.get("NOVEL_TWO_VIEWS", "").strip() == "1"
        # Only a sheet that exists is referenced: the fast tier never draws one at render time (the full tier
        # did, which is what the old "phase is None" clause assumed), and a plan that promises a missing file
        # fails the pre-render check for the whole episode.  Without a novel_dir to look at, keep the old rule.
        wants_sheet = sheet if novel_dir is not None else phase is None
        if (two_views and wants_sheet) or lead_sheet:
            count += 1
            second = count
            references.append({"tag": f"@图片{second}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/expressions.jpeg"})
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>{body_note}对应@图片{first}和@图片{second}：@图片{first}定五官、发型、年龄感和肤色，"
                f"@图片{second}定身体比例、服装版型、主色和配饰；两张都不采用背景、姿势和构图；"
                # The single-view binding always said this; the two-view one - every lead - did not, and 408 of
                # 雾月's 574 doppelganger clips show an extra person wearing the lead's face.
                "画面中其他任何人都不得使用这两张图的相貌、发型或服装，该角色只能出现一次"
                + (f"。{name}的辨识特征：{anchor}" if anchor else ""))
        else:
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>{body_note}只对应@图片{first}，只采用五官、发型、体型和服装，不采用图片背景、姿势和构图；"
                "不得把该角色的长相用在其他人身上"
                + (f"。{name}的辨识特征：{anchor}" if anchor else ""))
    full = location_map[location_short]
    location_asset = f"location_{location_index[location_short]:03d}"
    count += 1
    references.append({"tag": f"@图片{count}", "role": "location", "name": location_short, "asset_id": location_asset, "path": f"series_assets/locations/{location_asset}/establishing.jpeg"})
    # One reference voice per speaking character that has one in the bank.  The
    # model listens to the references and matches them to the on-screen speakers
    # by itself; it ignores both @音频N text bindings and the order of the audio
    # items (docs/seedance-reference-audio.md), so nothing about voices goes
    # into the prompt.  Cast order here is just for a stable plan file.
    for voice_index, name in enumerate((n for n in cast if n in speakers and n in settings.voices), start=1):
        references.append({"tag": f"@音频{voice_index}", "role": "voice", "name": name, "path": settings.voices[name]})
    description = compact(full.split("：", 1)[1] if "：" in full else full, 40)
    location_binding = f"@图片{count}用于<{location_short}>的建筑、地面、固定道具和光线（{description}），不采用图中人物"
    return references, bindings, location_binding


