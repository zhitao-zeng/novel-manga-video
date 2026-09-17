"""status_server responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler
import json
import time
import novel_manga.application.dashboard.resources as dashboard_resources
import novel_manga.application.dashboard.service as dashboard_service
from urllib.parse import urlsplit
from novel_manga.dashboard.pages import render_page, static_resource
from . import config

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.path.startswith('/static/'):
            try:
                body, content_type = static_resource(urlsplit(self.path).path.removeprefix('/static/'))
            except KeyError:
                self.send_error(404)
                return
        elif self.path.startswith('/runtime.json'):
            body = json.dumps({'now':time.strftime('%F %T'), 'processes':dashboard_resources._processes(), 'inflight':dashboard_resources._inflight()}, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path.startswith("/status.json"):
            body = json.dumps(dashboard_service.cached_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path.startswith("/board.json"):
            body = json.dumps(dashboard_service.board_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = render_page('status', config.UI_VERSION).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif self.path == "/board":
            body = render_page('board', config.UI_VERSION).encode("utf-8")
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
