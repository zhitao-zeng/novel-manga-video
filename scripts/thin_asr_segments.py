"""Recognise a list of speech chunks with one SenseVoice model load (ASR venv)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.rendering.asr_segments import main

if __name__ == "__main__":
    raise SystemExit(main())
