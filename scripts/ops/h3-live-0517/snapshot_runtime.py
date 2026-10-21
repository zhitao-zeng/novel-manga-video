"""Freeze the running H3 installation without modifying its shared environment."""
from collections import deque
from datetime import datetime, timezone
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
import json
import os
import shutil
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROJECT = Path(os.environ.get('H3_SOURCE_PROJECT', '/mnt/disk0/zzt/minimax-h3-r2v-544p')).resolve()
DEPLOY = Path(os.environ.get('H3_SOURCE_DEPLOY', str(PROJECT / 'deploy/g52-1245-20261010'))).resolve()
DEST = Path(os.environ.get('H3_IMAGE_BUILD_CONTEXT', str(Path(__file__).resolve().parent))).resolve()
ROOT = DEST / 'rootfs'
PREFIX = Path(sys.prefix)
SITE = PREFIX / 'lib/python3.11/site-packages'
PYTHON = ROOT / 'opt/h3/python'
sys.path.insert(0, str(PROJECT / 'deps'))


def copy_file(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return str(target)
    try:
        os.link(source.resolve(), target)
    except OSError:
        shutil.copy2(source, target)
    return str(target)


def copy_tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True, copy_function=copy_file,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '.lock', 'direct_url.json'))


def copy_package_directories(selected):
    # Conda metadata can omit compiled extensions. Preserve actual package contents.
    directories = set()
    for dist in selected.values():
        for module in (dist.read_text('top_level.txt') or '').split():
            if '.' in module or module in {'nvidia', 'google', 'cuda', 'ruamel'}:
                continue
            spec = find_spec(module)
            if spec is None:
                continue
            if spec.origin and spec.origin not in {'built-in', 'frozen'}:
                source = Path(spec.origin)
                try:
                    relative = source.relative_to(SITE)
                except ValueError:
                    continue
                if source.is_file():
                    copy_file(source, PYTHON / 'lib/python3.11/site-packages' / relative)
                if spec.submodule_search_locations:
                    directories.add(relative.parent)
        for rel in dist.files or []:
            source = Path(dist.locate_file(rel))
            try:
                relative = source.relative_to(SITE)
            except ValueError:
                continue
            if '..' in relative.parts or len(relative.parts) < 2:
                continue
            depth = 2 if relative.parts[0] in {'nvidia', 'google', 'cuda', 'ruamel'} else 1
            directory = Path(*relative.parts[:depth])
            if (SITE / directory).is_dir():
                directories.add(directory)
    for directory in sorted(directories):
        copy_tree(SITE / directory, PYTHON / 'lib/python3.11/site-packages' / directory)


def main():
    if (DEST / 'snapshot.json').exists():
        raise SystemExit('Snapshot already exists; use the existing frozen context.')
    ROOT.mkdir(exist_ok=True)
    roots = ['sglang[diffusion]', 'accelerate', 'librosa', 'soxr', 'opt-einsum',
             'peft', 'pip', 'setuptools', 'wheel', 'flashinfer-cubin']
    pending = deque(roots)
    selected, seen, missing = {}, set(), set()
    while pending:
        req = Requirement(pending.popleft())
        name = canonicalize_name(req.name)
        state = (name, tuple(sorted(req.extras)))
        if state in seen:
            continue
        seen.add(state)
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            missing.add(name)
            continue
        selected[name] = dist
        for raw in dist.requires or []:
            child = Requirement(raw)
            if child.marker is None or any(child.marker.evaluate({'extra': extra})
                                           for extra in ['', *req.extras]):
                pending.append(str(child))
    files, byte_count = 0, 0
    for name, dist in sorted(selected.items()):
        for rel in dist.files or []:
            source = Path(dist.locate_file(rel))
            try:
                relative = source.relative_to(SITE)
            except ValueError:
                continue
            if '..' in relative.parts or source.name == 'direct_url.json':
                continue
            if '__pycache__' in relative.parts or not source.is_file():
                continue
            target = PYTHON / 'lib/python3.11/site-packages' / relative
            if not target.exists():
                copy_file(source, target)
                files += 1
                byte_count += source.stat().st_size
    copy_package_directories(selected)
    package_files = [p for p in (PYTHON / 'lib/python3.11/site-packages').rglob('*') if p.is_file()]
    files = len(package_files)
    byte_count = sum(p.stat().st_size for p in package_files)
    # Keep the actual interpreter, Python.h, extension modules and Conda shared libraries.
    stdlib = PREFIX / 'lib/python3.11'
    for source in stdlib.iterdir():
        if source.name in ['site-packages', '__pycache__']:
            continue
        target = PYTHON / 'lib/python3.11' / source.name
        if source.is_dir():
            copy_tree(source, target)
        elif source.is_file():
            copy_file(source, target)
    copy_file(PREFIX / 'bin/python3.11', PYTHON / 'bin/python3.11')
    (PYTHON / 'bin/python').symlink_to('python3.11')
    (PYTHON / 'bin/python3').symlink_to('python3.11')
    copy_tree(PREFIX / 'include/python3.11', PYTHON / 'include/python3.11')
    for source in (PREFIX / 'lib').iterdir():
        if source.is_file() and '.so' in source.name:
            copy_file(source, PYTHON / 'lib' / source.name)
    for name in ['ninja', 'ffmpeg', 'ffprobe']:
        source = shutil.which(name, path=f'{PREFIX}/bin:/mnt/disk0/zzt:/usr/bin:/bin')
        if source:
            copy_file(source, ROOT / 'usr/local/bin' / name)
    # Preserve CUDA JIT compiler and headers, without Nsight GUI/profiling tools.
    cuda = Path('/usr/local/cuda').resolve()
    for name in ['bin', 'nvvm', 'targets/x86_64-linux']:
        copy_tree(cuda / name, ROOT / 'opt/cuda' / name)
    for name, target in [('lib64', 'targets/x86_64-linux/lib'),
                         ('include', 'targets/x86_64-linux/include')]:
        (ROOT / 'opt/cuda' / name).symlink_to(target)
    for name in ['version.json', 'EULA.txt']:
        copy_file(cuda / name, ROOT / 'opt/cuda' / name)
    copy_tree(PROJECT / 'sglang-source', ROOT / 'opt/h3/sglang-source')
    copy_tree(PROJECT / 'deps', ROOT / 'opt/h3/deps')
    for name in ['entry.py', 'request_defaults.py']:
        copy_file(DEPLOY / name, ROOT / 'opt/h3/app' / name)
    packages = {name: dist.version for name, dist in sorted(selected.items())}
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_service': 'GPU052:zzt-h3-g52-a.service',
        'source_project': str(PROJECT), 'source_python': str(PREFIX),
        'python_version': sys.version.split()[0], 'packages': packages,
        'uninstalled_declared_dependencies': sorted(missing),
        'package_file_count': files, 'package_bytes': byte_count,
        'source_overlay': 'Current live sglang-source and deps/peft, not vanilla upstream 0.5.17',
        'model_weights_included': False,
        'lora': 'minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors',
        'defaults': {'short_edge': 544, 'num_inference_steps': 9,
                     'flow_shift': 12.0, 'audio_flow_shift': 3.0, 'seconds': 5},
    }
    (DEST / 'snapshot.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    copy_file(DEST / 'snapshot.json', ROOT / 'opt/h3/snapshot.json')
    print(json.dumps({'packages': len(packages), 'package_files': files,
                      'package_gb': round(byte_count/1e9, 2),
                      'missing_in_source': sorted(missing)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
