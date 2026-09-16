from novel_manga.config import Settings


def test_planning_timeout_defaults_to_ten_minutes() -> None:
    assert Settings().planning_timeout_seconds == 600.0
