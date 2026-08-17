from novel_manga.runtime_backends import merge_punctuation_only_events


def test_merge_punctuation_only_events_preserves_text_and_time() -> None:
    events = [
        {"start": 0.24, "end": 6.46, "text": "前一行\\N后一行"},
        {"start": 6.46, "end": 6.64, "text": "。"},
    ]

    repaired = merge_punctuation_only_events(events)

    assert repaired == [
        {"start": 0.24, "end": 6.64, "text": "前一行\\N后一行。"}
    ]


def test_merge_punctuation_only_events_leaves_normal_pages_unchanged() -> None:
    events = [
        {"start": 0.2, "end": 1.0, "text": "第一句。"},
        {"start": 1.0, "end": 2.0, "text": "第二句。"},
    ]

    assert merge_punctuation_only_events(events) == events
