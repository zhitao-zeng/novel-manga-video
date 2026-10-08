"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

from novel_manga.review.endpoints import judge_settings




def bible_settings():
    """The endpoint every question about the cast is asked on: the judge's, whatever process asks.

    These five calls - scan the names, fill a character, scan the places, rule on an unknown name,
    sum up a volume - used to follow the process they ran in.  In the batch parent that was the 27B;
    inside the planner, which applies the Flash-Next preset to its own environment, it was
    Flash-Next.  So one book's cast was built by two models, and which one decided a name depended on
    which side happened to ask first.  Passed explicitly, as the H3 translation passes its own, the
    process environment stops mattering.
    """
    return judge_settings()
