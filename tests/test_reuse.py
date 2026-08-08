from __future__ import annotations

import pytest

from novel_manga.models import EpisodePlan, ScriptTurn, Shot
from novel_manga.reuse import match_reusable_units


def _shot(index: int, *turns: ScriptTurn) -> Shot:
    quote = turns[0].source_quote
    return Shot(
        index=index,
        narration=turns[0].text,
        subtitle=turns[0].text,
        visual_prompt="画面",
        motion_prompt="动作",
        source_quote=quote,
        turns=list(turns),
    )


def test_reuse_requires_exact_visible_dialogue_but_allows_grounded_narration_compression() -> None:
    source = EpisodePlan(
        video_title="源",
        hook="源",
        summary="源",
        shots=[
            _shot(
                1,
                ScriptTurn(text="深夜，他恢复意识并听见心跳。", source_quote="深夜，他恢复意识并听见心跳。"),
                ScriptTurn(
                    role="克莱恩·莫雷蒂",
                    speaker_name="克莱恩·莫雷蒂",
                    text="我不是已经死了吗？",
                    speaking=True,
                    source_quote="我不是已经死了吗？",
                ),
            )
        ],
    )
    target = EpisodePlan(
        video_title="目标",
        hook="目标",
        summary="目标",
        shots=[
            _shot(
                1,
                ScriptTurn(text="深夜，他听见心跳。", source_quote="深夜，他恢复意识并听见心跳。"),
                ScriptTurn(
                    role="周明瑞/克莱恩·莫雷蒂",
                    speaker_name="周明瑞/克莱恩·莫雷蒂",
                    text="我不是已经死了吗？",
                    speaking=True,
                    source_quote="我不是已经死了吗？",
                ),
            )
        ],
    )

    matches = match_reusable_units(target, source)
    assert [item["match_type"] for item in matches] == [
        "grounded_narration_rewrite",
        "exact_visible_dialogue",
    ]

    changed = target.model_copy(deep=True)
    changed.shots[0].turns[1].text = "我怎么还活着？"
    with pytest.raises(ValueError, match="no safe reusable source"):
        match_reusable_units(changed, source)
