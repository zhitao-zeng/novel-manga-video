"""Build each character's reference voice from the episodes already rendered.

    build_voices_thin.py --novel-dir outputs/X [--min-seconds 8] [--target-seconds 14] [--only 沈玄川,苏清月]

Seedance clones a voice from a reference_audio, and the reference has to be real
speech of some length: two seconds of "嗯" does nothing, twelve seconds of lines
puts the generated voice past the same-speaker line (see
docs/seedance-reference-audio.md).  This gathers, for every character, the ASR
chunks the runner aligned to that character's lines alone, longest first, and
concatenates them until the target length, into

    outputs/<novel>/series_assets/voices/<name>.wav   (16 kHz mono)
    outputs/<novel>/series_assets/voices/voices.json  (seconds, sources)

A character below --min-seconds gets no file: a weak reference is worse than
none.  Re-running only rebuilds characters whose material grew; pass --rebuild to
redo everything.  The renderer attaches these automatically."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.voices import main

if __name__ == "__main__":
    raise SystemExit(main())
