"""Authored skill links resolve locally before a sandbox needs them."""
from pathlib import Path
import re

from novel_manga.application.agents.episode.inputs import SKILL_REFERENCES
from novel_manga.application.agents.episode.session import SKILL_SOURCE


def test_skill_reference_routes_resolve_inside_the_installed_package():
    authored = [SKILL_SOURCE / 'SKILL.md',
                *(SKILL_SOURCE / name for name in SKILL_REFERENCES.values()
                  if not name.startswith('references/official/'))]
    reached = set()
    for document in authored:
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
            if target.startswith(('https://', 'http://', '#')):
                continue
            path = (document.parent / target.split('#', 1)[0]).resolve()
            assert path.is_file(), f'{document.name} links to missing resource {target}'
            assert path.is_relative_to(SKILL_SOURCE.resolve())
            reached.add(path)
    assert all((SKILL_SOURCE / name).resolve() in reached for name in SKILL_REFERENCES.values())
