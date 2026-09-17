"""entity_ledger_thin responsibilities; existing evidence and identity policy."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.identity.ledger_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
