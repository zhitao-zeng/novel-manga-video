"""conductor_common_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
from pathlib import Path
import os


def load_dotenv(path: Path) -> None:
    """Read .env into the environment, as thin_batch.py does.  The conductor passes os.environ to every
    worker it spawns, and build_cards_thin.py has no reader of its own: without this, a conductor started
    from a shell that never sourced .env gives its card workers no PHANROUTER_API_KEY and they exit at
    once (2026-09-12: 514 cards queued, 0 built)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


REPO = project_root()


PY = str(REPO / ".venv" / "bin" / "python")


SCRIPTS = REPO / "scripts"


BASE_ENV = {"PYTHONPATH": "src:scripts", "NOVEL_PLANNER_BACKEND": "deterministic",
            "NOVEL_CREATIVE_PROFILE": "short-drama-adaptive-v1"}


PLAN_BLOCK_RUNS = 3  # runs a planning block gets while some of its chapters are left without a plan


PLAN_RETRY_SECONDS = 600  # ...spaced out, so a planning-server outage does not burn them in three ticks


REVIEW_ERROR_ROUNDS = 3  # reviews an episode gets while the judge keeps failing on some of its clips


REVIEW_BATCH_TRIES = 3  # review batches a final is put in before the conductor stops waiting for its review
