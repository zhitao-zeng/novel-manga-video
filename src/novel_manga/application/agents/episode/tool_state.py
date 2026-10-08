"""Write the existing host-operation execution record."""
import datetime
import json
import os
from novel_manga.util import atomic_write_json
from novel_manga.application.production import episode_state


def record_state(ctx, phase, **details):
    record = {"phase": phase, "pid": os.getpid(),
              "at": datetime.datetime.now().isoformat(),
              "episode": str(ctx.episode_dir), **details}
    atomic_write_json(ctx.run_dir / "execution.json", record)
    episode_state.record(ctx.run_dir, 'operation', **record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
