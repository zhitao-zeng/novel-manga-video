"""Single-file SenseVoice adapter for NOVEL_ASR_COMMAND."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.rendering.asr_command import main

if __name__ == "__main__":
    raise SystemExit(main())
