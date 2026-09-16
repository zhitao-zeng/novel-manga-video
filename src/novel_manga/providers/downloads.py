"""Download existing paid artifacts, retrying only retrieval with original limits."""
from __future__ import annotations

import os
import time
from pathlib import Path
import httpx

def download_file(client, url: str, output: Path, max_bytes: int = 512 * 1024 * 1024) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    # The CDN sometimes drops a stream part-way through ("peer closed
    # connection without sending complete message body").  The artifact is
    # already generated and paid for, so read it again a few times before
    # giving the failure to the caller.
    for attempt in range(1, 4):
        try:
            with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                total = 0
                with partial.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"remote artifact exceeds {max_bytes} bytes")
                        stream.write(chunk)
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError):
            partial.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(3 * attempt)
    os.replace(partial, output)


