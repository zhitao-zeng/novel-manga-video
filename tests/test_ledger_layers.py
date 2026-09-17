import ast
import json
import runpy
import sys
from pathlib import Path
import pytest
from novel_manga.entities import evidence
import novel_manga.application.identity.ledger_store as store
import novel_manga.application.identity.ledger_resolution as resolution


def test_ledger_command_decisions_use_separate_resolver(tmp_path, monkeypatch):
    root = tmp_path/'book';root.mkdir()
    (root/'story_bible.json').write_text('{"characters":[]}')
    calls=[]
    monkeypatch.setattr(resolution,'decide',lambda ledger,claim,status,note:calls.append((ledger.novel_dir,claim,status,note)) or True)
    monkeypatch.setattr(sys,'argv',['entity_ledger_thin.py','accept','--novel-dir',str(root),'--claim','c1','--note','source confirmed'])
    command=Path(__file__).resolve().parents[1]/'scripts/entity_ledger_thin.py'
    with pytest.raises(SystemExit) as result:runpy.run_path(str(command),run_name='__main__')
    assert result.value.code==0 and calls==[(root,'c1','accepted','source confirmed')]


def test_store_never_imports_judges_or_identity_decision_flow():
    root=Path(__file__).resolve().parents[1]
    for path in [root/'src/novel_manga/application/identity/ledger_store.py', *sorted((root/'src/novel_manga/entities').glob('*.py'))]:
        for node in ast.walk(ast.parse(path.read_text())):
            names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module] if isinstance(node,ast.ImportFrom) else []
            assert not set(names)&{'novel_manga.application.identity.ledger_judges','novel_manga.application.identity.ledger_resolution','novel_manga.application.identity.ledger_flow','novel_manga.application.review.second_pass'},path
    # The repository presents storage and lookups, never a hidden model operation.
    assert not hasattr(store.Ledger,'extract') and not hasattr(store.Ledger,'resolve_chapter')
