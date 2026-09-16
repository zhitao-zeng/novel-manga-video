"""Existing submission and retry predicates; no scheduling."""
import re
from ..providers.phanrouter import SubmissionUncertain
from ..providers.h3_pool import PoolUnavailable

RETRY_SUFFIX = "\n【质量重试】上一次生成的对白听不清或不完整。保持以上全部内容不变重新生成，每句台词都必须清晰完整地说出。"

RETRY_SUFFIX_H3 = ("\n\nretake_note:\nThe previous take dropped or slurred some of the lines. Keep everything "
                   "above unchanged, and have every <d> line spoken clearly and completely.")

RETRY_TAIL = re.compile("(?:" + re.escape(RETRY_SUFFIX) + "|" + re.escape(RETRY_SUFFIX_H3) + r")(?: This is take \d+\.|（第\d+次）)?\Z")

RETRY_TAIL_H3 = re.compile(re.escape(RETRY_SUFFIX_H3) + r"(?: This is take \d+\.)?\Z")

MAX_ATTEMPTS_FREE = 8

OUTPUT_MODERATION_MARKERS = ("OutputVideoSensitiveContentDetected", "OutputAudioSensitiveContentDetected")

RATE_LIMIT_RE = re.compile(r"HTTP (429|502|503|504)\b|Too Many Requests|rate ?limit|concurren|QuotaExceeded|RequestLimit|ServerOverloaded", re.I)

INPUT_TEXT_MARKER = "InputTextSensitiveContentDetected"

SOFTEN = [  # stage descriptions only get milder wording on a text-moderation refusal; spoken lines stay
    (re.compile(r"打死|弄死|杀死|杀了|杀掉|干掉"), "打倒"), (re.compile(r"鲜血|血迹|血液|流血|血"), "伤痕"), (re.compile(r"尸体|死尸"), "倒下的人"),
    (re.compile(r"砍|捅|刺"), "挥"), (re.compile(r"手枪|枪"), "棍棒"), (re.compile(r"毒品|吸毒"), "违禁品"), (re.compile(r"强奸|轮奸|猥亵"), "欺负"),
    (re.compile(r"赌博|赌钱|赌"), "比试"), (re.compile(r"废了你|打断.{0,2}腿|弄残"), "教训你"), (re.compile(r"威胁"), "警告"), (re.compile(r"自杀|上吊|跳楼"), "轻生"),
]

SUBMIT_BACKOFF_SECONDS = (30, 60, 90, 120, 180, 240, 300)

COMPLIANCE_SUFFIX = "\n【合规】画面健康、日常、无任何暴力、血腥、色情、赌博或违规内容；人物衣着完整；屏幕上的文字仅为剧情中的普通聊天内容；声音只有普通对白、环境音效和无歌词的哼唱，不含任何已有歌曲、歌词或背景音乐。"

PRESCREEN_RISK = 0.6

def takes_past_cache(settings, retake_failed: bool, cache_only: bool) -> bool:
    """Whether a clip whose cached takes all failed the speech gate gets fresh takes this run.  Always on a free
    lane (local H3); on a paid one only when a person asks (--retake-failed), since every take is paid for."""
    return (bool(settings.local_h3_base_url) or retake_failed) and not cache_only

def soften_prompt(prompt: str, rules=None) -> str:
    for pattern, replacement in SOFTEN if rules is None else rules:
        prompt = pattern.sub(replacement, prompt)
    return prompt + COMPLIANCE_SUFFIX

def resubmittable(error: Exception) -> bool:
    """A failed submission worth waiting out and asking again: the video service throttled, or no H3 pool instance had
    room within the clip's time.  Never one that may already be a paid task, nor a content refusal."""
    if isinstance(error, SubmissionUncertain) or "SensitiveContentDetected" in str(error):
        return False
    return isinstance(error, PoolUnavailable) or bool(RATE_LIMIT_RE.search(str(error)))
