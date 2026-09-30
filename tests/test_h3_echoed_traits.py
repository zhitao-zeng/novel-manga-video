"""Chinese direction text must be regenerated, not silently stripped from the model's answer."""
import re
from novel_manga.application.rendering import h3 as translator
from novel_manga.story.h3 import CJK


def clip():
    return {'clip_id':'clip_20','request_seconds':5,
            'prompt':'【阶段一】斯塔克站在巴士台阶上。结束时：斯塔克无语。画面呈现',
            'references':[{'role':'character','name':'斯塔克','path':'c.jpeg'}]}


def test_copied_chinese_traits_do_not_get_silently_deleted(monkeypatch):
    c=clip()
    monkeypatch.setattr(translator,'ask_json',lambda *a,**k:{
        'shots':['<Subject 1> (倒三角形，肩宽腰细) stands on the bus steps.'],
        'soundscape':'None.','conflicts':[]})
    assert not translator.convert(c,tries=1)
    assert not c.get('prompt_h3')


def test_regeneration_preserves_the_trait_in_english(monkeypatch):
    c=clip();calls=[]
    answers=iter([
        {'shots':['<Subject 1> (肩宽腰细) stands on the bus steps.'],'soundscape':'None.','conflicts':[]},
        {'shots':['<Subject 1>, broad-shouldered with a narrow waist, stands on the bus steps.'],
         'soundscape':'None.','conflicts':[]}])
    def answer(parts,schema,**kw):
        calls.append(parts[0]['text']);return next(answers)
    monkeypatch.setattr(translator,'ask_json',answer)
    assert translator.convert(c,tries=2)
    assert len(calls)==2 and 'Previous response was rejected' in calls[1]
    assert 'broad-shouldered with a narrow waist' in c['prompt_h3']
    assert not CJK.search(re.sub(r'<d>.*?</d>','',c['prompt_h3'],flags=re.S))
