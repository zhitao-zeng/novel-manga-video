"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from contextlib import contextmanager
import os


@contextmanager
def process_environment(values):
    """This sequential command owns its environment until its host jobs have settled."""
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
