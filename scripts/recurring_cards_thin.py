"""Cards for named characters who keep coming back without one.

    recurring_cards_thin.py --novel-dir outputs/X [--min-episodes 2] [--build]

Cards are normally built only for the characters an episode puts on screen; a
character who only ever speaks in the group chat, or off screen, is never
referenced and so never gets one - and then the chat card shows a coloured
initial for them and, if they ever do step into frame, the video model invents a
face.  This lists every named character (anonymous 无名 roles excluded) who is
in the bible, appears in at least --min-episodes episodes by any route (on
screen, chat, off screen) and has no card, and with --build renders their
turnaround card through build_cards_thin.py.  thin_batch.py runs the same rule
after each episode is planned."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.recurring import main

if __name__ == "__main__":
    raise SystemExit(main())
