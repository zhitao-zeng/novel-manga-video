"""planner_requests_thin responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.llm.config import endpoint_order
from novel_manga.llm.responses import extract_json
from novel_manga.llm.transport import post_any
from novel_manga.application.profiles import FRAMES
from novel_manga.application.profiles import style_name
from novel_manga.application.profiles import frame_spec
import httpx
import json
import os
import re
import time
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.contracts as pc_contracts
import novel_manga.planning.prompts as pc_prompts
import novel_manga.planning.text as pc_text
import novel_manga.planning.validation as pc_validation
from novel_manga.planning.methods import get_method
from novel_manga.planning.methods.base import StoryMethod
from novel_manga.planning.methods.blueprint import blueprint_schema, blueprint_prompt, validate_blueprint, normalize_blueprint
from novel_manga.planning.methods.prompts import screenplay_prompt

from novel_manga.planning.methods.errors import IncompleteOutlineError
from novel_manga.planning.binding import AUTHORED_FIELDS


def generate_outline(client: httpx.Client, endpoints: list[str], headers: dict, *, model: str, payload: dict,
                     mode: str, max_tokens: int, timeout: float, seed: int | None = None,
                     method: StoryMethod | None = None) -> tuple[str, list[dict]]:
    """Normal completion and an explicit complete artifact are prerequisites for pass two."""
    segment_ids = [s["segment_id"] for s in payload["segments"]]
    schema = blueprint_schema(method, payload['segments']) if method else pc_contracts.outline_schema(mode, segment_ids)
    instructions = blueprint_prompt(method) if method else pc_prompts.outline_prompt(mode)
    attempts = []
    draft_for_retry = None
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
            "response_format": {"type": "json_schema", "json_schema": {"name": f"chapter_outline_{method.key}" if method else "chapter_outline", "strict": True, "schema": schema}},
            "messages": [{"role": "system", "content": instructions + reminder},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }
        if method and draft_for_retry is not None:
            request['messages'].append({'role': 'user', 'content':
                '下面是上次已完整输出但尚未通过检查的提纲，仅修上方指出的错误区段，保留其余已成立的创作决策。'
                '原文优先于待修稿；仍按当前Schema输出全部beats_by_segment，编号由代码生成。\n待修稿：'
                + json.dumps(draft_for_retry, ensure_ascii=False)})
        try:
            client.timeout = httpx.Timeout(remaining)
            response = post_any(client, endpoints, headers, request)
            choice = response["choices"][0]
            content = choice["message"].get("content") or ""
            if method:
                row['model_content_chars'] = len(content)
                try:
                    normalized = normalize_blueprint(json.loads(content), payload['segments'])
                    content = json.dumps(normalized, ensure_ascii=False)
                    errors = validate_blueprint(content, method, payload['segments'])
                    if choice.get('finish_reason') == 'stop':
                        draft_for_retry = normalized
                except (ValueError, TypeError, AttributeError) as error:
                    errors = [str(error)]
            else:
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


def _patch_reply(raw, missing_ids, faulty, segments, names, locations, *, timeout, ctx, split_labels):
    from novel_manga.llm.client import ask_json
    segment_ids = [s["segment_id"] for s in segments]
    texts = {s["segment_id"]: s["text"] for s in segments}
    slots = pc_validation.stage_slots(raw)
    faulty = {label: errs for label, errs in faulty.items() if label in slots}
    clip_ids = [str(c.get("clip_id")) for c in raw.get("clips", []) if isinstance(c, dict)]
    if not clip_ids or not (missing_ids or faulty):
        raise ValueError("nothing to patch")
    prop_names = list(dict.fromkeys(p for clip in raw.get('clips', []) for stage in clip.get('stages', [])
                                   for p in [*(stage.get('props') or []), *(stage.get('wears') or {}).values()] if p))
    split_labels = set(split_labels) & set(faulty)
    binding_labels = set(faulty) if ctx.authored_storyboard and not split_labels else set()
    neighbouring_slots = list(slots.values())

    def stage_of(ids, *, binding_only=False):
        stage = pc_contracts.build_schema(names, locations, ids, ctx=ctx, prop_names=prop_names)["properties"]["clips"]["items"]["properties"]["stages"]["items"]
        if binding_only:
            stage["properties"] = {key: field for key, field in stage["properties"].items()
                                   if key not in AUTHORED_FIELDS}
            stage["required"] = [key for key in stage["required"] if key not in AUTHORED_FIELDS]
        return stage
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
                "properties": {"label": {"type": "string", "enum": list(faulty) or ["-"]},
                               "stage": stage_of(segment_ids, binding_only=bool(binding_labels) and not split_labels)}}},
        },
    }
    if split_labels:
        schema['properties']['replacements']['items']['properties']['continuations'] = {
            'type': 'array', 'maxItems': 6, 'items': stage_of(segment_ids)}
    parts = ["下面是一集短剧的分镜大纲。只输出需要修改的部分，不要改动其他阶段。"]
    if missing_ids:
        parts.append(f"原文里有 {len(missing_ids)} 个区段还没有任何阶段引用（{'、'.join(missing_ids)}）。为每个遗漏区段补写恰好一个阶段，插进最合适的 clip"
                     "（after_stage 是插在该 clip 第几个阶段之后，0 表示放在最前）；新阶段的 segment_id 必须是该遗漏区段。")
    if faulty and not binding_labels:
        parts.append(f"另有 {len(faulty)} 个阶段没过硬门检查，逐个重写整个阶段（label 原样填回，segment_id 不变），只修错误指出的问题，其余内容尽量保持。")
    elif faulty:
        parts.append(f"另有 {len(faulty)} 个阶段没过硬门检查（label 原样填回，segment_id 不变）。"
                     "未要求拆镜的阶段只修Schema中的绑定字段；作者只读上下文由调用方原样保留，不要回抄进stage。"
                     "缺失的可选字段保持原值；明确清除时返回空数组或null。"
                     "wears仅填写人物名到穿戴物的关系，作者字段、镜号、时长和叙事目的都不是穿戴者。"
                     "姿态纠正须结合相邻镜头的实际起止状态；同步纠正绑定actions中错误的姿态注记，保留动作主体和对象。"
                     "不要为迁就错误的坐站状态，虚构或重复姿态过渡；前镜已经站着时，本镜不能再从椅子上站起。"
                     "如果原文或作者演出明确需要先坐后起，而绑定只漏了自然落座过程，可在起止状态和绑定动作中补足一次必要的自然过渡；"
                     "不改作者关键事件、机位或对白，不把作者明确坐着的后续镜头一律改成站着。")
    if split_labels:
        parts.append(f"镜头{sorted(split_labels)}需要明确拆镜：stage写第一镜，continuations写后续完整镜头。"
                     f"每镜按实际对白估算不得超过{ctx.max_clip_seconds:g}秒，逐镜独立写起点、事件、末态、机位和光源。"
                     "后镜衔接已发生的结果，不重复进入、推门或飞离；保留说话人、全部必要对白和事实顺序。"
                     "心声与现场对白必须分镜，心声镜只保留同一位角色的心声；不得靠删除inner_monologue改成普通画外音。"
                     "其他问题阶段不得增加continuations。")
    from novel_manga.story.fields import field_instructions
    parts.append(field_instructions('planning'))
    # The picture rule is the same one validation applies, in the same words; the paid platforms
    # refuse blood and the local models do not, and a fix written for one book's stele used to be
    # sent to every book here.
    picture_rule = ("画面描述不得出现血液、伤口、破皮、流血，" + pc_constants.FORBIDDEN_VISUAL_FIX["血液或伤口"].split("；", 1)[-1]
                    if ctx.renderer_moderates else "")
    parts.append("规则：source_quote 从该区段原文逐字复制 8 到 120 字；" + (picture_rule + "；" if picture_rule else "")
                 + "offscreen_dialogue 和 chat_message 必须写 speaker_name；对白保持原文事实、意图与知识边界，允许等义口语改写；"
                 "心理叙述改为本人心声时，用本人视角表达自己的想法，不能保留导致指代变成其他人的第三人称；"
                 "chat_message仍逐字取自原文；只用给出的人物名，格式和已有阶段一致。")
    parts.append(f"分镜大纲：{json.dumps(outline, ensure_ascii=False)}")
    parts.append(f"可用人物：{names}")
    if ctx.story_blueprint:
        parts.append("保留每阶段的beat_id，不丢失该拍的动作和出口；提纲：" + json.dumps(ctx.story_blueprint, ensure_ascii=False))
    shown: list[str] = list(missing_ids)
    for label, errs in faulty.items():
        clip_index, stage_index = slots[label]
        stage = raw["clips"][clip_index]["stages"][stage_index]
        heading = f"问题阶段 {label}\n错误：" + " | ".join(errs)
        if label in binding_labels:
            fields = stage_of(segment_ids, binding_only=True)['properties']
            current = {key: value for key, value in stage.items() if key in fields}
            # Mechanical IDs and import bookkeeping have no bearing on the requested binding.
            # The author-owned performance remains available, separately from writable fields.
            readonly = {key: stage[key] for key in AUTHORED_FIELDS if key in stage
                        and key in {'event', 'shot_scale', 'camera', 'duration_seconds', 'purpose', 'sfx', 'turns'}}
            parts.append(heading + f"\n可修改绑定内容：{json.dumps(current, ensure_ascii=False)}"
                         + f"\n作者只读上下文（仅供核对，调用方原样保留，不输出）：{json.dumps(readonly, ensure_ascii=False)}")
            position = neighbouring_slots.index((clip_index, stage_index))
            neighbours = {}
            for title, index in (('前镜', position - 1), ('后镜', position + 1)):
                if 0 <= index < len(neighbouring_slots):
                    ci, si = neighbouring_slots[index]
                    neighbour_clip = raw['clips'][ci]
                    neighbour = neighbour_clip['stages'][si]
                    neighbours[title] = {
                        'label': next(name for name, slot in slots.items() if slot == (ci, si)),
                        'location': neighbour.get('location') or neighbour_clip.get('location'),
                        **{key: neighbour[key] for key in ('scene_id', 'scene_time', 'start_state',
                           'event', 'end_state', 'in_frame', 'actions') if key in neighbour}}
            parts.append('相邻镜头只读衔接（场景/时间与人物承接以这些实际字段及原文为据，不输出）：'
                         + json.dumps(neighbours, ensure_ascii=False))
        else:
            parts.append(heading + f"\n当前内容：{json.dumps(stage, ensure_ascii=False)}")
        shown.append(str(stage.get("segment_id")))
    for sid in dict.fromkeys(shown):
        if sid in texts:
            parts.append(f"区段 {sid} 原文：\n{texts[sid][:1800]}")
    budget = min(10000, 800 + 900 * (len(missing_ids) + len(faulty)) + 1600 * len(split_labels))
    return ask_json([{"type": "text", "text": "\n\n".join(parts)}], schema, name="plan_patch", max_tokens=budget, timeout=timeout, retry_truncated=False)


def _apply_patch(raw, verdict, split_labels, *, authored=False):
    """All reply addresses refer to this original draft, including across batches."""
    slots = pc_validation.stage_slots(raw)
    split_labels = set(split_labels)
    patched = json.loads(json.dumps(raw, ensure_ascii=False))
    replacements = {}
    for item in verdict.get('replacements', []):
        label = str(item.get('label'))
        slot = slots.get(label)
        if not slot or not isinstance(item.get('stage'), dict):
            raise ValueError('patch returned an unknown or incomplete replacement')
        extra = item.get('continuations') or []
        if extra and label not in split_labels:
            raise ValueError('patch split a stage that was not requested for splitting')
        original = raw['clips'][slot[0]]['stages'][slot[1]]
        if label in split_labels and any(t.get('inner_monologue') for t in original.get('turns') or []):
            from novel_manga.story.voice_delivery import speech_sequence
            if speech_sequence([original]) != speech_sequence([item['stage'], *extra]):
                raise ValueError('voice split changed authored words, speaker, delivery or inner_monologue')
        if authored and label not in split_labels:
            saved = patched['clips'][slot[0]]['stages'][slot[1]]
            corrected = {**saved, **item['stage']}
            corrected.update({key: saved[key] for key in AUTHORED_FIELDS if key in saved})
            replacements[slot] = [corrected]
        else:
            replacements[slot] = [item['stage'], *extra]
    insertions = {}
    counts = {str(c['clip_id']): len(c.get('stages') or []) for c in raw['clips']}
    for item in verdict.get('insertions', []):
        clip_id, position = str(item['clip_id']), int(item['after_stage'])
        if clip_id not in counts or not 0 <= position <= counts[clip_id]:
            raise ValueError('patch insertion is outside the original stage range')
        insertions.setdefault((clip_id, position), []).append(item['stage'])
    # Every label and insertion position refers to the ORIGINAL draft. Expanding
    # an early stage must not shift a later replacement or insertion onto another one.
    for ci, clip in enumerate(patched['clips']):
        original = clip.get('stages') or []
        stages = list(insertions.get((str(clip['clip_id']), 0), []))
        for si, stage in enumerate(original):
            stages.extend(replacements.get((ci, si), [stage]))
            stages.extend(insertions.get((str(clip['clip_id']), si + 1), []))
        clip['stages'] = stages
    return patched


def patch_plan(raw: dict, missing_ids: list[str], faulty: dict[str, list[str]], segments: list[dict], names: list[str], locations: list[str], *, timeout: float = pc_constants.PATCH_TIMEOUT_SECONDS, ctx: PlannerContext, split_labels=()) -> dict:
    """Make one local proposal; the caller validates the copied draft before adopting it."""
    reply = _patch_reply(raw, missing_ids, faulty, segments, names, locations,
                         timeout=timeout, ctx=ctx, split_labels=split_labels)
    return _apply_patch(raw, reply, split_labels, authored=ctx.authored_storyboard)


def patch_batches(raw, missing_ids, faulty, segments, names, locations, *, timeout, total_timeout,
                  ctx, split_labels=()):
    """Yield completed small proposals inside the existing shared time allowance.

    Replies always address the original draft. Merging them there avoids shifting
    a later repair onto another shot when an earlier repair adds continuations.
    The flow writes each completed proposal using its existing patch checkpoint.
    """
    split_labels = set(split_labels) & set(faulty)
    tasks = [('insertion', sid, 1) for sid in missing_ids]
    tasks += [('replacement', label, 3 if label in split_labels else 1) for label in faulty]
    batches, batch, weight = [], [], 0
    for task in tasks:
        if batch and weight + task[2] > 4:
            batches.append(batch)
            batch, weight = [], 0
        batch.append(task)
        weight += task[2]
    if batch:
        batches.append(batch)
    # Small repairs keep their current request and call behaviour.
    if len(batches) <= 1:
        yield {'draft': patch_plan(raw, missing_ids, faulty, segments, names, locations,
                                  timeout=min(timeout, total_timeout), ctx=ctx, split_labels=split_labels)}
        return
    deadline = time.monotonic() + total_timeout
    combined = {'insertions': [], 'replacements': []}
    for number, batch in enumerate(batches, 1):
        missing = [value for kind, value, _ in batch if kind == 'insertion']
        errors = {value: faulty[value] for kind, value, _ in batch if kind == 'replacement'}
        row = {'batch': number, 'segments': missing, 'stages': list(errors)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield {**row, 'failed': 'TimeoutError: shared patch time budget exhausted'}
            break
        started = time.monotonic()
        try:
            reply = _patch_reply(raw, missing, errors, segments, names, locations,
                                 timeout=min(timeout, remaining), ctx=ctx,
                                 split_labels=split_labels & set(errors))
            labels = [item.get('label') for item in reply.get('replacements', [])]
            inserted = [item.get('stage', {}).get('segment_id') for item in reply.get('insertions', [])]
            if sorted(labels) != sorted(errors) or sorted(inserted) != sorted(missing):
                raise ValueError('patch did not return exactly its requested stages and segments')
            candidate = {key: [*combined[key], *reply.get(key, [])] for key in combined}
            patched = _apply_patch(raw, candidate, split_labels, authored=ctx.authored_storyboard)
        except Exception as error:  # noqa: BLE001 - other batches retain their own proposals
            yield {**row, 'failed': f'{type(error).__name__}: {str(error)[:120]}',
                   'elapsed_seconds': round(time.monotonic() - started, 1)}
            continue
        combined = candidate
        yield {**row, 'draft': patched, 'elapsed_seconds': round(time.monotonic() - started, 1)}


def qwen_default() -> str:
    return "__all__"


def call_model(*, base_url: str, model: str, payload: dict, schema: dict, max_tokens: int, timeout: float, analysis_tokens: int = 4096, notes: str = "", grammar: dict | None = None, profile: dict | None = None, fast: bool = False, outline_mode: str = "coverage", seed: int | None = None, ctx: PlannerContext, scene_script: dict | None = None) -> tuple[str, dict]:
    # A sheet somebody already cut is not a chapter to write.  This branch used to be unconditional
    # and first, so a book that names a local story_method took it even for a chapter holding an
    # accepted sandbox storyboard: the method wrote its own scenes and merge() then failed on an
    # authored shot it had never produced.  Binding what exists comes before choosing how to invent.
    method = None if ctx.authored_storyboard else get_method(ctx.story_method or (profile or {}).get('story_method'))
    ctx.story_blueprint = {}
    if method:
        from novel_manga.application.planning.method_pipeline import generate
        from novel_manga.llm.config import endpoint_key
        key = endpoint_key()
        enriched = {**payload, 'production_profile': profile or payload.get('production_profile', {})}
        if grammar:
            enriched['visual_grammar'] = grammar
        return generate(method=method, payload=enriched, model=model,
                        endpoints=endpoint_order(payload.get('chapter_title', '')) if base_url == qwen_default() else [base_url],
                        headers={'Authorization': f'Bearer {key}'} if key else {}, post=post_any,
                        max_tokens=max_tokens, scene_tokens=analysis_tokens, timeout=timeout, seed=seed, notes=notes, ctx=ctx,
                        scene_script=scene_script)
    if scene_script is not None:
        raise ValueError('复用分场稿需要指定创作方法')
    frame = frame_spec(profile) if profile else FRAMES["9:16"]
    brief = screenplay_prompt(method) if method else ctx.system_prompt
    system_prompt = pc_prompts.render_brief(brief, ctx=ctx).replace("{frame_text}", frame["text"]).replace("{style_name}", style_name((profile or {}).get("style", "2d")))
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
            **({'method': method} if method else {}),
        )
        if method:
            ctx.story_blueprint = json.loads(analysis)
            schema = pc_contracts.bind_blueprint_schema(schema, ctx.story_blueprint)
        analysis_seconds = round(time.monotonic() - started, 1)
        # Scene-exit question-and-answer pairs, straight from the source: both writers (the sandbox
        # sheet and the local two-pass) dropped the pair that carries 诊室 into 公交站 - the outline
        # may compress prose, but a question whose answer is the NEXT SCENE cannot be lost silently.
        pairs = pc_text.handoff_pairs("\n".join(str(s.get("text") or "") for s in payload.get("segments") or []))
        pair_text = ("；".join(f"问「{q}」答「{a}」" for q, a in pairs[:8]) or "（本章无）")
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
                {"role": "user", "content":
                    "第一遍提纲已完成（检查只验证了结构完整，内容质量由你负责）。按提纲和原始请求写最终镜头JSON，不要解释。落实提纲时遵守：\n"
                    "1. 提纲 scene_handoffs 里每场的「开始于…」就是这一场第一个镜头的画面内容——新场景的第一镜必须画新场景的画面，"
                    "不得把上一场的画面或事件句复制进 start_state；「结束于…」是这一场最后一镜之后留下的状态或问题，用可听的对白或可见的画面交代，不能只在 event 里描述。\n"
                    "2. 提纲 retained_dialogue 列出的关键台词必须逐条进入对应镜头的 turns：包括成对的问答（提问和它的回答/画面回答都要有），"
                    "一句都不许只留在提纲里。\n"
                    "3. 换场前后时段连续（提纲没有写时间跳跃就是同一天同一时段），地点卡的默认时段不覆盖故事连续性。\n"
                    "4. 下面这些原文问答对是换场的关键交接，无论提纲是否保留，都必须有对应的镜头表达（提问和回答都要可听，或回答由下一场第一镜的画面直接给出）："
                    + pair_text + "\n"
                    "完整提纲：" + analysis},
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
        "outline_mode": f"method:{method.key}" if method else outline_mode,
        "seed": seed,
        **({'story_method': method.describe()} if method else {}),
    }
    return content, meta
