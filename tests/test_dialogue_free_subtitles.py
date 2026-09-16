from novel_manga.qc import subtitle_check


def test_no_dialogue_accepts_retained_empty_ass(tmp_path):
    ass = tmp_path / 'subtitles.ass'
    ass.write_text('[Script Info]\n[Events]\n')
    assert subtitle_check(ass, required=False)['passed']
    assert not subtitle_check(ass)['passed']


def test_missing_ass_is_still_a_failure(tmp_path):
    assert not subtitle_check(tmp_path / 'missing.ass', required=False)['passed']


def test_dialogue_requires_real_caption_events(tmp_path):
    ass = tmp_path / 'subtitles.ass'
    ass.write_text('[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,你好\n')
    assert subtitle_check(ass)['passed']
