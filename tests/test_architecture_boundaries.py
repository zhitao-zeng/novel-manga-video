"""The completed package split must not regress into command or experiment imports."""
import ast
from pathlib import Path


def test_production_dependencies_have_no_cycles_or_upward_rule_imports():
    root = Path(__file__).resolve().parents[1]
    source = root / 'src'
    modules = {}
    for path in (source / 'novel_manga').rglob('*.py'):
        name = '.'.join(path.relative_to(source).with_suffix('').parts)
        if name.endswith('.__init__'):
            name = name.removesuffix('.__init__')
        modules[name] = path
    commands = {p.stem for p in (root / 'scripts').glob('*.py')}
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(), feature_version=(3, 11))
        package = name if path.name == '__init__.py' else name.rsplit('.', 1)[0]
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                origin = node.module or ''
                if node.level:
                    pieces = package.split('.')
                    origin = '.'.join(pieces[:len(pieces) - node.level + 1] + ([origin] if origin else []))
                targets = [origin, *(origin + '.' + a.name for a in node.names)]
            for target in targets:
                assert target.split('.')[0] not in commands and not target.startswith(('scripts.', 'experiments.')), (name, target)
                if name.startswith(('novel_manga.story.', 'novel_manga.planning.', 'novel_manga.repair.')):
                    assert not target.startswith('novel_manga.application.'), (name, target)
                if target in modules and target != name:
                    graph[name].add(target)
    visited, active = set(), []
    def visit(name):
        assert name not in active, active + [name]
        if name in visited:
            return
        active.append(name)
        for target in graph[name]:
            visit(target)
        active.pop(); visited.add(name)
    for name in graph:
        visit(name)


def test_tests_use_support_fixtures_instead_of_other_test_modules():
    for path in Path(__file__).parent.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            targets = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            assert not any(name.startswith('test_') for name in targets), path
