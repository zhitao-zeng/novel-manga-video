"""A card that clips have already rendered with is never redrawn: not by the privacy repair when
Seedance rejects a clip, not by the card remediation, and the backfill records the cards existing
renders used."""
from __future__ import annotations
import novel_manga.media.asset_repair as asset_repair

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mark_cards_in_use  # noqa: E402
from novel_manga.media import assets as media_assets
import render_flow_thin  # noqa: E402
import review_cards_thin as review_cards  # noqa: E402


def novel(tmp_path: Path) -> Path:
    root = tmp_path / "wuyue"
    for card in ("character_001", "character_002"):
        d = root / "series_assets" / "characters" / card
        d.mkdir(parents=True)
        (d / "turnaround.jpeg").write_bytes(b"jpeg")
    return root


def ref(card: str) -> dict:
    return {"role": "character", "asset_id": card, "path": f"series_assets/characters/{card}/turnaround.jpeg"}


def test_privacy_repair_leaves_proven_cards_alone(tmp_path, monkeypatch):
    root = novel(tmp_path)
    redrawn = []
    monkeypatch.setattr(asset_repair, 'stylize_card', lambda provider, path: redrawn.append(path.parent.name))
    render_flow_thin.record_privacy_ok(root, [ref("character_001")["path"]])
    runner = SimpleNamespace(novel_dir=root, provider=None, _ok_assets=set())
    clip = {"clip_id": "clip_01", "references": [ref("character_001"), ref("character_002")]}
    # one card unproven: only that one is restyled
    assert asset_repair.repair_privacy_cards(runner, clip) == ["character_002/turnaround.jpeg"]
    assert redrawn == ["character_002"]
    # every card proven: nothing is restyled, the rejection stands
    render_flow_thin.record_privacy_ok(root, [ref("character_002")["path"]])
    redrawn.clear()
    assert asset_repair.repair_privacy_cards(runner, clip) == []
    assert redrawn == []


def test_named_rejected_reference_is_not_redrawn_when_proven(tmp_path, monkeypatch):
    root = novel(tmp_path)
    redrawn = []
    monkeypatch.setattr(asset_repair, 'stylize_card', lambda provider, path: redrawn.append(path.parent.name))
    render_flow_thin.record_privacy_ok(root, [ref("character_001")["path"]])
    runner = SimpleNamespace(novel_dir=root, provider=None, _ok_assets=set())
    clip = {"clip_id": "clip_01", "references": [ref("character_001"), ref("character_002")]}
    assert asset_repair.repair_rejected_reference(runner, clip, 0) == []
    assert redrawn == [] and (root / "series_assets/characters/character_001/turnaround.jpeg").is_file()
    assert asset_repair.repair_rejected_reference(runner, clip, 1) == ["character_002/turnaround.jpeg"]
    assert redrawn == ["character_002"]


def test_remediation_skips_cards_in_use(tmp_path, monkeypatch):
    root = novel(tmp_path)
    monkeypatch.setattr("novel_manga.config.Settings.from_env", lambda **_: None)
    monkeypatch.setattr("novel_manga.media.adapters.FramedPhanRouter", lambda *_: None)
    render_flow_thin.record_privacy_ok(root, [ref("character_001")["path"]])
    report = {"characters": {
        "character_001": {"views": ["turnaround.jpeg"], "actions": ["regenerate"]},
        "character_002": {"views": ["turnaround.jpeg"], "actions": ["regenerate"]}}, "locations": {}}
    done = review_cards.remediate_cards(root, report)
    assert done["in_use"] == ["character_001/turnaround.jpeg"]
    assert done["deleted"] == ["character_002/turnaround.jpeg"]
    assert (root / "series_assets/characters/character_001/turnaround.jpeg").is_file()
    assert not (root / "series_assets/characters/character_002/turnaround.jpeg").exists()


def test_backfill_records_referenced_cards(tmp_path, monkeypatch):
    root = novel(tmp_path)
    attempt = root / "wuyue_7" / "work" / "clips" / "clip_01" / "attempt_01"
    attempt.mkdir(parents=True)
    attempt.joinpath("request.json").write_text(json.dumps({"references": [
        "series_assets/characters/character_001/turnaround.jpeg", "series_assets/locations/location_003/establishing.jpeg"]}))
    monkeypatch.setattr(sys, "argv", ["mark_cards_in_use.py", "--novel-dir", str(root), "--apply"])
    assert mark_cards_in_use.main() == 0
    assert render_flow_thin.load_privacy_ok(root) == {
        "series_assets/characters/character_001/turnaround.jpeg", "series_assets/locations/location_003/establishing.jpeg"}
