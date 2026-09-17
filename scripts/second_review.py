"""Ask what a viewer would notice, in two passes, and combine the answers.

The first review judges each clip against the character cards, so it reports a dragon whose
scales are the wrong white.  This one asks whether the picture itself is wrong - and it took
three failed wordings to get there, each failing the same way: a rule that is true in general
was false for this particular show.

    "画面里多出一个本段没有的人"        - the cast list names who must appear, not everyone
                                          allowed on screen, so every crowd became a fault
    "动物身上长着人的手"                - 星海's dragons are anthropomorphic by design
    "物件浮在空中"                      - in a cultivation story, swords are supposed to fly

So what counts as normal is not written into this file.  It is read from
``outputs/<novel>/review_normal.txt`` and pasted into both prompts.  A new novel gets its own
note; getting that note wrong is the failure mode to watch for.

Two passes, because the model is better at seeing than at judging.  It will describe "一个拥有
三个龙头的类人生物" accurately and then score it 0, since a three-headed dragon sounds like a
legitimate creature.  So the first pass looks at frames and scores, the second reads back the
description it wrote - with no picture to soften it - and rules on that alone.  A clip is
worth money when both agree.

    second_review.py <novel> [--all] [--stage look|read|report|all] [--workers N]
                             [--judge local|flashnext] [--limit N]

Without --all only the clips the first review already doubted are examined.  Every verdict is
written as it arrives and a worker claims a clip by creating its file exclusively, so a crash
costs one clip and a restart resumes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.second_pass import main

if __name__ == "__main__":
    raise SystemExit(main())
