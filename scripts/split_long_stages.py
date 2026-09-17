"""Split the over-long stages of clip plans packed before the packer did it, leaving every other clip as it is.

Until 2026-09-11 the packer cut only between stages, so a stage longer than a clip became one clip whose request
was clamped to the cap: 星海 484 clip_05, 71 s of lines asked of a 15 s clip, 45 % of them never spoken.  Packing
such a chapter again from its script would also re-word every other clip - the packer's prompts have changed since
- and pay for all of them again.  This replaces only the clamped clips, each by the parts build_clip_plan_thin now
cuts it into (split_long_shot), keeps every other clip entry as it is, numbers the clips again in order, and moves
the rendered clips, director corrections and overrides to their new ids, so the next render generates only the new
parts.  A split clip's old video goes to work/clips_before_split/, its correction (it described the clamped clip)
to split_long_stages.json.

    split_long_stages.py outputs/<novel> [--chapters 1-50,60] [--margin 5] [--tier fast] [--apply]
    split_long_stages.py outputs/<novel> --rebuild-parts [--apply]

Without --apply it reports what it would change.  An episode another process is rendering is skipped.  The parts
are packed for --tier (fast, as the conductor plans): the first run on 2026-09-11 took profile.json's tier, which
for 星海 and 雾月 is quality, so their parts asked for expression cards the fast tier never builds and every render
stopped at "reference image missing"; --rebuild-parts builds the parts of episodes split earlier again."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.split import main

if __name__ == "__main__":
    raise SystemExit(main())
