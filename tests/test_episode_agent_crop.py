"""An actual media crop preserves audio, budgets and the normal acceptance path."""
from pathlib import Path
import json
import subprocess

import pytest

from tests.test_episode_agent_assembly import host
from novel_manga.application.agents.episode import media_repair
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json


def proposal(ctx, crop=None):
    row={'clip_id':'clip_01','action':'crop','reason':'edge artifact, important action stays visible',
         'crop':crop if crop is not None else {'x':.13,'y':0,'width':.56}}
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row]})
    return row


def sample(host):
    ctx,tools,events,state=host
    state['feedback']={'clip_01':'extra body at lower edge'}
    tools.dispatch('render')
    report=read_json(ctx.episode_dir/'thin_media_report.json')
    take=report['clips'][0]['selected'];raw=Path(take['video'])
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1','-threads','2',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(raw)],check=True)
    take.update(hypothesis='existing measured speech',duration=1)
    atomic_write_json(raw.parent/'asr.json',{'hypothesis':take['hypothesis'],'chunks':[]})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',
                      {'clip_01':{'limit':1,'reason':'one already used authorized take'}})
    return raw


def test_crop_preserves_audio_and_exhausted_generation_counts_then_reviews_normally(host,tmp_path):
    ctx,tools,events,state=host;raw=sample(host)
    original=raw.read_bytes();counts=history.generation_counts(ctx.episode_dir)
    grants=(ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    proposal(ctx)
    assert 'crop' in tools.dispatch('status')['repair_actions']['clip_01']
    assert tools.dispatch('check',{'file':'output/repair.json'})['valid']
    result=tools.dispatch('apply')
    assert result['ok'] and result['changed']==[] and result['media_changed']==['clip_01']
    report=read_json(ctx.episode_dir/'thin_media_report.json');selected=report['clips'][0]['selected']
    edited=Path(selected['postmix_video'])
    assert selected['hypothesis']=='existing measured speech' and selected['video']==str(raw)
    assert read_json(edited.parent/'asr.json')['hypothesis']=='existing measured speech'
    assert raw.read_bytes()==original and history.generation_counts(ctx.episode_dir)==counts
    assert (ctx.episode_dir/'repair_budget_grants.json').read_bytes()==grants
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(edited)],text=True))
    video=next(s for s in info['streams']if s['codec_type']=='video')
    assert (video['width'],video['height'],video['codec_name'],video['r_frame_rate'])==(1920,1080,'h264','25/1')
    audios=[]
    for index,path in enumerate([raw,edited]):
        audio=tmp_path/f'{index}.aac';subprocess.run(['ffmpeg','-y','-v','error','-i',str(path),
            '-map','0:a:0','-c:a','copy','-f','adts',str(audio)],check=True);audios.append(audio.read_bytes())
    assert audios[0]==audios[1]
    assert tools.dispatch('finish')['required_action']=='review'
    state['feedback']={}
    reviewed=tools.dispatch('review')
    assert reviewed['ok']
    assert tools.dispatch('finish')['passed']
    assert history.generation_counts(ctx.episode_dir)==counts
    assert events.count('render')==1


def test_identical_crop_reuses_output_instead_of_cropping_twice(host,monkeypatch):
    ctx,tools,events,state=host;raw=sample(host);proposal(ctx)
    first=tools.dispatch('apply');assert first['ok']
    original=media_repair.subprocess.run
    def probe_only(command,**kwargs):
        assert command[0] != 'ffmpeg', 'same crop encoded twice'
        return original(command,**kwargs)
    monkeypatch.setattr(media_repair.subprocess,'run',probe_only)
    second=tools.dispatch('apply')
    assert second['ok'] and second['reused'] and second['media_changed']==[]
    assert Path(first['crop']['video']).is_file() and first['crop']['source']==str(raw)


def test_first_pass_cannot_repair_an_already_filmed_clip_with_crop(host,monkeypatch):
    ctx,tools,events,state=host;state['feedback']={'clip_01':'edge artifact'};tools.dispatch('render')
    proposal(ctx);before=(ctx.episode_dir/'thin_media_report.json').read_bytes()
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY','1')
    monkeypatch.setattr(media_repair.subprocess,'run',lambda *a,**kw:pytest.fail('first pass edited an existing movie'))
    assert tools.dispatch('check',{'file':'output/repair.json'})['valid'] is False
    result=tools.dispatch('apply')
    assert result['ok'] is False and 'first pass' in result['error']
    assert(ctx.episode_dir/'thin_media_report.json').read_bytes()==before


@pytest.mark.parametrize('crop', [
    {'x':0,'y':0,'width':0}, {'x':.8,'y':0,'width':.5},
    {'x':True,'y':0,'width':.5}, {'x':0,'y':float('nan'),'width':.5},
    {'x':0,'y':0,'width':.5,'height':.5},
])
def test_crop_check_and_apply_reject_invalid_arguments_without_touching_media(host,monkeypatch,crop):
    ctx,tools,events,state=host;state['feedback']={'clip_01':'edge artifact'};tools.dispatch('render')
    proposal(ctx,crop);before=(ctx.episode_dir/'thin_media_report.json').read_bytes()
    monkeypatch.setattr(media_repair.subprocess,'run',lambda *a,**kw:pytest.fail('invalid crop encoded'))
    assert tools.dispatch('check',{'file':'output/repair.json'})['valid'] is False
    assert tools.dispatch('apply')['ok'] is False
    assert(ctx.episode_dir/'thin_media_report.json').read_bytes()==before
