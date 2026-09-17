"""Inline packaged scripts for the existing isolated JavaScript VM tests."""
import re
from urllib.parse import urlsplit
from novel_manga.dashboard.pages import render_page, static_resource


def inline_page(view='status'):
    from novel_manga.application.dashboard.config import UI_VERSION
    page = render_page(view, UI_VERSION)
    def replace(match):
        name = urlsplit(match[1]).path.removeprefix('/static/')
        return '<script>' + static_resource(name)[0].decode() + '</script>'
    return re.sub(r'<script src="([^"]+)"></script>', replace, page)
