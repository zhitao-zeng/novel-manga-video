"""Advance eligible episode records, preserving review -> history -> publication order."""
from __future__ import annotations
import novel_manga.application.repair.manager_state as state
import novel_manga.application.review.store as reviews
import novel_manga.application.repair.history as history
import novel_manga.application.repair.delivery as delivery


def advance(manager):
    scan = state.begin_scan(manager)
    for n, directory in state.eligible_episodes(manager, scan):
        try:
            observed = state.read_episode(manager, scan, n, directory)
            if n not in scan.busy:
                reviews.write_reconciled(directory, observed.previous, observed.review)
                history.observe(directory, observed.review, observed.takes)
                delivery.publish_if_ready(directory, observed.review, observed.takes)
            # Publication and history can affect readiness: read it after those
            # updates, at the same point as the original per-episode loop.
            state.inspect_episode(manager, scan, observed)
        except (OSError, ValueError, KeyError):
            state.mark_unreadable(scan, n)
    snapshot = state.finish_scan(manager, scan)
    state.install_snapshot(manager, snapshot)
    return snapshot
