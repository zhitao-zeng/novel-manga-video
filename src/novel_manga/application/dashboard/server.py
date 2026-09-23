"""status_server responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import json
import re
import time
import novel_manga.application.dashboard.resources as dashboard_resources
import novel_manga.application.dashboard.service as dashboard_service
import novel_manga.application.dashboard.workbench as workbench
from urllib.parse import unquote, urlsplit
from novel_manga.dashboard.pages import render_page, static_resource
from . import config


def _json_body(data) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's interface
        path = unquote(urlsplit(self.path).path)
        if path.startswith('/static/'):
            try:
                body, content_type = static_resource(path.removeprefix('/static/'))
            except KeyError:
                self.send_error(404)
                return
        elif path.startswith('/runtime.json'):
            body = json.dumps({'now':time.strftime('%F %T'), 'processes':dashboard_resources._processes(), 'inflight':dashboard_resources._inflight()}, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif path.startswith("/status.json"):
            body = json.dumps(dashboard_service.cached_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif path.startswith("/board.json"):
            body = json.dumps(dashboard_service.board_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif path.startswith("/api/"):
            try:
                body = self._api(path)
            except KeyError:
                self.send_error(404)
                return
            content_type = "application/json; charset=utf-8"
        elif path.startswith("/media/"):
            parts = path.strip('/').split('/', 2)
            try:
                target = workbench.media_file(config.ROOT, parts[1], parts[2] if len(parts) > 2 else '')
                self._send_file(target, workbench.MEDIA_TYPES[target.suffix.lower()])
            except (KeyError, IndexError):
                self.send_error(404)
            return
        elif path.startswith("/published/"):
            # Published card copies, served to the video provider's asset library through the
            # tunnel.  The provider's pre-flight is a HEAD; do_HEAD below answers it.
            self._published(path, head_only=False)
            return
        elif path.startswith("/thumb/"):
            # /thumb/<book>/<relative path>?w=520: the gallery's downscaled JPEG of an image
            parts = path.strip('/').split('/', 2)
            query = urlsplit(self.path).query
            width = dict(p.split('=', 1) for p in query.split('&') if '=' in p).get('w', 520)
            try:
                self._send_file(workbench.thumbnail(config.ROOT, parts[1], parts[2] if len(parts) > 2 else '',
                                                    int(width)), "image/jpeg")
            except (KeyError, IndexError, ValueError):
                self.send_error(404)
            return
        elif path in ("/", "/index.html"):
            body = render_page('status', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/board":
            body = render_page('board', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/workbench":
            body = render_page('workbench', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/experiments":
            body = render_page('experiments', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/compare":
            body = render_page('compare', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/recent":
            body = render_page('recent', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif re.fullmatch(r"/novel/[A-Za-z0-9._-]+", path):
            body = render_page('novel', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif re.fullmatch(r"/episode/[A-Za-z0-9._-]+/\d+", path):
            body = render_page('episode', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif re.fullmatch(r"/assets/[A-Za-z0-9._-]+", path):
            body = render_page('assets', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif re.fullmatch(r"/health/[A-Za-z0-9._-]+", path):
            body = render_page('health', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif re.fullmatch(r"/bible/[A-Za-z0-9._-]+", path):
            body = render_page('bible', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _api(self, path: str) -> bytes:
        parts = path.strip('/').split('/')
        if parts == ['api', 'workbench']:
            return _json_body(workbench.books(config.ROOT))
        if parts == ['api', 'experiments']:
            return _json_body(workbench.experiments(config.ROOT))
        if parts == ['api', 'compare', 'samples']:
            return _json_body(workbench.samples(config.ROOT))
        if parts == ['api', 'recent']:
            return _json_body(workbench.recent(config.ROOT))
        if len(parts) == 4 and parts[:2] == ['api', 'book'] and parts[3] == 'episodes':
            return _json_body(workbench.episodes(config.ROOT, parts[2]))
        if len(parts) == 5 and parts[:2] == ['api', 'book'] and parts[3] == 'episode':
            return _json_body(workbench.episode(config.ROOT, parts[2], int(parts[4])))
        if len(parts) == 4 and parts[:2] == ['api', 'book'] and parts[3] == 'assets':
            return _json_body(workbench.assets(config.ROOT, parts[2]))
        if len(parts) == 4 and parts[:2] == ['api', 'book'] and parts[3] == 'health':
            return _json_body(workbench.health(config.ROOT, parts[2]))
        if len(parts) == 4 and parts[:2] == ['api', 'book'] and parts[3] == 'bible':
            return _json_body(workbench.bible(config.ROOT, parts[2]))
        raise KeyError(path)

    def _send_file(self, target, content_type: str):
        # A single Range is honored so the <video> element can seek without re-downloading
        # the whole film.
        size = target.stat().st_size
        start, end, status = 0, size - 1, 200
        header = (self.headers.get('Range') or '').strip()
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
                elif match.group(1) == '':
                    start = max(0, size - int(match.group(2) or '0'))
                    end = size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(target, "rb") as media:
            media.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = media.read(min(262144, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _published(self, path: str, *, head_only: bool):
        from novel_manga.media.publish import publish_root
        relative = urlsplit(path).path.removeprefix("/published/")
        parts = Path(relative).parts
        if not parts or Path(relative).is_absolute() or ".." in parts:
            self.send_error(404)
            return
        target = publish_root(config.ROOT / "outputs").joinpath(*parts)
        content_type = workbench.MEDIA_TYPES.get(target.suffix.lower(), "")
        if not content_type.startswith("image/") or not target.is_file():
            self.send_error(404)
            return
        if head_only:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            return
        self._send_file(target, content_type)

    def do_HEAD(self):  # noqa: N802 - http.server's interface; the provider's pre-flight is a HEAD
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/published/"):
            self._published(path, head_only=True)
        else:
            self.send_error(404)

    def log_message(self, *args):  # quiet: this runs for months
        pass


def main():
    import sys
    from http.server import ThreadingHTTPServer
    import novel_manga.application.dashboard.config as config
    from novel_manga.application.dashboard.metrics import start_monitor
    port = int(sys.argv[1]) if len(sys.argv) > 1 else config.PORT
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    start_monitor(config.ROOT, [n['id'] for n in config.NOVELS])
    server.serve_forever()
