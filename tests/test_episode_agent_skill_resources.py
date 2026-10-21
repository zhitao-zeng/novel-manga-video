"""Authored skill links resolve locally before a sandbox needs them."""
from types import SimpleNamespace
import re

from novel_manga.application.agents.episode.inputs import SKILL_REFERENCES, _install_skill
from novel_manga.application.agents.episode.session import SKILL_SOURCE


def test_skill_reference_routes_resolve_inside_the_installed_package(tmp_path):
    skill, references = _install_skill(SimpleNamespace(work_dir=tmp_path))
    package = (tmp_path / skill).parent
    assert (tmp_path / skill).read_bytes() == (SKILL_SOURCE / 'SKILL.md').read_bytes()
    for key, name in SKILL_REFERENCES.items():
        assert (tmp_path / references[key]).read_bytes() == (SKILL_SOURCE / name).read_bytes()
    authored = [tmp_path / skill,
                *(tmp_path / references[key] for key, name in SKILL_REFERENCES.items()
                  if not name.startswith('references/official/'))]
    reached = set()
    for document in authored:
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
            if target.startswith(('https://', 'http://', '#')):
                continue
            path = (document.parent / target.split('#', 1)[0]).resolve()
            assert path.is_file(), f'{document.name} links to missing resource {target}'
            assert path.is_relative_to(package.resolve())
            reached.add(path)
    assert all((tmp_path / name).resolve() in reached for name in references.values())
