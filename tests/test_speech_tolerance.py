from novel_manga.media import speech


def test_exact_mandarin_homophones_are_not_missing_dialogue():
    for expected, heard in [('箭！', '剑'), ('艾雅！艾灵！', '哎呀艾玲')]:
        result = speech.evaluate(expected, [{'hypothesis': heard}], -20, -4)
        assert result['passed'] and result['phonetic_equivalent']
        assert result['hypothesis'] == heard


def test_phonetic_tolerance_does_not_hide_missing_or_extra_speech():
    assert not speech.evaluate('箭！', [], -60, -55)['passed']
    assert not speech.evaluate('不能开门', [{'hypothesis': '能开门'}], -20, -4).get('phonetic_equivalent')
    rows = [{'start': 0, 'end': 1, 'hypothesis': '箭'},
            {'start': 2, 'end': 6, 'hypothesis': '现在我来介绍一个完全不同的故事'}]
    assert 'excess_unplanned_speech' in speech.evaluate('箭', rows, -20, -4)['issues']


def test_nonverbal_laughter_is_advisory_and_does_not_hide_words():
    rows = [{'start': 0, 'end': 4, 'hypothesis': '请进呵呵呵呵呵呵'}]
    result = speech.evaluate('请进', rows, -20, -4)
    assert result['passed'] and result['warnings']
    assert result['hypothesis'] == rows[0]['hypothesis']
    assert not speech.evaluate('请进', [{'start': 0, 'end': 5,
        'hypothesis': '呵呵今天我来给你讲一个新的故事'}], -20, -4)['passed']


def test_short_nonlexical_cry_is_advisory_only_with_audible_signal():
    assert speech.evaluate('啊——！', [], -20, -4)['passed']
    assert not speech.evaluate('啊——！', [], -70, -65)['passed']
    assert not speech.evaluate('杀！', [], -20, -4)['passed']


def test_current_policy_refreshes_cached_failures_without_new_asr():
    old={'reference':'箭！','hypothesis':'剑','chunks':[{'hypothesis':'剑'}],
         'mean_volume_db':-20,'max_volume_db':-4,'passed':False,
         'issues':['missing_1.0_over_0.5'],'speech_issues':['missing_1.0_over_0.5'],
         'speech_evaluation_policy':5,'video':'unchanged.mp4'}
    current=speech.refresh('箭！',old,[],{})
    assert current['passed'] and current['issues']==[] and current['speech_issues']==[]
    assert current['video']==old['video'] and old['passed'] is False
    old['issues'].append('black_frames')
    assert speech.refresh('箭！',old,[],{})['issues']==['black_frames']


def test_native_cache_migration_does_not_restore_old_laughter_failure(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.media import analysis
    video=tmp_path/'clip.mp4';video.write_bytes(b'old video')
    (tmp_path/'native.wav').write_bytes(b'old wave')
    old={'reference':'请进','hypothesis':'请进呵呵呵呵呵呵',
         'chunks':[{'start':0,'end':4,'hypothesis':'请进呵呵呵呵呵呵'}],
         'mean_volume_db':-20,'max_volume_db':-4,'passed':False,
         'issues':['excess_unplanned_speech'],'speech_evaluation_policy':5}
    (tmp_path/'asr.json').write_text(json.dumps(old))
    monkeypatch.setattr(analysis,'media_duration',lambda path:5)
    current=analysis.analyse_clip(SimpleNamespace(protected_terms=[],aliases={}),
                                 {'clip_id':'clip_01','spoken_text':'请进'},video)
    assert current['passed'] and current['issues']==[]
    assert current['speech_evaluation_policy']==speech.EVALUATION_POLICY



def test_near_phonetic_name_with_exact_dialogue_context_is_corrected():
    from novel_manga.media.evidence_backends import correct_protected_lexicon
    reference = '是莉莉大婶么您是东顺王的乳娘自然算是本宫的半个母亲'
    heard = '是丽丽大师吗您是东顺王的乳娘自然算是本宫的半个母亲'
    fixed, changes = correct_protected_lexicon(heard, reference, ['莉莉大婶'])
    assert '莉莉大婶' in fixed
    assert changes[0]['source'] == '丽丽大师'
    assert changes[0]['method'] == 'phonetic_with_context'


def test_near_phonetic_name_cannot_hide_unrelated_speech_or_known_other_identity():
    from novel_manga.media.evidence_backends import correct_protected_lexicon
    reference = '是莉莉大婶么您是东顺王的乳娘'
    for heard, terms in [('丽丽大师现在讲完全不同的故事',['莉莉大婶']),
                         ('是莉莉大师么您是东顺王的乳娘',['莉莉大婶','莉莉大师'])]:
        assert correct_protected_lexicon(heard,reference,terms)[1] == []



def test_name_correction_uses_neighboring_asr_chunks_without_changing_their_times():
    reference = '是莉莉大婶么？您是东顺王的乳娘，自然算是本宫的半个母亲。'
    rows = [{'start':4.797,'end':6.048,'hypothesis':'是丽丽大师吗'},
            {'start':6.503,'end':10.055,'hypothesis':'您是东顺王的乳娘自然算是本宫的半个母亲'}]
    corrected = speech.corrected_rows(rows,reference,['莉莉大婶'],{})
    assert corrected[0]['hypothesis'] == '是莉莉大婶吗'
    assert corrected[0]['raw_hypothesis'] == rows[0]['hypothesis']
    assert [(r['start'],r['end'])for r in corrected] == [(r['start'],r['end'])for r in rows]
    assert speech.evaluate(reference,corrected,-20,-4)['passed']



def test_cached_name_refresh_retains_contextual_correction_through_recheck():
    reference = '是莉莉大婶么？您是东顺王的乳娘，自然算是本宫的半个母亲。'
    old = {'chunks':[{'start':4.797,'end':6.048,'hypothesis':'是丽丽大师吗'},
        {'start':6.503,'end':10.055,'hypothesis':'您是东顺王的乳娘自然算是本宫的半个母亲'}],
        'mean_volume_db':-20,'max_volume_db':-4,'passed':False,
        'issues':['excess_unplanned_speech'],'video':'unchanged.mp4'}
    current = speech.refresh(reference,old,['莉莉大婶'],{})
    assert current['passed'] and current['issues'] == []
    assert current['chunks'][0]['hypothesis'] == '是莉莉大婶吗'
    assert current['video'] == old['video']
