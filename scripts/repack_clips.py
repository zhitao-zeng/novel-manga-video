"""Re-pack planned episodes to a different clip length, without planning anything again.

An episode's expensive part - reading the chapter, writing the shots and the dialogue - lands
in chapter_script.json and never mentions a clip length.  Clip length only enters when those
shots are packed into clips, which build_clip_plan_thin.py does locally from environment:
NOVEL_CLIP_SECONDS_MAX=15 caps a clip at 15 s and a clip at three stages instead of six.

So converting a 30 s novel to 15 s costs no model calls at all.  It re-runs the packer.

    repack_clips.py <novel> [--seconds 15] [--apply] [--include-rendered] [--workers N]

Rendered episodes are left alone by default: re-packing renumbers the clips and rewrites the
prompts, so every clip already paid for would be orphaned and the episode would re-render from
nothing.  The old plan is kept beside the new one as clip_plan.<n>s.json."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.repack import main

if __name__ == "__main__":
    raise SystemExit(main())
