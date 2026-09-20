"""Six opt-in local story methods; the existing planner remains the default."""
from .base import StoryMethod
from .shanyin import METHOD as SHANYIN
from .community import METHOD as COMMUNITY
from .drama import METHOD as DRAMA
from .dream import METHOD as DREAM
from .leos import METHOD as LEOS
from .visual import METHOD as VISUAL

METHODS = {m.key: m for m in (SHANYIN, COMMUNITY, DRAMA, DREAM, LEOS, VISUAL)}


def get_method(key: str | None) -> StoryMethod | None:
    if key in (None, "", "default"):
        return None
    if key not in METHODS:
        raise ValueError(f"unknown story method {key!r}; choose default or {', '.join(METHODS)}")
    return METHODS[key]
