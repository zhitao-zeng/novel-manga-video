"""planner_requests_thin responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.llm.config import endpoint_order
from novel_manga.llm.responses import extract_json
from novel_manga.llm.transport import post_any
from thin_profile import FRAMES
from thin_profile import STYLE_NAME
from thin_profile import frame_spec
import httpx
import json
import os
import re
import time
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.contracts as pc_contracts
import novel_manga.planning.prompts as pc_prompts
import novel_manga.planning.validation as pc_validation

class IncompleteOutlineError(RuntimeError):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        super().__init__("first-pass outline incomplete: " + "; ".join(attempts[-1]["errors"]))


def generate_outline(client: httpx.Client, endpoints: list[str], headers: dict, *, model: str, payload: dict,
                     mode: str, max_tokens: int, timeout: float, seed: int | None = None) -> tuple[str, list[dict]]:
    """Normal completion and an explicit complete artifact are prerequisites for pass two."""
    segment_ids = [s["segment_id"] for s in payload["segments"]]
    schema = pc_contracts.outline_schema(mode, segment_ids)
    attempts = []
    deadline = time.monotonic() + timeout
    original_timeout = client.timeout
    for limit in dict.fromkeys((max_tokens, max(max_tokens, min(max_tokens * 2, 8192)))):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        started = time.monotonic()
        row = {"max_tokens": limit}
        reminder = ("\n上一次未形成完整提纲。修复：" + "; ".join(attempts[-1]["errors"])) if attempts else ""
        request = {
            "model": model, "temperature": 0.3, "max_tokens": limit,
            **({"seed": seed} if seed is not None else {}),
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "chapter_outline", "strict": True, "schema": schema}},
            "messages": [{"role": "system", "content": pc_prompts.outline_prompt(mode) + reminder},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }
        try:
            client.timeout = httpx.Timeout(remaining)
            response = post_any(client, endpoints, headers, request)
            choice = response["choices"][0]
            content = choice["message"].get("content") or ""
            errors = pc_contracts.validate_outline(content, mode, segment_ids)
            if choice.get("finish_reason") != "stop":
                errors.insert(0, f"finish_reason={choice.get('finish_reason')}")
            row.update(finish_reason=choice.get("finish_reason"), content_chars=len(content), usage=response.get("usage", {}), errors=errors)
        except (httpx.HTTPError, TimeoutError) as error:
            row["errors"] = [type(error).__name__]
            row["seconds"] = round(time.monotonic() - started, 3)
            attempts.append(row)
            raise IncompleteOutlineError(attempts) from error
        finally:
            client.timeout = original_timeout
        row["seconds"] = round(time.monotonic() - started, 3)
        attempts.append(row)
        if not errors:
            return content, attempts  # preserve the entire explicit artifact, never a reasoning suffix
    if not attempts:
        attempts = [{"errors": ["outline time budget exhausted"]}]
    raise IncompleteOutlineError(attempts)


def patch_plan(raw: dict, missing_ids: list[str], faulty: dict[str, list[str]], segments: list[dict], names: list[str], locations: list[str], *, timeout: float = pc_constants.PATCH_TIMEOUT_SECONDS, ctx: PlannerContext) -> dict:
    """Repair a plan with one small call instead of a 150-650 s re-plan.

    The model sees the clip outline, the forgotten segments' text and the
    rejected stages with their errors and source text; it returns only the
    stages to insert and the replacements for the rejected ones.  The result
    is a deep copy; the caller validates it like any draft.
    """
    from novel_manga.llm.client import ask_json
    segment_ids = [s["segment_id"] for s in segments]
    texts = {s["segment_id"]: s["text"] for s in segments}
    slots = pc_validation.stage_slots(raw)
    faulty = {label: errs for label, errs in faulty.items() if label in slots}
    clip_ids = [str(c.get("clip_id")) for c in raw.get("clips", []) if isinstance(c, dict)]
    if not clip_ids or not (missing_ids or faulty):
        raise ValueError("nothing to patch")
    stage_of = lambda ids: pc_contracts.build_schema(names, locations, ids, ctx=ctx)["properties"]["clips"]["items"]["properties"]["stages"]["items"]  # noqa: E731
    outline = []
    for clip in raw.get("clips", []):
        stages = clip.get("stages") or []
        outline.append({
            "clip_id": clip.get("clip_id"), "location": clip.get("location"), "characters": clip.get("characters"),
            "stages": [{"n": i, "segment_id": s.get("segment_id"), "event": str(s.get("event", ""))[:60]} for i, s in enumerate(stages, start=1)],
        })
    schema = {
        "type": "object", "additionalProperties": False, "required": ["insertions", "replacements"],
        "properties": {
            "insertions": {"type": "array", "minItems": len(missing_ids), "maxItems": len(missing_ids), "items": {
                "type": "object", "additionalProperties": False, "required": ["clip_id", "after_stage", "stage"],
                "properties": {"clip_id": {"type": "string", "enum": clip_ids}, "after_stage": {"type": "integer", "minimum": 0},
                               "stage": stage_of(missing_ids or segment_ids)}}},
            "replacements": {"type": "array", "minItems": len(faulty), "maxItems": len(faulty), "items": {
                "type": "object", "additionalProperties": False, "required": ["label", "stage"],
                "properties": {"label": {"type": "string", "enum": list(faulty) or ["-"]}, "stage": stage_of(segment_ids)}}},
        },
    }
    parts = ["下面是一集短剧的分镜大纲。只输出需要修改的部分，不要改动其他阶段。"]
    if missing_ids:
        parts.append(f"原文里有 {len(missing_ids)} 个区段还没有任何阶段引用（{'、'.join(missing_ids)}）。为每个遗漏区段补写恰好一个阶段，插进最合适的 clip"
                     "（after_stage 是插在该 clip 第几个阶段之后，0 表示放在最前）；新阶段的 segment_id 必须是该遗漏区段。")
    if faulty:
        parts.append(f"另有 {len(faulty)} 个阶段没过硬门检查，逐个重写整个阶段（label 原样填回，segment_id 不变），只修错误指出的问题，其余内容尽量保持。")
    parts.append("规则：source_quote 从该区段原文逐字复制 8 到 120 字；画面描述不得出现血液、伤口、破皮、流血，碑上的结果写成无字的发光纹路；"
                 "offscreen_dialogue 和 chat_message 必须写 speaker_name；台词从原文取；只用给出的人物名，格式和已有阶段一致。")
    parts.append(f"分镜大纲：{json.dumps(outline, ensure_ascii=False)}")
    parts.append(f"可用人物：{names}")
    shown: list[str] = list(missing_ids)
    for label, errs in faulty.items():
        clip_index, stage_index = slots[label]
        stage = raw["clips"][clip_index]["stages"][stage_index]
        parts.append(f"问题阶段 {label}\n错误：" + " | ".join(errs) + f"\n当前内容：{json.dumps(stage, ensure_ascii=False)}")
        shown.append(str(stage.get("segment_id")))
    for sid in dict.fromkeys(shown):
        if sid in texts:
            parts.append(f"区段 {sid} 原文：\n{texts[sid][:1800]}")
    budget = min(6000, 800 + 900 * (len(missing_ids) + len(faulty)))
    verdict = ask_json([{"type": "text", "text": "\n\n".join(parts)}], schema, name="plan_patch", max_tokens=budget, timeout=timeout, retry_truncated=False)
    patched = json.loads(json.dumps(raw, ensure_ascii=False))
    for item in verdict.get("replacements", []):  # replacements first: insertions shift stage numbers
        slot = slots.get(str(item.get("label")))
        if slot and isinstance(item.get("stage"), dict):
            patched["clips"][slot[0]]["stages"][slot[1]] = item["stage"]
    by_id = {str(c.get("clip_id")): c for c in patched.get("clips", [])}
    for item in verdict.get("insertions", []):
        clip = by_id.get(str(item.get("clip_id"))) or patched["clips"][-1]
        stages = clip.setdefault("stages", [])
        position = max(0, min(int(item.get("after_stage", len(stages))), len(stages)))
        stages.insert(position, item["stage"])
    return patched


def qwen_default() -> str:
    return "__all__"


def call_model(*, base_url: str, model: str, payload: dict, schema: dict, max_tokens: int, timeout: float, analysis_tokens: int = 4096, notes: str = "", grammar: dict | None = None, profile: dict | None = None, fast: bool = False, outline_mode: str = "coverage", seed: int | None = None, ctx: PlannerContext) -> tuple[str, dict]:
    frame = frame_spec(profile) if profile else FRAMES["9:16"]
    system_prompt = pc_prompts.render_brief(ctx.system_prompt, ctx=ctx).replace("{frame_text}", frame["text"]).replace("{style_name}", STYLE_NAME[(profile or {}).get("style", "2d")])
    system_prompt += f"\n\n【画幅】{frame['text']}。{frame['composition']}。"
    if grammar:
        system_prompt += f"\n\n【全书视觉语法，camera 和 light 字段必须与之一致】{pc_prompts.grammar_text(grammar)}"
    system_prompt += (f"\n\n【本章导演意见，优先于一般偏好】{notes}" if notes else "")
    headers = {}
    from novel_manga.llm.config import endpoint_key
    api_key = endpoint_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    user_content = json.dumps(payload, ensure_ascii=False)
    started = time.monotonic()
    sampling = {"seed": seed} if seed is not None else {}
    endpoints = endpoint_order(payload.get("chapter_title", "") + str(payload.get("chapter_index", ""))) if base_url == qwen_default() else [base_url]
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        analysis, outline_attempts = generate_outline(
            client, endpoints, headers, model=model, payload=payload, mode=outline_mode,
            max_tokens=analysis_tokens, timeout=timeout, seed=seed,
        )
        analysis_seconds = round(time.monotonic() - started, 1)
        body = post_any(client, endpoints, headers, {
            "model": model,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            **sampling,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "thin_chapter_clips", "strict": True, "schema": schema},
            },
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
                {"role": "user", "content": "第一遍提纲已完整生成并通过检查。按完整提纲和原始请求输出最终镜头JSON，不要解释。\n完整提纲：" + analysis},
            ],
        })
    choice = body["choices"][0]
    content = choice["message"].get("content") or ""
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "usage": body.get("usage"),
        "analysis_usage": {key: sum((attempt.get("usage") or {}).get(key, 0) or 0 for attempt in outline_attempts)
                           for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "outline_attempts": outline_attempts,
        "outline_complete": True,
        "outline_content_chars": len(analysis),
        "outline_forwarded_chars": len(analysis),
        "analysis_seconds": analysis_seconds,
        "seconds": round(time.monotonic() - started, 1),
        "analysis": analysis,
        "outline_mode": outline_mode,
        "seed": seed,
    }
    return content, meta
