"""The Chinese traits a translator copies after a subject tag never reach H3, which reads Chinese aloud.

美漫 ch12 clip_20 (2026-09-25): "<Subject 2> (倒三角形，肩宽腰细，姿态张扬) stands on the bus steps" - 48 characters
outside the dialogue tags, the same on every retry."""
import re

from novel_manga.application.rendering import h3 as translator
from novel_manga.application.rendering.h3 import strip_echoed_traits
from novel_manga.story.h3 import CJK


def test_only_a_bracket_holding_chinese_after_a_tag_goes():
    assert strip_echoed_traits('<Subject 2> (倒三角形，肩宽腰细) stands') == '<Subject 2> stands'
    assert strip_echoed_traits('<Subject 2>（姿态张扬） waits') == '<Subject 2> waits'
    assert strip_echoed_traits('<Subject 2> (S1) says') == '<Subject 2> (S1) says'
    assert strip_echoed_traits('<Subject 2> (in the armor) waits') == '<Subject 2> (in the armor) waits'


def test_a_translation_that_copied_the_traits_comes_out_english(monkeypatch):
    clip = {'clip_id': 'clip_20', 'request_seconds': 5,
            'prompt': '【阶段一】斯塔克站在巴士台阶上。结束时：斯塔克无语。画面呈现',
            'references': [{'role': 'character', 'name': '斯塔克', 'path': 'c.jpeg'}]}
    monkeypatch.setattr(translator, 'ask_json', lambda parts, schema, **kw: {
        'shots': ['<Subject 1> (倒三角形，肩宽腰细，姿态张扬) stands on the bus steps and says nothing.']})
    monkeypatch.setattr(translator, 'english_delivery', lambda *_: {})
    assert translator.convert(clip)
    assert not CJK.search(re.sub(r'<d>.*?</d>', '', clip['prompt_h3'], flags=re.S))
    assert '<Subject 1> stands on the bus steps' in clip['prompt_h3']
