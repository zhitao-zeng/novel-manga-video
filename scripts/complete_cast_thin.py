"""Put back the characters a storyboard's own shot descriptions name but its casts left out.

    complete_cast_thin.py --novel-dir outputs/X [--chapters 12,48-60] [--apply] [--no-pack] [--repack]

plan_chapter_thin now completes a shot's `characters` from its description at planning time (2026-09-13,
雾月 761: 薇奥拉 kissed 莱恩 in the description, the cast said 莱恩 and the cat, so the cat kissed him).
Storyboards planned before that carry the gap.  This adds the missing names to chapter_script.json
(backup chapter_script.json.bak-cast, once) and rebuilds only the clips whose cast changed.

The clip boundaries stay where they are: the packer cuts on cast changes, so re-packing the completed
storyboard would move the cuts in a quarter of the episodes (星海: 115 of 448), and a moved cut means the
whole episode renders again and every review of it is void. Recorded stage parts recover each clip's own
dialogue range, and each clip's entry is rebuilt in place with the plan's original tier. Only changed
casts, dialogue ranges or references carry a new request; the others keep their prompt and
the lane's English prompt.  An episode whose old cuts cannot be reproduced (a plan from another packer
version) is left alone and counted.  --repack asks for a full re-pack instead (new cuts, whole episode).
Without --apply it only counts; --no-pack rewrites the storyboard but leaves the clip plan alone.
--rebuild-existing also checks plans whose storyboard was already completed by an earlier run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.cast_completion import main

if __name__ == "__main__":
    raise SystemExit(main())
