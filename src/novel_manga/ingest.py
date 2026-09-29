from __future__ import annotations

import re
import json
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from novel_manga.models.source import NovelDocument, Episode
from .splitting import split_episodes, visible_count


SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".docx", ".pdf"}


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode text file: {path}")


def _read_docx(path: Path) -> str:
    document = Document(path)
    blocks = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append("\t".join(cells))
    return "\n\n".join(blocks)


def _read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(page for page in pages if page)
    if not text:
        raise ValueError("PDF contains no extractable text; OCR input is not supported in this build")
    return text


def read_source_text(path: str | Path) -> str:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported file type {suffix}; supported: {sorted(SUPPORTED_SUFFIXES)}")
    if suffix in {".txt", ".md", ".markdown"}:
        text = _read_text(source)
    elif suffix == ".docx":
        text = _read_docx(source)
    else:
        text = _read_pdf(source)

    return re.sub(r"\n{4,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def chapter_table(novel_dir: str | Path) -> list[dict] | None:
    path = Path(novel_dir) / "novel.json"
    return json.loads(path.read_text(encoding="utf-8")).get("chapters") if path.is_file() else None


def recorded_episodes(text: str, chapters: list[dict]) -> list[Episode]:
    """Keep a registered book's numbering; offsets refer to read_source_text's text.

    Old registrations without offsets keep their original heading boundaries. They
    must not silently opt into the new long-chapter split during a later planning run.
    """
    legacy = None
    result = []
    for entry in chapters:
        if "start" in entry and "end" in entry:
            start, end = int(entry["start"]), int(entry["end"])
            if not 0 <= start < end <= len(text):
                raise ValueError(f"registered chapter {entry['index']} is outside its source text")
            raw = text[start:end]
            start += len(raw) - len(raw.lstrip())
            body = raw.strip()
            result.append(Episode(index=int(entry["index"]), source_title=entry["title"],
                                  source_text=body, text_count=visible_count(body),
                                  source_start=start, source_end=start + len(body)))
        else:
            if legacy is None:
                legacy = {(ep.index, ep.source_title): ep
                          for ep in split_episodes(text, split_long_chapters=False)[0]}
            index = int(entry["index"])
            original = legacy.get((index, entry['title']))
            if original is None:
                raise ValueError(f"registered chapter {index} no longer matches its source title")
            result.append(original)
    return result


def registered_chapter_text(novel: dict, chapter: int) -> str:
    episodes = recorded_episodes(read_source_text(novel["source"]), novel.get("chapters", []))
    return next(ep.source_text for ep in episodes if ep.index == chapter)


def read_novel(path: str | Path, novel_id: str, title: str | None = None, *,
               chapters: list[dict] | None = None) -> NovelDocument:
    source = Path(path).resolve()
    text = read_source_text(source)
    if chapters:
        episodes = recorded_episodes(text, chapters)
        chaptered = bool(split_episodes(text, split_long_chapters=False)[1])
    else:
        episodes, chaptered = split_episodes(text)
    return NovelDocument(
        novel_id=str(novel_id),
        title=(title or source.stem).strip(),
        source_path=source,
        text=text,
        episodes=episodes,
        chaptered=chaptered,
    )
