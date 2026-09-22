"""Frozen review requests captured before extraction (e6e8093, 2026-09-16).

Only model calls and frame extraction are replaced. Real card encoding, evidence
loading, prompt construction and reply handling run through the production code.
"""
import ast
import contextlib
import copy
import json
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from novel_manga.llm import client as model_client
from novel_manga.models.bible import Character, StoryBible
from novel_manga.review import prompts
import novel_manga.application.review.bible as book
import novel_manga.application.review.evidence as evidence
import novel_manga.application.review.episode as episode_review
import novel_manga.application.review.judges as judges


def test_review_requests_match_before_extraction(tmp_path, monkeypatch):
    monkeypatch.delenv('NOVEL_REVIEW_MODE', raising=False)
    calls=[]
    def ask(parts,schema,**kw):
     # The contract is what is asked - parts, schema, name, budget - not where.  The bible's calls
     # now carry the judge's settings explicitly (test_endpoint_presets covers that); an endpoint
     # object in the freeze would pin a URL list that changes with the machines.
     calls.append(copy.deepcopy({'parts':parts,'schema':schema,**{k:v for k,v in kw.items() if k!='settings'}}))
     return {'verdict':'fine','people':[],'scripted':True,'evidence':'掌心裂开一张嘴。','note':'原文明确写到','instruction':'甲将物品交给乙。','characters':[],'locations':[],'summary':'梗概','open_threads':[],'standing':[]}
    with contextlib.nullcontext(tmp_path) as temp:
     novel=Path(temp)/'n';ep=novel/'n_1';work=ep/'work/review/clip_01';work.mkdir(parents=True)
     Image.new('RGB',(4,4),'white').save(ep/'frame.jpg')
     (ep/'segments.json').write_text(json.dumps([{'segment_id':'s1','text':'甲将物品交给乙。白猫在门边等待。'}]))
     (novel/'review_normal.txt').write_text('# ignored\n允许原文明确的动物形态。')
     bible=StoryBible(novel_title='n',genre='g',visual_style='v',palette='p',style_fingerprint='f',characters=[Character(name=n,gender='男',appearance='白衣少年',wardrobe='白衣') for n in ['甲','乙','丙']],locations=[])
     for i in range(3):
      p=novel/f'series_assets/characters/character_{i+1:03}/turnaround.jpeg';p.parent.mkdir(parents=True);Image.new('RGB',(4,4),'red').save(p)
     clip={'clip_id':'clip_01','kind':'video','cast':['甲','乙'],'extras':['灰山羊'],'listeners':['乙'],'background_only':['丙'],
           'location':'院内','lines':[{'speaker_name':'甲','text':'给你。'},{'speaker_name':'丙','text':'我在门外。','delivery_mode':'offscreen_dialogue'}],
           'chat_lines':[{'speaker_name':'乙','text':'收到'}],'segment_ids':['s1'],'prompt':'主要事件是甲将物品交给乙。\n',
           'references':[{'name':n,'path':f'series_assets/characters/character_{i+1:03}/turnaround.jpeg'} for i,n in enumerate(['甲','乙'])]}
     with patch.object(model_client,'ask_json',ask),patch.object(evidence,'clip_frames',lambda *a:[ep/'frame.jpg']*a[-1]),patch.object(evidence,'identity_prompt_block',lambda *a:''):
      for mode in ['classic','verify']:
       for screen in ['card','video']:
        (novel/'profile.json').write_text(json.dumps({'review_mode':mode}))
        (novel/'chat_screen.json').write_text(json.dumps({'render':screen,'group_name':'同门'}))
        judges.judge_clip(clip,ep/'fake.mp4',bible,{'院内':'白天'},'给你',work)
      book.extract_names('甲将物品交给乙。');book.extract_locations('甲在院内。',['院内'])
      judges.judge_character_cards(bible.characters[0],[ep/'frame.jpg'])
      judges.judge_location_card('院内','白天',ep/'frame.jpg')
      judges.script_check({'prompt':'主要事件是掌心裂开一张嘴。\n','segment_ids':['s1']},{'defect_issue':'掌心有嘴'},{'s1':'掌心裂开一张嘴。'})
      judges.instruction_for('丙替甲递物品','甲递物品','甲将物品交给乙。')
      (novel/'recap.json').write_text(json.dumps([{'chapter':1,'summary':'甲递物品'}]))
      book.summarize_volume(novel,1,2)
    expected = json.loads((Path(__file__).parent / "fixtures/review_requests_before.json").read_text())
    assert calls == expected


def test_prompt_formatting_is_repeatable_and_does_not_mutate_evidence(tmp_path, monkeypatch):
    from novel_manga.review.evidence import ClipEvidence
    clip = {'cast': ['甲'], 'segment_ids': ['s1'], 'lines': [{'speaker_name': '甲', 'text': '走吧'}]}
    facts = ClipEvidence({'甲': Character(name='甲', appearance='青年', wardrobe='白衣')}, ['甲'],
                         ['山羊'], [], ['乙'], [], ['图1=视频第1帧'], {'s1': '甲招呼乙。'}, '', '', '', '', {})
    before = copy.deepcopy((clip, facts))
    for build, args in [(prompts.classic_prompt, (clip, {}, '', facts)), (prompts.verify_prompt, (clip, {}, facts))]:
        assert build(*args) == build(*args)
    assert (clip, facts) == before


def test_failed_clip_does_not_discard_completed_verdicts(tmp_path, monkeypatch):
    novel = tmp_path / 'n'; directory = novel / 'n_1'; directory.mkdir(parents=True)
    bible = StoryBible(novel_title='n', genre='g', visual_style='v', palette='p', style_fingerprint='f', characters=[], locations=[])
    (novel / 'story_bible.json').write_text(bible.model_dump_json())
    clips = [{'clip_id': str(i), 'kind': 'video'} for i in range(3)]
    (directory / 'clip_plan.json').write_text(json.dumps({'clips': clips}))
    for clip in clips:
        path = directory / 'work/clips' / clip['clip_id'] / 'attempt_01'
        path.mkdir(parents=True); (path / 'clip.mp4').write_bytes(b'current take')
    called = []
    def judge(clip, *args):
        called.append(clip['clip_id'])
        if clip['clip_id'] == '1':
            raise RuntimeError('model unavailable')
        return {'severity': 'pass'}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    monkeypatch.delenv('NOVEL_REVIEW_FRESH', raising=False)
    first = episode_review.review_episode(directory)
    assert called == ['0', '1', '2']
    assert first['error_rounds'] == 1 and first['clips']['1']['severity'] == 'review_error'
    assert first['clips']['0']['severity'] == first['clips']['2']['severity'] == 'pass'
    called.clear()
    def recovered(clip, *args):
        called.append(clip['clip_id']); return {'severity': 'pass'}
    monkeypatch.setattr(judges, 'judge_clip', recovered)
    second = episode_review.review_episode(directory)
    assert called == ['1'] and second['error_rounds'] == 0
    assert second['clips']['0'] == first['clips']['0']
    assert second['clips']['2'] == first['clips']['2']
    called.clear()
    assert episode_review.review_episode(directory) == second and called == []


def test_shared_review_code_and_callers_follow_dependency_direction():
    root = Path(__file__).resolve().parents[1]
    shared = [*sorted((root / 'src/novel_manga/llm').glob('*.py')), *sorted((root / 'src/novel_manga/review').glob('*.py'))]
    scripts = {p.stem for p in (root / 'scripts').glob('*.py')}
    for path in shared:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and not node.level else []
            assert not any(name and (name.split('.')[0] in scripts or name.startswith('scripts.')) for name in names), path
    for base in ('scripts', 'src', 'experiments'):
        for path in (root / base).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    assert all(a.name != 'thin_review' for a in node.names), path
                elif isinstance(node, ast.ImportFrom):
                    assert node.module != 'thin_review', path


def test_episode_command_can_run_precise_review(tmp_path, monkeypatch):
    import runpy
    import sys
    import pytest
    novel = tmp_path / 'book'; directory = novel / 'book_1'
    attempt = directory / 'work/clips/clip_01/attempt_01'; attempt.mkdir(parents=True)
    (attempt / 'clip.mp4').write_bytes(b'mocked frames')
    bible = StoryBible(novel_title='n', genre='g', visual_style='v', palette='p', style_fingerprint='f', characters=[], locations=[])
    (novel / 'story_bible.json').write_text(bible.model_dump_json())
    (directory / 'clip_plan.json').write_text(json.dumps({'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]}))
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name']); return {'verdict': 'fine', 'people': []}
    monkeypatch.setenv('NOVEL_REVIEW_MODE', 'verify')
    monkeypatch.setattr(model_client, 'ask_json', ask)
    monkeypatch.setattr(evidence, 'clip_frames', lambda *a: [])
    monkeypatch.setattr(sys, 'argv', ['thin_review.py', 'episode', '--episode-dir', str(directory)])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/thin_review.py'), run_name='__main__')
    assert result.value.code == 0 and calls == ['clip_verify']
    report = json.loads((directory / 'episode_review.json').read_text())
    assert report['clips']['clip_01']['severity'] == 'pass'
