"""A whole-book reading must not assign a later actor's object to an earlier scene."""
import json
import sys

from novel_manga.application.planning import cli
from novel_manga.application.identity import flow as identity
from novel_manga.planning.context import PlannerContext


def test_dry_run_offers_only_props_known_by_the_episode_without_renumbering_the_bible(tmp_path, monkeypatch):
    source = tmp_path / 'source.md'
    source.write_text('第一章 夜行\n' + '主角穿着褐色披风，夹着黑布卷走出校门，皇家守卫身披黑色短披风。\n' * 30
                      + '第二章 会面\n' + '另一角色身披黑色短披风，主角提着黑布卷走近。\n' * 30)
    book = tmp_path / 'outputs/book'
    book.mkdir(parents=True)
    bible = {'novel_title': '测试', 'genre': '西方奇幻', 'visual_style': '三维动画',
             'palette': '棕色', 'style_fingerprint': 'test',
             'characters': [{'name': '主角', 'role': '主角', 'appearance': '黑发青年', 'wardrobe': '褐色披风'}],
             'locations': ['校门：石砌空场'],
             'props': [{'name': '黑布卷', 'category': '工具', 'appearance': '黑色布卷', 'owner': '主角'},
                       {'name': '黑色短披风', 'category': '其他', 'appearance': '黑色披风', 'owner': '另一角色', 'wearable': True}]}
    path = book / 'story_bible.json'
    path.write_text(json.dumps(bible, ensure_ascii=False))
    (book / 'bible_growth.json').write_text(json.dumps({'1': {'props': ['黑布卷']},
                                                       '2': {'props': ['黑色短披风']}}))
    monkeypatch.setattr(identity, 'resolve_chapter', lambda *a, **k: {})
    for chapter, expected in [(1, ['黑布卷']), (2, ['黑布卷', '黑色短披风'])]:
        monkeypatch.setattr(sys, 'argv', ['plan_chapter_thin.py', str(source), '--novel-id', 'book',
            '--title', '测试', '--bible', str(path), '--output-root', str(book.parent),
            '--episode-index', str(chapter), '--dry-run'])
        assert cli.main(context=PlannerContext.from_env()) == 0
        request = json.loads((book / f'book_{chapter}/request_dry_run.json').read_text())
        assert [p['name'] for p in request['story_bible']['props']] == expected
    assert json.loads(path.read_text())['props'] == bible['props']
