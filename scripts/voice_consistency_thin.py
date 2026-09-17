"""Does a character keep the same voice across episodes? (speaker-embedding version)

Every ASR chunk the runner matched to script lines belonging to exactly one
speaker is turned into a CAM++ speaker embedding.  Then three numbers per
character:

  within  - mean cosine similarity between that character's chunks inside one
            episode (the ceiling: the same generated voice, different lines)
  across  - mean cosine similarity between chunks from different episodes
  others  - mean similarity to other characters' chunks (the floor)

If across is close to within, the voice carries between episodes.  If across
sits near others, the character is re-cast every episode.

    speaker_probe.py outputs/zhutian-fast outputs/zhutian-card"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.voice import main

if __name__ == "__main__":
    raise SystemExit(main())
