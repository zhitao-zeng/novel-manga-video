"""Task submission safety, recorded uncertainty and result envelopes."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
import httpx
from ..util import atomic_write_json

SUBMIT_TIMEOUT_SECONDS = 120.0  # a task submission answers in seconds; downloads keep the long request timeout
RESUBMIT_ENV = "NOVEL_RESUBMIT_UNCONFIRMED"  # thin_batch --resubmit-unconfirmed: its start time, once someone has checked the bill
PROCESS_STARTED = time.time()


def resubmit_before() -> float:
    """Submissions left unconfirmed before this moment may go out again; 0 when none may.  thin_batch passes the time
    its run started, so what someone checked is released and a submission that goes unconfirmed during that same run
    is held like any other - the flag used to release those too, unchecked.  "1", set by hand, means before this
    process started."""
    value = os.environ.get(RESUBMIT_ENV, "").strip()
    if not value:
        return 0.0
    if value == "1":
        return PROCESS_STARTED
    try:
        return float(value)
    except ValueError:
        return 0.0


def unconfirmed(record: dict) -> bool:
    """A submission recorded as unconfirmed and not yet allowed to go out again."""
    at = record.get("submit_uncertain_at")
    return bool(at) and not record.get("task_id") and float(at) >= resubmit_before()


def held_message(record: dict) -> str:
    return (f"this request's submission at {time.strftime('%m-%d %H:%M', time.localtime(float(record['submit_uncertain_at'])))} went "
            "unconfirmed and may have created a paid task: not sent again until someone checks the bill and reruns with "
            "--resubmit-unconfirmed")


class SubmissionUncertain(RuntimeError):
    """A task submission whose answer was lost after the request went out.  The service may have created the
    task, and billed it, so the request is not sent again automatically."""


# A gateway error that can follow a request the service took (bad gateway, gateway timeout, an origin error behind a
# CDN) may come back for a task that was created - and billed - so it is not sent again.  429 and 503 turn a request
# away before anything is made: those go out again after a wait.
UNCERTAIN_STATUSES = {500, 502, 504, 520, 521, 522, 523, 524}
RETRYABLE_STATUSES = {429, 503}
PURGED_STATUSES = {403, 404, 410}  # a finished task's result the service no longer has
STATUS_IN_MESSAGE = re.compile(r"\bHTTP (\d{3})\b")


def http_status(error: Exception) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    match = STATUS_IN_MESSAGE.search(str(error))
    return int(match.group(1)) if match else None


def submit_once(submit, attempts: int = 3, base_delay: float = 1.0) -> httpx.Response:
    """Send a task-creating request, retrying only when no task can have been created: the connection was never
    made, or the service turned it away with 429 or 503.  retry() resent on any error, so a submission the service
    had accepted but whose answer was lost was created - and paid for - twice.  A lost answer (read timeout, dropped
    connection) or a gateway error that can follow an accepted request (500, 502, 504, 52x) raises
    SubmissionUncertain instead: a 502 used to go back to the runner, whose throttle backoff sent it up to seven more
    times.  Any other refusal is a RuntimeError, as the callers expect - an image submission's HTTPStatusError used
    to get past every retry loop and take the whole render down."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return submit()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            last = error
        except httpx.TransportError as error:
            raise SubmissionUncertain(f"task submission unconfirmed ({type(error).__name__}): not sent again, the service may have accepted it") from error
        except (RuntimeError, httpx.HTTPStatusError) as error:
            status = http_status(error)
            if status in UNCERTAIN_STATUSES:
                raise SubmissionUncertain(f"task submission unconfirmed (HTTP {status}): not sent again, the service may have accepted it") from error
            if status not in RETRYABLE_STATUSES:
                if isinstance(error, httpx.HTTPStatusError):
                    raise RuntimeError(f"task submission returned HTTP {status}: {error.response.text.strip()[:300]}") from error
                raise
            last = error
        if attempt + 1 < attempts:
            time.sleep(base_delay * (2 ** attempt))
    assert last is not None
    if isinstance(last, httpx.HTTPStatusError):
        raise RuntimeError(f"task submission returned HTTP {http_status(last)} after {attempts} tries: {last.response.text.strip()[:300]}") from last
    raise last


def task_data(payload: dict) -> dict:
    result = payload.get("Result") or payload.get("data") or payload
    return result if isinstance(result, dict) else payload


def submit_recorded(submit, task_path: Path, record: dict) -> str | None:
    """submit_once; an unconfirmed submission is written beside the output, so it is not sent again unasked."""
    try:
        return submit_once(submit).json().get("task_id")
    except SubmissionUncertain:
        atomic_write_json(task_path, {**record, "submit_uncertain_at": time.time()})
        raise


