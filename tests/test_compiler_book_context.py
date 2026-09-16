import json
from concurrent.futures import ThreadPoolExecutor
import build_clip_plan_thin as packer
from novel_manga.models import StoryBible, Character


def test_packer_book_and_saved_lane_settings_are_independent(tmp_path):
    books=[]
    for name,cap,frame in [('first',15,'16:9'),('second',30,'9:16')]:
        novel=tmp_path/name;episode=novel/f'{name}_1';episode.mkdir(parents=True)
        bible=novel/'story_bible.json'
        bible.write_text(StoryBible(novel_title=name,genre='generic',visual_style='v',palette='p',style_fingerprint='f',characters=[Character(name='演员',appearance='青年',wardrobe='白衣')],locations=['庭院']).model_dump_json())
        plan={'policy':'test','limits':{'max_clip_seconds':cap},'totals':{'profile':{'frame':frame,'style':'3d','tier':'fast' if cap==15 else 'quality'}}}
        if name=='first':
            (novel/'chat_screen.json').write_text('{"self_name":"演员","group_name":"第一本群聊"}')
            voices=novel/'series_assets/voices';voices.mkdir(parents=True)
            (voices/'voices.json').write_text('{"演员":{}}');(voices/'演员.wav').write_bytes(b'fixture')
        books.append((episode,bible,plan))
    def load(case):
        option=packer.context_for_plan(*case)['compiler_options']
        return (option.max_clip_seconds,option.max_stages,option.frame['width'],option.chat_screen['self_name'],option.voices,option.two_view_cast_limit)
    expected=[(15.,3,1920,'演员',{'演员':'series_assets/voices/演员.wav'},0),(30.,6,1080,'',{},2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(load,books*6))==expected*6
    assert packer.load_voices(books[1][0].parent)=={}
    assert packer.load_chat_screen(books[1][0].parent)['group_name']==''
