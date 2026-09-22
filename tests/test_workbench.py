"""The workbench discovers books on disk and reads episode artifacts, all under outputs/.

Covered here: discovery skips working dirs and keeps unmanaged books; the episode listing flags
what each directory holds; media serving is contained to the book's own directory and to media
extensions - a path escaping it, or a .json, is not a media file.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from novel_manga.application.dashboard import workbench

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_HEADERS = ["镜号", "摄影角度", "景别", "画面内容 / 动作", "场景", "台词 / 声音",
                 "机位 / 运镜 / 连续性", "叙事目的", "预算秒"]


def write_sheet(path, shots):
    """A real workbook, because what is being tested is whether the real reader can read it."""
    book, rels = ET.Element(f"{{{S}}}workbook"), ET.Element(f"{{{P}}}Relationships")
    listed = ET.SubElement(book, f"{{{S}}}sheets")
    ET.SubElement(listed, f"{{{S}}}sheet", {"name": "分镜", "sheetId": "1", f"{{{R}}}id": "r1"})
    ET.SubElement(rels, f"{{{P}}}Relationship", {"Id": "r1", "Target": "worksheets/s1.xml"})
    sheet = ET.Element(f"{{{S}}}worksheet")
    data = ET.SubElement(sheet, f"{{{S}}}sheetData")
    for i, values in enumerate([SHEET_HEADERS, *shots], 1):
        row = ET.SubElement(data, f"{{{S}}}row", {"r": str(i)})
        for j, value in enumerate(values):
            numeric = i > 1 and j == 8
            cell = ET.SubElement(row, f"{{{S}}}c", {"r": f"{chr(65 + j)}{i}", "t": "n" if numeric else "inlineStr"})
            if numeric:
                ET.SubElement(cell, f"{{{S}}}v").text = value
            else:
                ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = value
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        for name, part in {"xl/workbook.xml": book, "xl/_rels/workbook.xml.rels": rels,
                           "xl/worksheets/s1.xml": sheet}.items():
            archive.writestr(name, ET.tostring(part, encoding="utf-8"))
    return path


def make_root(tmp_path, *, managed=("wuyue",)):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pipeline.json").write_text(json.dumps({
        "novels": [{"id": n, "title": {"wuyue": "雾月秘典"}.get(n, n)} for n in managed]}),
        encoding="utf-8")
    return tmp_path


def make_book(root, book_id, *, episodes=()):
    directory = root / "outputs" / book_id
    directory.mkdir(parents=True)
    (directory / "profile.json").write_text(json.dumps({"style": "3d", "frame": "16:9"}), encoding="utf-8")
    for n in episodes:
        episode = directory / f"{book_id}_{n}"
        episode.mkdir()
        (episode / f"{book_id}_{n}.mp4").write_bytes(b"\x00" * 64)
        (episode / "chapter_script.md").write_text(f"# 第{n}集 剧本", encoding="utf-8")
        (episode / "clip_plan.json").write_text(json.dumps({
            "clips": [{"kind": "video", "request_seconds": 5, "text": "甲走进门"},
                      {"kind": "video", "request_seconds": 7, "prompt": "乙抬头"}],
            "totals": {"estimated_seconds": 12}}), encoding="utf-8")
    return directory


def test_discovery_finds_books_and_skips_working_dirs(tmp_path):
    root = make_root(tmp_path)
    make_book(root, "wuyue", episodes=[1])
    make_book(root, "shengtang")                                   # on disk, not in pipeline.json
    (root / "outputs" / "_rejected").mkdir()                       # working dir: underscore
    (root / "outputs" / "api-maoxing-v22").mkdir()                 # experiment dir: no profile.json
    rows = {b["id"]: b for b in workbench.books(root)["books"]}
    assert set(rows) == {"wuyue", "shengtang"}
    assert rows["wuyue"]["managed"] is True and rows["wuyue"]["title"] == "雾月秘典"
    assert rows["shengtang"]["managed"] is False and rows["shengtang"]["style"] == "3d"
    assert rows["wuyue"]["episodes"] == 1


def test_episode_listing_flags_and_detail_reads(tmp_path):
    root = make_root(tmp_path)
    make_book(root, "wuyue", episodes=[3])
    listing = workbench.episodes(root, "wuyue")["episodes"]
    assert listing[0]["episode"] == 3 and listing[0]["video"] and listing[0]["script"]
    assert not listing[0]["review"]

    detail = workbench.episode(root, "wuyue", 3)
    assert detail["video"] == "wuyue_3/wuyue_3.mp4"
    assert detail["script_md"]["text"].startswith("# 第3集")
    assert [c["offset"] for c in detail["clips"]] == [0.0, 5.0]   # jump list accumulates seconds
    assert detail["clips"][0]["label"] == "甲走进门"
    assert detail["review"] is None


def test_media_is_contained_to_the_book_and_to_media_types(tmp_path):
    root = make_root(tmp_path)
    book = make_book(root, "wuyue", episodes=[1])
    assert workbench.media_file(root, "wuyue", "wuyue_1/wuyue_1.mp4").name == "wuyue_1.mp4"
    with pytest.raises(KeyError):
        workbench.media_file(root, "wuyue", "../wuyue/profile.json")       # escaping the book dir
    with pytest.raises(KeyError):
        workbench.media_file(root, "wuyue", "profile.json")                # not a media extension
    with pytest.raises(KeyError):
        workbench.media_file(root, "wuyue", "wuyue_1/missing.mp4")         # does not exist
    with pytest.raises(KeyError):
        workbench.media_file(root, "../etc", "x.mp4")                      # not a book id
    with pytest.raises(KeyError):
        workbench.media_file(root, "ghost", "x.mp4")                       # not a discovered book
    assert (book / "profile.json").is_file()


def test_experiments_scans_registered_and_unregistered(tmp_path):
    root = make_root(tmp_path)
    make_book(root, "wuyue")
    done = root / "outputs" / "experiments" / "planner-ab"
    done.mkdir(parents=True)
    (done / "manifest.json").write_text(json.dumps({
        "created_at": "2026-09-14", "comparison": "endpoint swap", "model": "Qwen3.8-27B-Project"}),
        encoding="utf-8")
    (done / "final-summary.json").write_text("{}", encoding="utf-8")
    loose = root / "outputs" / "experiments" / "quick-try"
    loose.mkdir()
    (loose / "notes.txt").write_text("随便试试", encoding="utf-8")

    data = workbench.experiments(root)
    rows = {e["name"]: e for e in data["experiments"]}
    assert rows["planner-ab"]["registered"] is True and rows["planner-ab"]["status"] == "完成"
    assert rows["planner-ab"]["comparison"] == "endpoint swap"
    assert rows["quick-try"]["registered"] is False and rows["quick-try"]["status"] == "活跃"
    assert data["agents"]["books"] == []                       # every book here plans locally


def test_agent_books_surface_on_the_experiments_shelf(tmp_path):
    root = make_root(tmp_path)
    book = make_book(root, "wuyue")
    profile = json.loads((book / "profile.json").read_text(encoding="utf-8"))
    profile["planning_backend"] = "sandbox_agent"
    (book / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    data = workbench.experiments(root)
    assert data["agents"]["books"][0]["backend"] == "sandbox_agent"
    assert workbench.books(root)["books"][0]["backend"] == "sandbox_agent"


def test_media_follows_a_symlinked_assets_dir(tmp_path):
    """zhutian-card's series_assets is a symlink into zhutian-fast's; the card must still serve."""
    root = make_root(tmp_path)
    make_book(root, "wuyue")
    shared = root / "outputs" / "shared-assets"          # lives outside the book, like zhutian-fast
    cards = shared / "characters" / "character_001"
    cards.mkdir(parents=True)
    (cards / "turnaround.jpeg").write_bytes(b"\xff\xd8\xff")
    (root / "outputs" / "wuyue" / "series_assets").symlink_to(shared)
    served = workbench.media_file(root, "wuyue", "series_assets/characters/character_001/turnaround.jpeg")
    assert served.read_bytes() == b"\xff\xd8\xff"
    with pytest.raises(KeyError):
        workbench.media_file(root, "wuyue", "series_assets/../../wuyue/profile.json")


def test_thumbnails_are_downscaled_cached_and_images_only(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    root = make_root(tmp_path)
    book = make_book(root, "wuyue")
    cards = book / "series_assets" / "characters" / "character_001"
    cards.mkdir(parents=True)
    with Image.new("RGB", (2000, 3000), (30, 60, 90)) as big:
        big.save(cards / "turnaround.jpeg", "JPEG")

    thumb = workbench.thumbnail(root, "wuyue", "series_assets/characters/character_001/turnaround.jpeg", 520)
    with Image.open(thumb) as image:
        assert image.width == 520 and image.height == 780
    assert thumb.stat().st_size < (cards / "turnaround.jpeg").stat().st_size
    assert workbench.thumbnail(root, "wuyue", "series_assets/characters/character_001/turnaround.jpeg", 520) == thumb
    with pytest.raises(KeyError):
        workbench.thumbnail(root, "wuyue", "series_assets/voices/甲.wav", 520)   # not an image
    with pytest.raises(KeyError):
        workbench.thumbnail(root, "wuyue", "series_assets/characters/character_001/spec.json", 520)


def test_episode_detail_carries_the_agents_own_storyboard(tmp_path):
    root = make_root(tmp_path)
    book = make_book(root, "wuyue", episodes=[7])
    episode = book / "wuyue_7"
    (episode / "agent_storyboard.json").write_text(json.dumps({
        "status": "accepted", "skill": "分镜", "attempt": 2, "accepted_at": "2026-09-21 22:10"}),
        encoding="utf-8")
    write_sheet(episode / "agent_storyboard" / "分镜表.xlsx",
                [["1", "平视", "中景", "席勒站在讲台后。", "教室", "席勒（说）：“随堂测验。”", "固定", "立住人物", "6"]])
    profile = json.loads((book / "profile.json").read_text(encoding="utf-8"))
    profile["planning_backend"] = "sandbox_agent"
    (book / "profile.json").write_text(json.dumps(profile), encoding="utf-8")

    detail = workbench.episode(root, "wuyue", 7)
    assert detail["backend"] == "sandbox_agent"
    board = detail["agent_storyboard"]
    assert board["record"]["skill"] == "分镜"
    assert board["sheets"][0]["rows"][0]["motion_prompt"] == "席勒站在讲台后。"
    assert board["sheets"][0]["rows"][0]["edit_seconds"] == 6.0
    assert detail["video"] == "wuyue_7/wuyue_7.mp4"


def test_episode_without_agent_has_no_storyboard(tmp_path):
    root = make_root(tmp_path)
    make_book(root, "wuyue", episodes=[1])
    detail = workbench.episode(root, "wuyue", 1)
    assert detail["backend"] == "local"
    assert detail["agent_storyboard"] is None


def test_assets_reads_specs_images_and_voices(tmp_path):
    root = make_root(tmp_path)
    book = make_book(root, "wuyue")
    char = book / "series_assets" / "characters" / "character_001"
    char.mkdir(parents=True)
    (char / "spec.json").write_text(json.dumps({"name": "甲", "role": "主角"}), encoding="utf-8")
    (char / "turnaround.jpeg").write_bytes(b"\xff")
    voices = book / "series_assets" / "voices"
    voices.mkdir()
    (voices / "甲.wav").write_bytes(b"RIFF")
    data = workbench.assets(root, "wuyue")
    assert data["characters"][0]["spec"]["name"] == "甲"
    assert data["characters"][0]["images"] == ["turnaround.jpeg"]
    assert data["voices"] == [{"name": "甲.wav", "type": "audio/wav"}]
