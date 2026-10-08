"""Read the existing flat books and nested episode-agent books without moving data."""
from __future__ import annotations

from pathlib import Path

from novel_manga.episodes import episode_name, is_episode, parse_episode


def book_directories(root: Path) -> list[Path]:
    """Find profile-bearing books, stopping before their clips/assets/work directories."""
    root = Path(root)
    found, seen = [], set()
    def visit(directory, depth):
        if (directory / 'profile.json').is_file():
            resolved = directory.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(directory)
            return
        if depth == 4:
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir()
                              and not p.name.startswith(('.', '_'))
                              and p.name not in {'series_assets', 'work', 'sandbox', 'experiments'})
        except OSError:
            return
        for child in children:
            visit(child, depth + 1)
    visit(root, 0)
    return sorted(found)


def episode_directories(root: Path, *, novel_id: str | None = None) -> list[Path]:
    """A book or Agent output collection; keep flat-book lexical order for existing callers."""
    root = Path(root)
    books = book_directories(root)
    if not books:
        books = [root]  # Small existing tools/tests may have episodes before profile creation.
    found, seen = [], set()
    for book in books:
        ident = novel_id or book.name
        try:
            children = sorted(p for p in book.iterdir() if p.is_dir()
                              and p.name.startswith(ident + '_') and is_episode(p.name))
        except OSError:
            continue
        for child in children:
            resolved = child.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(child)
    return sorted(found)


def episode_selector(name: str) -> int | str:
    chapter, part = parse_episode(name)
    return chapter if part is None else f'{chapter}-{part}'


def resolve_episode(book: Path, selector: int | str) -> Path:
    token = str(selector)
    parsed = parse_episode(episode_name(book.name, token))
    if parsed is None:
        raise KeyError(selector)
    path = book / episode_name(book.name, *parsed)
    if not path.is_dir():
        raise KeyError(selector)
    return path
