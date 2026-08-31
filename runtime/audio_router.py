#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class AudioRouter:
    def __init__(self, manifest: Path, tts_port: int, evidence_port: int) -> None:
        self.manifest = manifest
        self.tts_port = tts_port
        self.evidence_port = evidence_port
        self.processes: list[subprocess.Popen[bytes]] = []

    @staticmethod
    def _worker_command(python: str, stage: str, port: int, manifest: Path) -> list[str]:
        return [
            python,
            "/app/runtime/model_worker.py",
            "--stage",
            stage,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--manifest",
            str(manifest),
        ]

    def start(self) -> None:
        commands = (
            self._worker_command(
                "/opt/venvs/indextts/bin/python",
                "audio-tts",
                self.tts_port,
                self.manifest,
            ),
            self._worker_command(
                "/opt/venvs/audio/bin/python",
                "audio-evidence",
                self.evidence_port,
                self.manifest,
            ),
        )
        env = os.environ.copy()
        env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONPATH": "/app/src:/opt/index-tts",
            }
        )
        for command in commands:
            self.processes.append(
                subprocess.Popen(command, env=env, start_new_session=True)
            )

    def stop(self) -> None:
        processes, self.processes = self.processes, []
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)

    @staticmethod
    def _get(url: str, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
        try:
            with DIRECT_HTTP.open(url, timeout=timeout) as response:
                return response.status, json.loads(response.read())
        except (OSError, urllib.error.URLError, ValueError) as error:
            return 503, {"ready": False, "error": f"{type(error).__name__}: {error}"}

    @staticmethod
    def _post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with DIRECT_HTTP.open(
                request,
                timeout=float(os.getenv("NOVEL_LOCAL_MODEL_REQUEST_TIMEOUT", "7200")),
            ) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            try:
                return error.code, json.loads(body)
            except ValueError:
                return error.code, {"success": False, "error": body[:2000]}
        except (OSError, urllib.error.URLError, ValueError) as error:
            return 503, {"success": False, "error": f"{type(error).__name__}: {error}"}

    def ready(self) -> tuple[int, dict[str, Any]]:
        children = []
        for name, port, process in zip(
            ("tts", "evidence"),
            (self.tts_port, self.evidence_port),
            self.processes,
            strict=True,
        ):
            if process.poll() is not None:
                children.append(
                    {"name": name, "ready": False, "exit_code": process.returncode}
                )
                continue
            status, payload = self._get(f"http://127.0.0.1:{port}/ready")
            children.append(
                {"name": name, "ready": status == 200 and bool(payload.get("ready"))}
            )
        ready = len(children) == 2 and all(item["ready"] for item in children)
        return (200 if ready else 503), {"ready": ready, "children": children}

    def invoke(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        operation = str(request.get("operation") or "")
        if operation == "tts":
            port = self.tts_port
        elif operation in {"asr", "align"}:
            port = self.evidence_port
        else:
            return 400, {"success": False, "error": f"unsupported audio operation: {operation}"}
        return self._post(f"http://127.0.0.1:{port}/invoke", request)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--tts-port", type=int, default=18101)
    parser.add_argument("--evidence-port", type=int, default=18102)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    router = AudioRouter(args.manifest, args.tts_port, args.evidence_port)
    router.start()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path != "/ready":
                self._send(404, {"error": "not found"})
                return
            self._send(*router.ready())

        def do_POST(self) -> None:
            if self.path != "/invoke":
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                self._send(*router.invoke(payload))
            except Exception as error:
                self._send(500, {"success": False, "error": f"{type(error).__name__}: {error}"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((args.host, args.port), Handler)

    def watch_children() -> None:
        while router.processes:
            if any(process.poll() is not None for process in router.processes):
                server.shutdown()
                return
            time.sleep(0.5)

    threading.Thread(target=watch_children, daemon=True).start()

    def shutdown(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        router.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
