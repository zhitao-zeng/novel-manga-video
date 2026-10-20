"""A movie review must describe the current selected takes, not a retained old assembly."""
from pathlib import Path
import pytest

from novel_manga.application.agents.episode.media_operations import MediaOperations
from novel_manga.application.review import final_cut
from novel_manga.application.review.movie_context import assembly_current, assembly_inputs
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from tests.test_episode_agent_assembly import host
from tests.test_episode_agent_existing import _retained_asr_assembly
from tests.test_final_cut_protocol import episode


def test_replaced_selected_take_reassembles_before_review_without_rendering(host):
    ctx, tools, events, state = host
    assert tools.dispatch('render')['quality_passed']
    before = history.generation_counts(ctx.episode_dir)
    report = read_json(ctx.episode_dir/'thin_media_report.json')
    selected = report['clips'][0]['selected']
    replacement = Path(selected['video']).parent.parent/'recovered-current/clip.mp4'
    replacement.parent.mkdir()
    replacement.write_bytes(b'recovered current accepted take')
    (replacement.parent/'native.wav').write_bytes(b'current cached audio')
    selected['video'] = str(replacement)
    assert not assembly_current(ctx.episode_dir, report)
    result = tools.media.review_final(report)
    assert assembly_current(ctx.episode_dir, result)
    assert events == ['render', 'review', 'assemble', 'assemble']
    assert len(state['final_reviews']) == 2
    assert history.generation_counts(ctx.episode_dir) == before


def test_missing_current_take_defers_old_movie_and_preserves_independent_failures(host):
    ctx, tools, events, state = host
    state['final_feedback'] = {'clip_01': 'old movie says wrong costume'}
    assert not tools.dispatch('render')['quality_passed']
    report = read_json(ctx.episode_dir/'thin_media_report.json')
    report['clips'][0]['selected'] = None
    report['failed_clips'] = ['clip_01']
    review = read_json(ctx.episode_dir/'episode_review.json')
    review['clips']['clip_02']['severity'] = 'fail'
    review['feedback']['clip_02'] = 'independent wrong speaker'
    atomic_write_json(ctx.episode_dir/'episode_review.json', review)
    before = history.generation_counts(ctx.episode_dir)
    result = tools.media.review_final(report)
    current = read_json(ctx.episode_dir/'episode_review.json')
    assert current['feedback'] == {'clip_02': 'independent wrong speaker'}
    assert current['clips']['clip_02']['severity'] == 'fail'
    assert not result['quality_review']['passed']
    assert not result['quality_review']['final_cut_complete']
    assert result['quality_review']['remaining'] == ['clip_01', 'clip_02']
    assert events == ['render', 'review', 'assemble'] and len(state['final_reviews']) == 1
    assert history.generation_counts(ctx.episode_dir) == before
    assert read_json(ctx.episode_dir/'final_cut_review.json')['issues']  # provenance retained


def test_direct_movie_review_cannot_attach_new_asr_to_an_unbound_old_movie(tmp_path, monkeypatch):
    runner, media, movie = episode(tmp_path)
    media['assembly'].pop('source_inputs')
    monkeypatch.setattr(final_cut.model, 'ask_json', lambda *a, **k: pytest.fail('reviewed the old movie'))
    with pytest.raises(ValueError, match='assemble current selected takes'):
        final_cut.review_assembly(runner, media)


def test_postmix_change_invalidates_assembly_even_when_raw_take_is_unchanged(tmp_path):
    runner, media, movie = episode(tmp_path)
    mixed = movie.parent/'postmix.mp4'
    mixed.write_bytes(b'current postmixed audio and picture')
    media['clips'][0]['selected']['postmix_video'] = str(mixed)
    assert not assembly_current(movie.parent, media)
    media['assembly']['source_inputs'] = assembly_inputs(runner.context.clip_plan, media)
    assert assembly_current(movie.parent, media)
    mixed.write_bytes(b'changed actual postmixed track')
    assert not assembly_current(movie.parent, media)


def test_archived_take_with_complete_asr_restores_missing_wave_without_asr_or_render(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.tools import EpisodeTools
    from novel_manga.media import analysis
    ctx, report, video, analysed, asr_calls, subtitles, cached = _retained_asr_assembly(tmp_path, monkeypatch)
    report['clips'][0]['selected'].update(cached, video=str(video), clip_id='clip_01')
    (video.parent/'native.wav').unlink()
    extracted = []
    def extract(command):
        assert command[0] == 'ffmpeg' and command[command.index('-i')+1] == str(video)
        extracted.append(command)
        Path(command[-1]).write_bytes(b'restored audio from this same take')
    monkeypatch.setattr(analysis, 'run', extract)
    before = history.generation_counts(ctx.episode_dir)
    EpisodeTools(ctx).media.assemble(report)
    assert analysed == [video] and len(extracted) == 1 and asr_calls == []
    assert subtitles and (video.parent/'native.wav').is_file()
    assert history.generation_counts(ctx.episode_dir) == before
