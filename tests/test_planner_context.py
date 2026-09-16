import ast
import copy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.budget import configure_budget, budget_requirements
from novel_manga.planning.cast import mentioned_characters
from novel_manga.planning.prompts import render_brief
from planner_context_thin import load_entity_index
import plan_chapter_thin as entry


def test_independent_lane_budgets_and_book_aliases(tmp_path, monkeypatch):
    books = [tmp_path / 'first', tmp_path / 'second']
    contexts = []
    for cap, book, name in zip([15, 30], books, ['甲天河', '乙青阳']):
        book.mkdir();(book / 'bible_aliases.json').write_text(json.dumps({'同一个简称':name}))
        monkeypatch.setenv('NOVEL_CLIP_SECONDS_MAX', str(cap))
        ctx = PlannerContext.from_env();load_entity_index(book,ctx=ctx)
        configure_budget(3000,fast=False,ctx=ctx);contexts.append(ctx)
    before = copy.deepcopy(contexts)
    def render(ctx):
        return render_brief(ctx.system_prompt,ctx=ctx),budget_requirements(ctx=ctx),mentioned_characters('同一个简称走进来',['甲天河','乙青阳'],ctx=ctx)
    expected = [render(ctx) for ctx in contexts]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(render,contexts*5)) == expected*5
    assert expected[0][1]['clip_seconds']=='10-15' and expected[1][1]['clip_seconds']=='20-30'
    assert expected[0][2]==['甲天河'] and expected[1][2]==['乙青阳']
    # Derived form caches may be filled; user configuration and both aliases stay independent.
    for a,b in zip(contexts,before):
        assert a.aliases==b.aliases and a.system_prompt==b.system_prompt


def test_cli_reused_context_resets_absent_book_settings(tmp_path, monkeypatch):
    ctx = PlannerContext.from_env()
    source = tmp_path/'source.txt';source.write_text('第一章 开场\n'+'甲方站在院中。\n'*40)
    bible = tmp_path/'bible.json';bible.write_text(json.dumps({'novel_title':'测试','genre':'generic','visual_style':'v','palette':'p','style_fingerprint':'f','characters':[{'name':'甲方','role':'主角','appearance':'青年','wardrobe':'白衣'}],'locations':['院中']}))
    first=tmp_path/'out/first';first.mkdir(parents=True)
    (first/'chat_screen.json').write_text('{"self_name":"甲方","render":"video"}')
    (first/'profile.json').write_text('{"genre":"fantasy"}')
    for name in ['first','second']:
        monkeypatch.setattr('sys.argv',['plan_chapter_thin.py',str(source),'--novel-id',name,'--bible',str(bible),'--output-root',str(tmp_path/'out'),'--dry-run'])
        assert entry.main(context=ctx)==0
        if name=='first':assert ctx.chat_self=='甲方' and not ctx.chat_card_mode
    assert ctx.chat_self=='' and ctx.chat_card_mode
    assert ctx.aliases=={}


def test_planning_business_modules_do_not_import_scripts_or_each_other_cyclically():
    directory=Path(__file__).resolve().parents[1]/'src/novel_manga/planning'
    graph={}
    for path in directory.glob('*.py'):
        deps=set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Import):modules=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):modules=[node.module or '']
            else:continue
            assert not any(m.startswith('scripts.') or m.endswith('_thin') for m in modules),path
            deps.update(m.rsplit('.',1)[-1] for m in modules if m.startswith('novel_manga.planning.'))
        graph[path.stem]=deps
    def walk(name,trail):
        assert name not in trail,trail+[name]
        for dep in graph.get(name,[]):walk(dep,trail+[name])
    for name in graph:walk(name,[])
