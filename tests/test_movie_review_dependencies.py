"""Movie picture evidence survives derived judge updates, not changed production inputs."""
from copy import deepcopy
from pathlib import Path
import pytest
from novel_manga.application.review import final_cut, independent, final_dispute, final_dispute_review
from novel_manga.application.review.execution import save_result
from novel_manga.application.review.movie_context import assembly_inputs
from novel_manga.util import read_json, atomic_write_json
from tests.test_final_cut_protocol import episode
from tests.test_final_dispute import current_case, resolution
from tests.test_independent_final_review import setup


def completed(tmp_path):
    runner, media, movie = episode(tmp_path)
    directory = runner.context.episode_dir
    image = directory/'reference.jpg'; image.write_bytes(b'approved reference')
    plan = read_json(directory/'clip_plan.json')
    plan['clips'][0]['references'] = [{'path':'book_1/reference.jpg','role':'character'}]
    atomic_write_json(directory/'clip_plan.json', plan)
    runner.context.clip_plan = plan
    media["assembly"]["source_inputs"] = assembly_inputs(plan,media)
    final = {'policy':final_cut.POLICY, 'movie':str(movie),
             'context':final_cut.context(directory, media, movie),
             'complete':True, 'passed':True, 'issues':[], 'errors':[], 'frames':[]}
    atomic_write_json(directory/'final_cut_review.json', final)
    return runner, media, final


def test_native_reviewer_metadata_does_not_buy_another_whole_movie_review(tmp_path, monkeypatch):
    runner, media, final = completed(tmp_path); directory=runner.context.episode_dir
    native=read_json(directory/'episode_review.json')
    native['clips']['a'].update(policy='updated-native-policy', story_ok=True, feedback='same picture, revised wording')
    atomic_write_json(directory/'episode_review.json', native)
    monkeypatch.setattr(final_cut.subprocess, 'check_output', lambda *a,**k:pytest.fail('unchanged movie was decoded again'))
    assert final_cut.current(directory,media,final)
    assert final_cut.review_assembly(runner,media)==final


def test_new_native_failure_still_blocks_acceptance_with_cached_movie_pass(tmp_path):
    runner, media, final = completed(tmp_path); directory=runner.context.episode_dir
    native=read_json(directory/'episode_review.json')
    baseline=save_result(directory,deepcopy(media),native,plan=runner.context.clip_plan,write_media=False)
    assert baseline['quality_review']['passed']
    native['clips']['a'].update(severity='fail',policy='updated-native-policy',story_issue='speaker is the wrong person')
    native['feedback']={'a':'speaker is the wrong person'}
    atomic_write_json(directory/'episode_review.json',native)
    assert final_cut.current(directory,media,final)
    checked=save_result(directory,deepcopy(media),native,plan=runner.context.clip_plan,write_media=False)
    assert not checked['quality_review']['passed']
    assert not checked['quality_review']['native_content_passed']
    assert checked['quality_review']['remaining']==['a']


@pytest.mark.parametrize('change',['movie','script','reference','speech','delivery'])
def test_changed_actual_movie_evidence_still_invalidates_picture_review(tmp_path,change):
    runner,media,final=completed(tmp_path);d=runner.context.episode_dir
    if change=='movie':Path(final['movie']).write_bytes(b'a different movie')
    elif change=='script':atomic_write_json(d/'chapter_script.json',{'shots':[{'event':'changed source event'}]})
    elif change=='reference':(d/'reference.jpg').write_bytes(b'a different approved reference')
    elif change=='speech':media['clips'][0]['selected']['chunks']=[{'hypothesis':'newly measured speech','start':0,'end':1}]
    else:atomic_write_json(d/'delivery_policy.json',{'interaction_ambience':{'changed':True}})
    assert not final_cut.current(d,media,final)


def test_native_metadata_does_not_renew_one_use_picture_dispute(tmp_path,monkeypatch):
    media,final=current_case(tmp_path,monkeypatch)
    monkeypatch.setattr(independent,'observe',lambda d,f,*,dispute:resolution(f,dispute['findings']))
    decision={'reason':'current picture disproves phone allegation','evidence':['actual current JPEG']}
    final_dispute_review.adjudicate(tmp_path,'a',decision)
    native=read_json(tmp_path/'episode_review.json');native['clips']['a']['policy']='updated-native-policy'
    atomic_write_json(tmp_path/'episode_review.json',native)
    # Rebuilding audit metadata must not reopen a judgment of the same pictures.
    cached=read_json(tmp_path/'final_cut_review.json')
    cached['context']=final_cut.context(tmp_path,media,cached['movie'])
    child=cached.get('independent_review')
    if child:
        child['context']=deepcopy(cached['context'])
        atomic_write_json(tmp_path/'independent_review.json',child)
    atomic_write_json(tmp_path/'final_cut_review.json',cached)
    assert not final_dispute.available(tmp_path,'a')
    with pytest.raises(ValueError,match='already attempted'):
        final_dispute_review.adjudicate(tmp_path,'a',decision)


def test_independent_observation_is_reused_across_native_metadata_updates(tmp_path,monkeypatch):
    runner,media,final,raw=setup(tmp_path,monkeypatch)
    previous=independent.register(tmp_path,media,raw)
    native=read_json(tmp_path/'episode_review.json');native['clips']['a']['policy']='updated-native-policy'
    atomic_write_json(tmp_path/'episode_review.json',native)
    latest={**final,'context':final_cut.context(tmp_path,media,final['movie'])}
    monkeypatch.setattr(independent,'run_agent',lambda *a,**k:pytest.fail('same actual movie sent to independent Agent twice'))
    cached=independent.observe(tmp_path,latest,config={'runs_root':str(tmp_path/'runs'),'extra_args':[]})
    assert cached==previous
    assert independent.current(tmp_path,{**latest,'independent_review':cached})


def test_independent_prompt_receives_existing_shared_watchable_standard(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.review.contracts import WATCHABLE_CRITERIA
    runner,media,final,raw=setup(tmp_path,monkeypatch)
    def observe(run,*,config,timeout,**kwargs):
        work=Path(config['runs_root'])/run
        assert WATCHABLE_CRITERIA in (work/'prompt.txt').read_text()
        packet=read_json(work/'input/review.json')
        answer={'complete':True,'passed':True,'issues':[],
                'observations':[{'frame_path':f['path'],'observation':'actual current movie picture'}
                                for f in packet['primary_frames']]}
        atomic_write_json(work/'output/review.json',answer)
        return SimpleNamespace(ok=True,produced=['review.json'],directory=work/'attempt-1')
    monkeypatch.setattr(independent,'run_agent',observe)
    result=independent.observe(tmp_path,final,config={'runs_root':str(tmp_path/'runs'),'extra_args':[]})
    assert result['complete']


@pytest.mark.parametrize('related', [False, True])
def test_only_relevant_book_material_invalidates_current_movie_review(tmp_path, related):
    runner,media,final = completed(tmp_path);directory=runner.context.episode_dir
    script={'shots':[{'event':'甲走进屋内。','characters':['甲']}]}
    atomic_write_json(directory/'chapter_script.json',script)
    bible={'characters':[{'name':'甲','appearance':'current lead'},
                         {'name':'乙','appearance':'unrelated later character'}],
           'props':[], 'locations':['屋内：当前场景','山上：后续场景']}
    atomic_write_json(directory.parent/'story_bible.json',bible)
    final['context']=final_cut.context(directory,media,final['movie'])
    bible['characters'][0 if related else 1]['appearance']='new design'
    atomic_write_json(directory.parent/'story_bible.json',bible)
    assert final_cut.current(directory,media,final) is (not related)


@pytest.mark.parametrize('changed', ['take', 'postmix', 'missing'])
def test_assembly_is_bound_to_actual_selected_takes(tmp_path, changed):
    from novel_manga.application.review.movie_context import assembly_current
    runner,media,final=completed(tmp_path);directory=runner.context.episode_dir
    assert assembly_current(directory,media)
    if changed=='missing':media['clips'][0]['selected']=None
    elif changed=='take':Path(media['clips'][0]['selected']['video']).write_bytes(b'changed native take')
    else:
        edited=directory/'cropped.mp4';edited.write_bytes(b'current postprocessed take')
        media['clips'][0]['selected']['postmix_video']=str(edited)
    assert not assembly_current(directory,media)
    assert not final_cut.current(directory,media,final)
