from novel_manga.source.splitting import split_episodes, visible_count


def test_existing_chapters_are_one_episode_each_and_keep_order():
    text = "书名\n\n第一章 他的秘密\n开门。她看见了信。\n\n第二章 真相\n他终于承认。故事结束。"
    episodes, chaptered = split_episodes(text)
    assert chaptered is True
    assert [episode.source_title for episode in episodes] == ["第一章 他的秘密", "第二章 真相"]
    assert "书名" in episodes[0].source_text
    assert "第二章" not in episodes[0].source_text
    assert episodes[1].source_text.startswith("第二章")


def test_markdown_and_english_chapters_are_detected():
    zh, chaptered_zh = split_episodes("# 第一章 开始\n正文。\n## 第二章 后来\n结尾。")
    en, chaptered_en = split_episodes("Chapter 1: Start\nHello.\nChapter 2 End\nBye.")
    assert chaptered_zh and len(zh) == 2
    assert chaptered_en and len(en) == 2


def test_6000_or_less_is_one_episode():
    text = "甲" * 5999 + "。"
    episodes, chaptered = split_episodes(text)
    assert not chaptered
    assert len(episodes) == 1
    assert episodes[0].text_count == 6000


def test_6001_to_10000_is_two_balanced_episodes_without_sentence_cut():
    sentences = [f"第{i:03d}句" + "甲" * 40 + "。" for i in range(160)]
    text = "\n".join(sentences)
    episodes, chaptered = split_episodes(text)
    assert not chaptered
    assert len(episodes) == 2
    counts = [episode.text_count for episode in episodes]
    assert abs(counts[0] - counts[1]) / (sum(counts) / 2) <= 0.05
    reconstructed = "".join(episode.source_text.replace("\n", "") for episode in episodes)
    assert reconstructed == text.replace("\n", "")


def test_more_than_10000_uses_3000_to_6000_target():
    text = "\n".join("情节推进" + "甲" * 90 + "。" for _ in range(190))
    episodes, _ = split_episodes(text)
    assert len(episodes) == 4
    assert all(3000 * 0.95 <= episode.text_count <= 6000 * 1.05 for episode in episodes)
    assert sum(episode.text_count for episode in episodes) == visible_count(text)


def test_long_chapter_splits_into_balanced_parts():
    short = "短章正文。" * 10
    long_body = "\n".join("情节推进" + "甲" * 90 + "。" for _ in range(80))  # ~7600 字
    text = f"第一章 短\n{short}\n\n第二章 长\n{long_body}\n\n第三章 也短\n{short}"
    episodes, chaptered = split_episodes(text)
    assert chaptered is True
    assert [e.source_title for e in episodes] == ["第一章 短", "第二章 长（上）", "第二章 长（下）", "第三章 也短"]
    assert [e.index for e in episodes] == [1, 2, 3, 4]
    upper, lower = episodes[1], episodes[2]
    assert abs(upper.text_count - lower.text_count) / long_body.__len__() < 0.05
    assert not upper.source_text.endswith("，")  # cut at sentence ends
    # offsets slice exactly this part out of the source
    for e in episodes:
        assert text[e.source_start:e.source_end] == e.source_text
    assert "。" in lower.source_text and upper.source_text != lower.source_text


def test_chapter_at_6000_chars_stays_one_episode():
    body = "甲" * 5990 + "。"
    text = f"第一章 整六千\n{body}"
    episodes, chaptered = split_episodes(text)
    assert chaptered is True
    assert len(episodes) == 1
    assert episodes[0].source_title == "第一章 整六千"
