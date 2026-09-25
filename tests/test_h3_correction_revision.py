from novel_manga.application import profiles
from novel_manga.application.rendering import h3


def clip():
    return {'clip_id':'c','kind':'video','request_seconds':10,'cast':['甲'],'lines':[],
            'prompt':'【阶段】甲站在门旁。','references':[{'role':'character','name':'甲','path':'a.jpeg'}]}


def test_new_correction_does_not_reuse_the_old_merged_translation(monkeypatch):
    c=clip();calls=[]
    def translated(parts,schema,**kwargs):
        calls.append(parts[0]['text'])
        return {'shots':['The person stands by the door.'],'soundscape':'Quiet room tone.'}
    monkeypatch.setattr(h3,'ask_json',translated)
    assert h3.convert(c,note='镜头只拍手。')
    old=c['prompt_h3_of']
    assert profiles.h3_stamp(c,'镜头只拍脸。') != old
    assert h3.convert(c,note='镜头只拍脸。')
    assert '镜头只拍手。' not in c['prompt']
    assert c['prompt'].count('【导演修正】')==1
    assert '镜头只拍脸。' in c['prompt']
    assert not profiles.h3_prompt_outdated(c,'镜头只拍脸。',strict=True)


def test_changed_stage_text_still_invalidates_a_merged_correction(monkeypatch):
    c=clip()
    monkeypatch.setattr(h3,'ask_json',lambda *a,**k:{'shots':['The person stands by the door.']})
    assert h3.convert(c,note='保持单人画面。')
    c['prompt']=c['prompt'].replace('站在门旁','坐在窗边')
    assert profiles.h3_prompt_outdated(c,'保持单人画面。',strict=True)


def test_batch_writer_persists_the_same_corrected_source_as_convert(tmp_path,monkeypatch):
    import json,sys
    from novel_manga.application.preparation import readiness
    novel=tmp_path/'book';episode=novel/'book_1';episode.mkdir(parents=True)
    path=episode/'clip_plan.json';path.write_text(json.dumps({'clips':[clip()]},ensure_ascii=False))
    monkeypatch.setattr(readiness,'inspect_episode',lambda *a,**k:({},{}))
    monkeypatch.setattr(h3,'corrections',lambda *a:{'c':'只拍手。'})
    monkeypatch.setattr(h3,'ask_json',lambda *a,**k:{'shots':['The person stands by the door.']})
    monkeypatch.setattr(sys,'argv',['build_h3_prompts.py','book','--novel-dir',str(novel),'--workers','1'])
    assert h3.main()==0
    written=json.loads(path.read_text())['clips'][0]
    assert written.get('prompt_correction_merged') is True
    assert '【导演修正】只拍手。' in written['prompt']
    assert not profiles.h3_prompt_outdated(written,'只拍手。',strict=True)
