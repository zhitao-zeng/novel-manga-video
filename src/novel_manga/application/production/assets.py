"""production_assets_thin responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import threading
import novel_manga.application.production.common as production_common

class CardFactory:
    """Builds asset cards per asset, many at a time, ahead of the episodes that need them.

    Cards used to be built inside each episode's prepare step, one image after
    another and one episode at a time.  Now every referenced (or freshly added)
    asset is a job in a pool; a job runs build_cards_thin.py, which takes a
    per-asset file lock, builds the card(s), judges them and applies the one
    bounded fix.  Episodes wait only for the assets they reference.
    """

    def __init__(self, batch: "Batch", workers: int):
        self.batch = batch
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers))
        self.jobs: dict[str, "Future"] = {}
        self.results: dict[str, dict] = {}
        self.lock = threading.Lock()

    def _job(self, asset_id: str) -> dict:
        directory = self.batch.novel_dir / "series_assets" / ".factory"
        directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(production_common.SCRIPTS / "build_cards_thin.py"), "--novel-dir", str(self.batch.novel_dir), "--assets", asset_id]
        if self.batch.reviewing or self.batch.args.card_review:
            # A card is judged as soon as it is built and fixed once if it is
            # wrong, before any clip that references it is generated: a bad
            # card would otherwise be copied into every episode that uses it.
            command.append("--review")
        if self.batch.args.tier:
            command += ["--tier", self.batch.args.tier]
        log_path = directory / f"{asset_id}.log"
        self.batch.run(command, log_path)
        row = {"asset_id": asset_id, "status": "unknown", "flags": []}
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("{") and '"asset_id"' in line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    pass
        with self.lock:
            self.results[asset_id] = row
        production_common.log(f"card {asset_id}: {row.get('status')} {row.get('seconds', '')}s {'FLAG ' + '; '.join(row['flags'])[:120] if row.get('flags') else ''}")
        return row

    def want(self, asset_ids) -> None:
        with self.lock:
            for asset_id in sorted(set(asset_ids)):
                if asset_id not in self.jobs:
                    self.jobs[asset_id] = self.pool.submit(self._job, asset_id)

    def wait(self, asset_ids) -> list[dict]:
        rows = []
        for asset_id in sorted(set(asset_ids)):
            job = self.jobs.get(asset_id)
            if job is None:
                self.want([asset_id])
                job = self.jobs[asset_id]
            try:
                rows.append(job.result())
            except Exception as error:  # noqa: BLE001
                rows.append({"asset_id": asset_id, "status": f"error: {type(error).__name__}", "flags": []})
        return rows

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
