"""Standalone commands must import and parse without starting production work."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('entry', [
    'plan_chapter_thin.py','entity_ledger_thin.py','conductor_thin.py','thin_batch.py',
    'manage_repair_thin.py','pipeline.py','render_clips_thin.py','repair_review_thin.py','shared_audit_thin.py',
])
def test_command_help_starts_in_fresh_interpreter(entry):
    root=Path(__file__).resolve().parents[1]
    env={'PATH':os.environ['PATH'],'PYTHONPATH':'src:scripts:experiments','LANG':'C.UTF-8'}
    result=subprocess.run([sys.executable,str(root/'scripts'/entry),'--help'],cwd=root,env=env,
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'usage:' in result.stdout
