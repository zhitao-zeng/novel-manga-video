"""How the book actually refers to each character: the index every name lookup resolves through.

    build_entity_index.py --novel-dir outputs/X [--apply]

The bible names 薇奥拉公主 once; the chapters say 薇奥拉.  Until 2026-09-13 every stage matched names its own
way - the planner's candidate list, the packer's "who is in the picture", the reviewer's "who was named" -
and each missed the people the prose calls by a short form, which is how 薇奥拉's kiss was given to the cat.
This walks the book (the episodes' segments.json) and records, per bible character, the surface forms that
really occur - the full name, the aliases from bible_aliases.json, the given name before a ·surname, the name
without its title, 小+name - keeping a short form only when it belongs to that one character and sits inside
no other character's name.  With it come the mention count, the first and last chapter, a tier (lead / major
/ minor / extra: the role field is prose in this bible, so the count decides), a generic flag for role nouns
(醉汉, 酒保, 国王) that are as often a common noun as a person, and the card id from the manifest.  Written to
<novel>/entity_index.json; plan_chapter_thin, build_clip_plan_thin and the reviewer read it when present.
Without --apply it only prints."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.identity.index import main

if __name__ == "__main__":
    raise SystemExit(main())
