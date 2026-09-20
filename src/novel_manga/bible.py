"""Initial story-bible generation shared by batch setup and planning experiments."""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable
from typing import TypeVar

import httpx
from pydantic import ValidationError

from .config import Settings
from .llm.responses import bible_object
from .llm.transport import send_json
from novel_manga.models.bible import Character, StoryBible
from novel_manga.models.source import NovelDocument

ValidatedT = TypeVar("ValidatedT")


STYLE = (
    "精致国漫动态漫画，二维赛璐璐手绘，清晰墨线，柔和电影光影，"
    "人物五官稳定，服饰连续，竖屏中近景构图，禁止真人照片、3D和欧美卡通混入"
)

DIAGNOSIS_TOKEN_BUDGET = 6000

def _validation_feedback(error: ValueError) -> list[dict[str, object]]:
    if isinstance(error, ValidationError):
        return [
            {
                "location": [str(item) for item in row["loc"]],
                "type": row["type"],
                "message": row["msg"],
            }
            for row in error.errors(include_url=False)
        ]
    return [{"type": type(error).__name__, "message": str(error)[:3000]}]

def _validation_retry(
    revision: int,
    data: dict | None,
    error: ValidationError | ValueError,
    previous_retry: dict | None,
) -> dict[str, object]:
    """Build one bounded repair or independent-resample request."""

    # Structural feedback can repair a malformed draft, but repeatedly showing
    # a thin draft to the model anchors later attempts to the same writing.
    # Alternate one repair with one clean sample so every fresh candidate gets
    # a chance to fix mechanical schema errors without monopolising the budget.
    retry: dict[str, object] = {
        "revision": revision + 1,
        "validation_errors": _validation_feedback(error),
    }
    if revision == 0 or (previous_retry and previous_retry.get("resample")):
        retry["previous_response"] = data
    else:
        retry["resample"] = True
    return retry

def _bounded_validate(
    operation: str,
    max_revisions: int,
    request: Callable[[dict | None], dict],
    validate: Callable[[dict], ValidatedT],
) -> ValidatedT:
    """Ask a planner to repair only invalid structured output, with a hard limit."""

    repair: dict | None = None
    last_error: ValueError | None = None
    for revision in range(max_revisions + 1):
        data: dict | None = None
        try:
            data = request(repair)
            return validate(data)
        except (ValidationError, ValueError) as error:
            last_error = error
            if revision >= max_revisions:
                break
            repair = _validation_retry(revision, data, error, repair)
    assert last_error is not None
    details = json.dumps(_validation_feedback(last_error), ensure_ascii=False)
    raise ValueError(
        f"planner operation {operation} remained invalid after "
        f"{max_revisions + 1} attempt(s): {details}"
    ) from last_error

def _validate_story_bible(data: dict, novel: NovelDocument) -> StoryBible:
    bible = StoryBible.model_validate(data)
    issues: list[dict[str, object]] = []
    if re.sub(r"\s+", "", bible.novel_title) != re.sub(r"\s+", "", novel.title):
        issues.append({"field": "novel_title", "message": "must equal the requested novel title"})
    if not bible.characters:
        issues.append({"field": "characters", "message": "at least one reusable character is required"})
    if not bible.locations:
        issues.append({"field": "locations", "message": "at least one reusable location is required"})
    names = [character.name.strip() for character in bible.characters]
    if len(set(names)) != len(names):
        issues.append({"field": "characters", "message": "character names must be unique"})
    for index, character in enumerate(bible.characters):
        if not character.name.strip() or not character.appearance.strip() or not character.wardrobe.strip():
            issues.append({
                "field": f"characters.{index}",
                "message": "name, appearance, and wardrobe must be non-empty",
            })
    if issues:
        raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
    return bible.model_copy(
        update={"style_fingerprint": _fingerprint(novel.title, bible.visual_style, bible.characters)}
    )

def _compact_excerpt(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    third = limit // 3
    middle = len(text) // 2
    return text[:third] + "\n[中段抽样]\n" + text[middle - third // 2:middle + third // 2] + "\n[结尾]\n" + text[-third:]

def _fingerprint(title: str, style: str, characters: list[Character]) -> str:
    payload = title + style + "|".join(f"{c.name}:{c.appearance}:{c.wardrobe}" for c in characters)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class BibleBuilder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout)


    def _json(
        self,
        system: str,
        user: str,
        repair: dict | None = None,
        *,
        token_budget: int | None = None,
    ) -> dict:
        base = str(self.settings.llm_base_url).rstrip("/")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if repair:
            feedback = json.dumps(repair["validation_errors"], ensure_ascii=False)
            semantic_repair = any(
                code in feedback
                for code in (
                    "MISSING_CAUSALITY",
                    "CAUSAL_GAP",
                    "CHARACTER_MOTIVATION",
                    "causal_chain_broken",
                )
            )
            semantic_guidance = (
                "错误发生在某镜时，先找出观众理解该揭示或行动所必需的前置事实、关系和代价，"
                "并把这些内容落实到更小shot index的镜头；后置台词不能反向补足此前的动机。"
                "必须修改可检查的shots、event_ids、source_quote、动作或反应设计，"
                "不得只润色被点名镜头的措辞。"
                if semantic_repair
                else ""
            )
            if "causal_chain_broken" in feedback:
                semantic_guidance += (
                    "对于反馈中的‘结果事件缺少前置事件’pair，必须把前置event_id加入更小shot index的镜头，"
                    "并同步修正adaptation_ledger；只解释原因但不绑定event_ids不算修复。"
                )
            if "narrator_summarises_dialogue" in feedback:
                semantic_guidance += (
                    "凡旁白概括原文引号对白的镜头，删除概括句，按source_quote把原文对白逐条完整恢复给具体角色；"
                    "每条使用derivation=verbatim，不得合并、删词或同义改写。"
                )
            # A resample deliberately starts clean: carrying the rejected draft
            # would both anchor the model to it and, at roughly 25k tokens for
            # a full screenplay, overflow the context window once the output
            # budget is added.
            if repair.get("previous_response") is not None and not repair.get("resample"):
                messages.append({
                    "role": "assistant",
                    "content": json.dumps(repair["previous_response"], ensure_ascii=False),
                })
            if repair.get("resample"):
                messages.append({
                    "role": "user",
                    "content": (
                        "上一稿未通过校验，请重新独立创作一稿，不要沿用上一稿的写法。"
                        + semantic_guidance
                        + "需要避免的问题："
                        + feedback[:1200]
                    ),
                })
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        "上一次 JSON 未通过确定性校验。只修复列出的错误，继续忠于输入原文，"
                        "不要解释、不要输出 Markdown。"
                        + semantic_guidance
                        + "校验反馈："
                        + feedback
                    ),
                })
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "max_tokens": min(
                self.settings.llm_max_tokens,
                token_budget or self.settings.llm_max_tokens,
            ),
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        if repair and repair.get("resample"):
            # A resample only helps if it can actually diverge; at temperature
            # 0.2 the model reproduces its previous answer almost verbatim,
            # which is how three "revisions" came back byte-identical.  A seed
            # derived from the attempt number is not enough either: the prompt
            # is identical across resamples, so the sampler has to be told to
            # draw fresh each time.
            payload["temperature"] = 1.0
            payload["top_p"] = 0.95
            payload["seed"] = random.randint(1, 2**31 - 1)
        if self.settings.llm_disable_thinking:
            # vLLM/Qwen accepts this OpenAI-compatible extension.  Keep it
            # opt-in so hosted OpenAI-compatible providers are unaffected.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        response = send_json(self.client, f"{base}/chat/completions",
                             {"Authorization": f"Bearer {self.settings.llm_api_key}"}, payload)
        if getattr(response, "status_code", 200) == 400:
            try:
                error_message = str(response.json()["error"]["message"])
            except (KeyError, TypeError, ValueError):
                error_message = ""
            context_match = re.search(
                r"maximum context length is (\d+) tokens", error_message
            )
            input_match = re.search(
                r"prompt contains at least (\d+) input tokens", error_message
            )
            if context_match and input_match:
                adjusted = max(
                    512,
                    min(
                        18000,
                        int(context_match.group(1))
                        - int(input_match.group(1))
                        - 1024,
                    ),
                )
                if adjusted < int(payload["max_tokens"]):
                    payload["max_tokens"] = adjusted
                    response = send_json(self.client, f"{base}/chat/completions",
                                         {"Authorization": f"Bearer {self.settings.llm_api_key}"}, payload)
        try:
            response.raise_for_status()
        except Exception as error:
            # httpx names the status and nothing else; the server's body is
            # where the actual complaint is, and without it a 400 from the
            # inference server is undiagnosable from the logs.
            body = ""
            try:
                body = response.text[:600]
            except Exception:
                pass
            raise RuntimeError(f"{error}; body: {body}") from error
        content = response.json()["choices"][0]["message"]["content"]
        return bible_object(content)


    def build_bible(self, novel: NovelDocument) -> StoryBible:
        schema = StoryBible.model_json_schema()
        system = (
            "你是漫剧总美术和小说事实核验员。只提取原文可支持的信息；外貌未写明时可做克制设计。"
            "每名主要角色还要建立可跨集复用的选角档案：visual_archetype写社会与戏剧类型，"
            "face_anchors写3-5个不可漂移的五官锚点，silhouette、hair、palette和base_costume必须彼此可区分，"
            "signature_prop只填写原文支持或不改变剧情的识别物，expression_profile描述表情幅度，"
            "motion_signature描述角色惯用姿态和动作节奏，voice_profile_id填写稳定的声音角色标识。"
            "场景locations每条写成「地点名：一句空场描写」：冒号前是全书唯一的地点名，冒号后写这个地方的"
            "建筑结构、空间布局、关键物品与材质、时段与主光源方向。整条会原样用作空场卡的提示词——只写名字，"
            "模型就只能自己编。不把人物和人物动作写进地点。"
            "所有角色必须是健康、非色情、非血腥的统一国漫画风。严格输出 JSON。"
        )
        user = (
            f"小说名：{novel.title}\n文本：{_compact_excerpt(novel.text)}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}\n"
            f"visual_style 必须包含：{STYLE}。style_fingerprint 暂填空字符串。"
        )
        return _bounded_validate(
            "build_bible",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=DIAGNOSIS_TOKEN_BUDGET
            ),
            lambda data: _validate_story_bible(data, novel),
        )


