"""Write each clip a second prompt, in the shape MiniMax H3 was trained to read.

H3 decides what to speak by language.  Every example it was trained on puts the description in
English and the spoken words in <d>[Chinese] ...</d>, so Chinese text means "say this".  Our
prompt is Chinese from top to bottom, which marks two thousand characters of stage direction
as dialogue - and that is exactly what came back: the model recited 决定以灰头鹰身份保护公主
and dropped the line it was given.

Measured on three clips, rewriting the description into English and leaving only the lines in
Chinese took CER from 5.000 to 0.000, 3.778 to 0.778, and 4.750 to 1.000.

The translation is one local Qwen call per clip, so this is free and runs beside planning.
It writes prompt_h3 into the plan next to prompt, keyed by a digest of the Chinese one: re-pack
a chapter and its English prompt is rebuilt rather than silently kept.

    build_h3_prompts.py <novel> [--novel-dir DIR] [--chapters 1-50,60] [--workers N] [--limit N] [--rebuild]

A local-H3 lane (thin_batch.py) runs this for one episode right before rendering it whenever a clip has
no current English prompt - a new plan, a re-pack - so nothing waits for a pass run by hand.  A
translation that fails, or comes back with a different number of sentences than the clip has shots,
is asked again; after three tries the clip is left without one and the lane waits for it, rather than
render a shot under another shot's description."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.rendering.h3 import main

if __name__ == "__main__":
    raise SystemExit(main())
