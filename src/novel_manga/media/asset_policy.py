"""Existing refusal detection and retry constants."""
from __future__ import annotations

import re

MODERATION_MARKERS = ("violate", "usage policy", "content policy", "sensitive", "moderation", "safety", "违规", "敏感", "审核")


SCRUB_WORDS = re.compile(r"妩媚|性感|曼妙|露肩|低胸|大腿|俗气|轻浮|挑逗|妖艳|夸张")


SAFE_SUFFIX = "。整体端庄得体，衣着完整，表情自然温和，普通站姿，无任何性暗示、暴力或血腥"


ASSET_BUILD_ROUNDS = 6


ASSET_RETRY_SECONDS = 90


class ModerationRejected(RuntimeError):
    """The image service refused a card even after the prompt was toned down."""


def moderation_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in MODERATION_MARKERS)


