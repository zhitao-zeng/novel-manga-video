"""status_server responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler
import json
import time
import dashboard_resources_thin as dashboard_resources
import dashboard_service_thin as dashboard_service
import dashboard_ui_thin as dashboard_ui

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.path.startswith('/runtime.json'):
            body = json.dumps({'now':time.strftime('%F %T'), 'processes':dashboard_resources._processes(), 'inflight':dashboard_resources._inflight()}, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path.startswith("/status.json"):
            body = json.dumps(dashboard_service.cached_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path.startswith("/board.json"):
            body = json.dumps(dashboard_service.board_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = dashboard_ui.PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif self.path == "/board":
            body = dashboard_ui.PAGE_BOARD.encode("utf-8")
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


if __name__ == "__main__":
    import sys
    from http.server import ThreadingHTTPServer
    import dashboard_config_thin as config
    from pipeline_dashboard import start_monitor
    port = int(sys.argv[1]) if len(sys.argv) > 1 else config.PORT
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    start_monitor(config.ROOT, [n['id'] for n in config.NOVELS])
    server.serve_forever()
