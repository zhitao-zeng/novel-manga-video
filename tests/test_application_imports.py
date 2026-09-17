"""Importing services must not execute the former operator commands."""
import os
from pathlib import Path
import subprocess
import sys


def test_all_package_modules_import_without_commands_or_model_calls(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''from pathlib import Path
from unittest.mock import patch
import importlib, pkgutil, httpx, novel_manga
with patch.object(Path, 'write_text', side_effect=AssertionError('import writes records')), \\
     patch.object(Path, 'mkdir', side_effect=AssertionError('import creates work')), \\
     patch.object(httpx.Client, 'send', side_effect=AssertionError('import calls a service')):
    for module in pkgutil.walk_packages(novel_manga.__path__, novel_manga.__name__+'.'):
        importlib.import_module(module.name)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            env={'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root / 'src'),
                                 'NOVEL_PROJECT_ROOT': str(root)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
