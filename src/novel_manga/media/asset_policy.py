"""Existing refusal detection and retry constants."""
from __future__ import annotations

import re

MODERATION_MARKERS = ("violate", "usage policy", "content policy", "sensitive", "moderation", "safety", "违规", "敏感", "审核")


SCRUB_WORDS = re.compile(r"妩媚|性感|曼妙|露肩|低胸|大腿|俗气|轻浮|挑逗|妖艳|夸张")


SAFE_SUFFIX = "。整体端庄得体，衣着完整，表情自然温和，普通站姿，无任何性暗示、暴力或血腥"


# Seedream draws the characters gpt-image-2 refuses, but it frames them its own way: the
# figure comes back cut off at the knees on a two-colour gradient, where the rest of the
# series stands head to foot on flat colour.  Measured on 尼克·弗瑞 and 毒液:
#   - a softly worded "do not crop the body" cropped it harder than saying nothing at all;
#     only a leading block with a priority marker and explicit margins holds the full figure
#   - the background note has to come last, after the style direction, or it is simply the
#     earlier of two instructions and loses
#   - the background stays a single-hue gradient whatever is said, including with the style
#     line's "complementary colours clashing, sharp rim light" removed, so that much is
#     Seedream's own habit and not a contradiction in our prompt
#   - passing an existing flat-background card as a reference made both worse: the model
#     redraws rather than borrows a look, and the figure came back half-length
SEEDREAM_FRAMING = (
    "【构图，最高优先级】全身站立像，头顶到鞋底完整可见，绝不在膝盖、大腿或腰部裁切；"
    "人物高度约占画面八成，头顶上方留白约一成，鞋底下方留白约一成；"
    "背景是单一纯色平涂，不要渐变、不要斜切色块、不要双色分割、不要任何场景。"
)

SEEDREAM_BACKGROUND = (
    "。【背景，最高优先级，覆盖以上所有画风描述】背景是一整块完全均匀的纯色，"
    "从画面左上到右下色值完全一致；背景上没有渐变、没有明暗过渡、没有光晕、"
    "没有第二种颜色、没有斜切分割、没有纹理。互补色对撞与边缘光只作用在人物身上，不作用在背景上。"
)


def seedream_prompt(prompt: str) -> str:
    """The card prompt as Seedream has to be told it, framing first and background last."""
    return SEEDREAM_FRAMING + prompt + SEEDREAM_BACKGROUND


ASSET_BUILD_ROUNDS = 6


ASSET_RETRY_SECONDS = 90


class ModerationRejected(RuntimeError):
    """The image service refused a card even after the prompt was toned down."""


def moderation_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in MODERATION_MARKERS)


# The service answers a refusal with its whole task record, and the one sentence that says
# what happened sits in the middle of it.  Cutting the first 200 characters kept the
# timestamp and the task id and dropped the reason.
_SERVICE_MESSAGE = re.compile(r"['\"]message['\"]\s*:\s*['\"](.+?)['\"]", re.DOTALL)


def refusal_text(error: Exception, limit: int = 200) -> str:
    """The sentence a refusal is actually about, short enough for a log line."""
    text = str(error).strip()
    found = _SERVICE_MESSAGE.search(text)
    if found:
        return found.group(1).strip()[:limit]
    return (text.splitlines() or [""])[-1].strip()[:limit]


