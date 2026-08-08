from pathlib import Path

from PIL import Image, ImageDraw

from novel_manga.face_consistency import face_region_similarity


def _portrait(path: Path, *, face: str, hair: str, offset: int = 0) -> None:
    image = Image.new("RGB", (480, 800), (28, 35, 52))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120 + offset, 100, 360 + offset, 360), fill=face, outline=hair, width=28)
    draw.polygon(((120 + offset, 170), (170 + offset, 70), (360 + offset, 170)), fill=hair)
    draw.ellipse((185 + offset, 215, 205 + offset, 235), fill="black")
    draw.ellipse((275 + offset, 215, 295 + offset, 235), fill="black")
    draw.arc((210 + offset, 240, 270 + offset, 300), 10, 170, fill="black", width=6)
    draw.rectangle((120, 370, 360, 760), fill=(42, 90, 150))
    image.save(path, "JPEG", quality=95)


def test_face_region_similarity_is_high_for_same_character(tmp_path: Path) -> None:
    reference = tmp_path / "reference.jpeg"
    candidate = tmp_path / "candidate.jpeg"
    _portrait(reference, face="#e9bea0", hair="#1b1820")
    _portrait(candidate, face="#e9bea0", hair="#1b1820", offset=8)

    report = face_region_similarity(reference, candidate)

    assert report["status"] == "scored"
    assert report["score"] >= 75
