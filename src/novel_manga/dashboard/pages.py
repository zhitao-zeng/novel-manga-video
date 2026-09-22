"""Render the dashboard pages and load their packaged assets."""
from importlib.resources import files
import json
from html import escape

ASSETS = {'style.css': 'text/css; charset=utf-8', 'pipeline.css': 'text/css; charset=utf-8',
          'workbench.css': 'text/css; charset=utf-8',
          **{name + '.js': 'text/javascript; charset=utf-8'
             for name in ('common', 'pipeline', 'tooltips', 'status', 'board', 'workbench',
                          'compare', 'recent', 'health', 'bible')}}

VIEWS = {'status': '实时', 'board': '看板',
         'workbench': '工作台', 'novel': '工作台', 'episode': '工作台', 'assets': '工作台',
         'health': '工作台', 'bible': '工作台',
         'compare': '对比', 'recent': '动态', 'experiments': '实验'}

NAV_ITEMS = [('实时', '/'), ('看板', '/board'), ('工作台', '/workbench'),
             ('对比', '/compare'), ('动态', '/recent'), ('实验', '/experiments')]


def static_resource(name):
    content_type = ASSETS[name]
    return files('novel_manga.dashboard').joinpath('static', name).read_bytes(), content_type


def render_page(view: str, version: str) -> str:
    active = VIEWS[view]
    templates = files('novel_manga.dashboard').joinpath('templates')
    header = ('<span class="health" id="health"><i class="dot idle"></i>读取中</span><span id="stamp">加载中…</span>'
              if view == 'status' else '<span id="stamp"></span>')
    nav = ''.join(f'<a href="{href}" class="{"on" if title == active else ""}">{title}</a>'
                  for title, href in NAV_ITEMS)
    values = {'NAV': nav, 'HEADER': header, 'BODY': templates.joinpath(view + '.html').read_text(),
              'VERSION_JSON': json.dumps(version), 'VERSION': escape(version, quote=True)}
    result = templates.joinpath('base.html').read_text()
    for key, value in values.items():
        result = result.replace('{{' + key + '}}', value)
    return result
