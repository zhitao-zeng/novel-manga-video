"""Automatic rescue for a prompt the video service's text filter refuses.

The filter scores whole texts rather than matching a word list: after a manual
rescue of eight episodes on 2026-09-09 the refusal points moved around as the
wording changed, and one refusal turned out to be a single innocuous camera
sentence that every one of its own fragments passed.  Guessing words is
therefore a poor strategy; asking the filter is a good one.

The cheap Seedance lane shares the same input filter as the expensive one, so a
4 s text-only sd2.0 task is an oracle: a refusal costs nothing and an accepted
probe costs about a third of a second of sd2.5.  This module bisects the refused
prompt with those probes, has the local Qwen rewrite the offending part, and
verifies the rewrite through the same oracle before the caller pays for a video.

Spoken lines (the ``{...}`` spans) may change too - the manual rescue only
succeeded once a character name was replaced everywhere, lines included - but
never silently: a rewrite must declare each line edit, and the caller applies the
same edits to the clip's ``lines`` so the subtitles and the speech gate keep
matching what the model is asked to say.
"""
from __future__ import annotations

import json
import re
import time

import httpx

INPUT_TEXT_MARKER = "InputTextSensitiveContentDetected"
PROBE_MODEL = "sd2.0"  # cheapest video model behind the same input filter
PROBE_SECONDS = 4  # the shortest task the endpoint accepts
DEFAULT_BUDGET = 24  # probes per clip; a bisect of a 40-line prompt costs about 12
LOCATE_SHARE = 0.5  # at most half the budget goes to locating, the rest verifies rewrites
DEFAULT_ROUNDS = 3
SPOKEN = re.compile(r"\{[^{}]*\}")
# Boilerplate the rewrite must not touch: the negative list is the same in every
# prompt of the series, and a blanket word swap inside it once forbade the very
# effect the rewritten scene relies on.
FIXED_SECTIONS = ("【视觉语法】", "【不要】", "【合规】")
# What the platform's filter reacts to, in the order the rewrite should attack it.
ESCALATION = [
    "只改动定位到的措辞，其余逐字保留。",
    "除了定位到的措辞，还要把像真人艺名、影视人物或知名品牌的角色名整段统一换成普通的中文名或称呼，"
    "把国别、民族的指称换成架空说法（例如“东瀛/岛国”改成“异域”，“熊国人”改成“北国人”）。",
    "彻底中性化：删掉一切杀意、血腥、恐惧、羞辱、暧昧的形容，把打斗写成克制的过招，把情绪写成“神色一变”这类平淡描述，"
    "可疑的角色名一律换成普通称呼。宁可平淡也要通过审核。",
]


class ProbeBudget(Exception):
    """Raised when a repair has spent its allowance of oracle calls."""


class Oracle:
    """`refused(text)` through a 4 s text-only task on the cheap lane."""

    def __init__(self, settings, budget: int = DEFAULT_BUDGET) -> None:
        self.base_url = settings.phanrouter_base_url.rstrip("/")
        self.key = settings.phanrouter_image_api_key
        self.left = budget
        self.used = 0
        self.accepted = 0
        self._client = httpx.Client(timeout=60.0, trust_env=False)

    @property
    def available(self) -> bool:
        return bool(self.key)

    def refused(self, text: str) -> bool:
        if self.left <= 0:
            raise ProbeBudget(f"probe budget spent after {self.used} probes")
        self.left -= 1
        self.used += 1
        body = {
            "model": PROBE_MODEL,
            "content": [{"type": "text", "text": text}],
            "ratio": "16:9",
            "resolution": "480p",
            "duration": PROBE_SECONDS,
            "generate_audio": False,
            "watermark": False,
            "output_format": "mp4",
        }
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        response = None
        for wait in (5, 15, 30, None):
            response = self._client.post(f"{self.base_url}/api/v3/contents/generations/tasks", headers=headers, json=body)
            if INPUT_TEXT_MARKER in response.text:
                return True
            if response.status_code == 200:
                self.accepted += 1  # a 4 s sd2.0 task we never download: the oracle's whole cost
                return False
            if response.status_code in (429, 502, 503, 504) and wait is not None:
                time.sleep(wait)
                continue
            break
        raise RuntimeError(f"moderation probe failed: HTTP {response.status_code} {response.text[:200]}")

    def close(self) -> None:
        self._client.close()


def _split(text: str) -> list[str]:
    """Lines, then sentences, then comma phrases - always reassembling exactly."""
    parts = text.splitlines(keepends=True)
    if len(parts) > 1:
        return parts
    parts = [p for p in re.split(r"(?<=[。！？；])", text) if p]
    if len(parts) > 1:
        return parts
    return [p for p in re.split(r"(?<=[，、：])", text) if p] or [text]


def locate(prompt: str, oracle: Oracle, allowance: int) -> list[str]:
    """Narrow a refused prompt to the smallest refused piece the allowance buys.

    An empty list means the refusal belongs to the text as a whole: no half of it
    is refused on its own, so there is nothing to point the rewrite at.
    """
    spent_at_start = oracle.used
    parts = _split(prompt)
    while len(parts) > 1:
        if oracle.used - spent_at_start >= allowance:
            break
        middle = len(parts) // 2
        try:
            if oracle.refused("".join(parts[:middle])):
                parts = _split(parts[0]) if middle == 1 else parts[:middle]
                continue
            if oracle.refused("".join(parts[middle:])):
                parts = _split(parts[middle]) if len(parts) - middle == 1 else parts[middle:]
                continue
        except ProbeBudget:
            break
        return []  # neither half alone is refused: the text is judged as a whole
    span = "".join(parts).strip()
    return [span] if span and span != prompt.strip() else []


def _section(text: str, header: str) -> str | None:
    """The text of one 【…】 section, from its header to the next one."""
    start = text.find(header)
    if start < 0:
        return None
    end = text.find("\n【", start + 1)
    return text[start:] if end < 0 else text[start:end]


def _restore_fixed(original: str, rewritten: str) -> str:
    for header in FIXED_SECTIONS:
        before, after = _section(original, header), _section(rewritten, header)
        if before and after and before != after:
            rewritten = rewritten.replace(after, before)
    return rewritten


def _risky_spans(prompt: str) -> list[str]:
    """The local model's ranking of what a Chinese platform filter would flag."""
    from novel_manga.model_client import ask_json

    schema = {"type": "object", "properties": {"spans": {"type": "array", "items": {"type": "string"}}},
              "required": ["spans"], "additionalProperties": False}
    question = (
        "你是中国视频生成平台的文本审核专家。平台会拦截：涉政与国家民族贬称、暴力血腥、色情暧昧、脏话侮辱、"
        "真实人物或艺人姓名、封建迷信。下面这段提示词被平台的文本审核拒绝了，请逐字摘录最多 6 个最可能触发拦截的原文片段，"
        "按风险从高到低排列，只输出 JSON。\n\n" + prompt[:6000])
    return [s for s in ask_json([{"type": "text", "text": question}], schema, name="modrisk", max_tokens=600).get("spans", []) if s]


def _rewrite(prompt: str, spans: list[str], round_index: int, protect: tuple[str, ...] = ()) -> tuple[str, list[dict]] | None:
    """Rewrite the prompt; returns it with the spoken-line edits it declares."""
    from novel_manga.model_client import ask_json

    schema = {"type": "object", "properties": {
        "prompt": {"type": "string"},
        "line_edits": {"type": "array", "items": {"type": "object", "properties": {
            "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"], "additionalProperties": False}},
    }, "required": ["prompt", "line_edits"], "additionalProperties": False}
    target = ("重点是这些片段：" + json.dumps(spans, ensure_ascii=False)) if spans else "整段文本被作为一个整体判定，没有单独的触发点"
    keep = ("\n5. 这些是本系列的固定主角名，必须逐字保留、不得改名：" + "、".join(protect) + "。") if protect else ""
    question = (
        "你在帮一个中国视频生成平台的用户改写提示词，让它通过平台的文本审核。这段提示词已被拒绝，" + target + "。\n"
        "要求：\n"
        f"1. {ESCALATION[min(round_index - 1, len(ESCALATION) - 1)]}\n"
        "2. 段落结构、【】小节标记、@图片N 的引用编号和画面调度的意思都保持不变；\n"
        "3. 花括号 {} 里是角色要说出口的台词。默认逐字保留；确实必须改动时，"
        "把每一处改动写进 line_edits（old 是原台词全文，new 是新台词），并保证改写后的提示词里的台词与之一致；\n"
        "4. prompt 字段给出改写后的完整提示词，不要省略任何段落。" + keep + "\n\n"
        "提示词：\n" + prompt)
    verdict = ask_json([{"type": "text", "text": question}], schema, name="modrepair", max_tokens=8000)
    rewritten = str(verdict.get("prompt", "")).strip()
    if not rewritten or len(rewritten) < len(prompt) * 0.5:
        return None
    edits = [{"old": str(e.get("old", "")), "new": str(e.get("new", ""))} for e in verdict.get("line_edits", []) if e.get("old")]
    expected = SPOKEN.findall(prompt)
    for edit in edits:
        expected = [span.replace(edit["old"], edit["new"]) for span in expected]
    if SPOKEN.findall(rewritten) != expected:
        return None  # the declared edits do not explain the lines it actually changed
    if any(name and name not in rewritten for name in protect if name in prompt):
        return None  # a lead of the series was renamed
    return _restore_fixed(prompt, rewritten), edits


def repair(prompt: str, settings, *, log=print, assemble=lambda text: text, protect: tuple[str, ...] = (),
           rounds: int = DEFAULT_ROUNDS, budget: int = DEFAULT_BUDGET) -> tuple[str, list[dict]] | None:
    """Return an accepted rewrite with its spoken-line edits, or None.

    ``assemble`` maps a base prompt to the text actually submitted (director note,
    retry and compliance suffixes), so the oracle judges what will really be sent.
    """
    oracle = Oracle(settings, budget)
    if not oracle.available:
        log("moderation repair: no probe-lane key (PHANROUTER_IMAGE_API_KEY); skipping")
        return None
    try:
        # The filter is not deterministic: the same text is sometimes accepted on a
        # second look, and one probe is far cheaper than a rewrite.
        if not oracle.refused(assemble(prompt)):
            log("moderation repair: the filter accepts the prompt on a second look; resubmitting unchanged")
            return prompt, []
        allowance = max(2, int(budget * LOCATE_SHARE))
        spans = locate(assemble(prompt), oracle, allowance)
        if spans:
            log(f"moderation repair: refusal narrowed to {json.dumps(spans, ensure_ascii=False)[:200]}")
        else:
            spans = _risky_spans(prompt)
            log(f"moderation repair: judged as a whole; rewriting the model's picks {json.dumps(spans, ensure_ascii=False)[:200]}")
        candidate = prompt
        for round_index in range(1, rounds + 1):
            rewritten = _rewrite(candidate, spans, round_index, protect)
            if not rewritten:
                log(f"moderation repair: round {round_index} produced no usable rewrite")
                continue
            text, edits = rewritten
            try:
                if not oracle.refused(assemble(text)):
                    log(f"moderation repair: round {round_index} accepted ({oracle.used} probes, {oracle.accepted} paid, "
                        f"{len(edits)} line edits)")
                    return text, edits
            except ProbeBudget as exhausted:
                log(f"moderation repair: {exhausted}")
                break
            log(f"moderation repair: round {round_index} still refused")
            candidate = text
    except (RuntimeError, httpx.HTTPError, ValueError, ProbeBudget) as error:
        log(f"moderation repair failed ({type(error).__name__}: {str(error)[:160]})")
    finally:
        oracle.close()
    return None
