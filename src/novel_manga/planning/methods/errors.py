"""Incomplete stage output must not advance to downstream compilation."""

class IncompleteOutlineError(RuntimeError):
    def __init__(self, attempts: list[dict], *, cause=None):
        self.attempts = attempts
        self.cause = cause
        stage = attempts[-1].get('stage', 'first-pass outline')
        super().__init__(stage + " incomplete: " + "; ".join(attempts[-1]["errors"]))
