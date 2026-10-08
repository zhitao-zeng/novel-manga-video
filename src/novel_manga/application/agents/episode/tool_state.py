"""Write the existing host-operation execution record."""
import datetime
import json
import os
from novel_manga.util import atomic_write_json


def record_state(ctx, phase, **details):
    record = {"phase": phase, "pid": os.getpid(),
              "at": datetime.datetime.now().isoformat(),
              "episode": str(ctx.episode_dir), **details}
    atomic_write_json(ctx.run_dir / "execution.json", record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
