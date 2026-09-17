from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit
import re

from novel_manga.application.dashboard.server import Handler
from novel_manga.dashboard.pages import ASSETS, render_page, static_resource


def test_pages_reference_packaged_assets_and_keep_existing_containers():
    for view, container in [('status', 'novels'), ('board', 'board')]:
        page = render_page(view, 'test-version')
        assert '{{' not in page and f'id="{container}"' in page
        assert 'const DASHBOARD_VERSION="test-version"' in page
        for url in re.findall(r'(?:src|href)="(/static/[^"]+)"', page):
            content, content_type = static_resource(urlsplit(url).path.removeprefix('/static/'))
            assert content and ('javascript' in content_type or 'css' in content_type)


def test_http_serves_styles_scripts_and_pages_without_running_a_monitor():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        # Exercise only this local HTTP fixture; never the model services.
        from urllib.request import build_opener, ProxyHandler
        opener = build_opener(ProxyHandler({}))
        for path in ['/', '/board', *('/static/' + name + '?v=test' for name in ASSETS)]:
            with opener.open(f'http://127.0.0.1:{server.server_port}{path}', timeout=5) as response:
                assert response.status == 200 and response.read()
                assert 'charset=utf-8' in response.headers['Content-Type']
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
