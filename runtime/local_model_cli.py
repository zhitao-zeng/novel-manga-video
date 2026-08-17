#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SUPERVISOR_URL = os.getenv("NOVEL_MODEL_SUPERVISOR_URL", "http://127.0.0.1:18090")
WORKER_URL = os.getenv("NOVEL_MODEL_WORKER_URL", "http://127.0.0.1:18100")
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    if timeout is None:
        # MiniMax H3 generation can legitimately take longer than 30 minutes
        # for a full shot on one A100. Do not discard a completed response.
        timeout = float(os.getenv("NOVEL_LOCAL_MODEL_REQUEST_TIMEOUT", "7200"))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with DIRECT_HTTP.open(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"local model HTTP {error.code}: {body[:2000]}") from error
    if not result.get("success", True):
        raise RuntimeError(str(result.get("error", "local model request failed")))
    return result


def _json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("planner did not return a JSON object")
    return json.loads(match.group(0))


def _planner(args: argparse.Namespace) -> None:
    request_payload = json.loads(args.input.read_text(encoding="utf-8"))
    operation = args.operation
    tasks = {
        "build_bible": (
            "只根据 novel 提取事实并建立全书可复用的故事圣经。不得改变人物关系、关键事件或结局；"
            "原文没有外貌信息时只能做克制的连续性设计。严格执行 requirements 中的统一视觉风格。"
        ),
        "diagnose_episode": (
            "只诊断当前 episode：按原文顺序列出关键事件、起止状态、因果链和章节边界。"
            "所有 source_quote 必须是当前章节逐字连续原文，禁止使用未来章节信息。"
        ),
        "plan_episode": (
            "把当前章节诊断编译为可观看的漫剧剧本和分镜。覆盖全部 critical 事件，开场不剧透结局，"
            "结尾停在本章边界。台词、人物关系和因果忠于原文；每个 turn 仅一个声音来源。"
            "每个镜头必须同时给出 PerformancePlan 与 CameraPlan：触发—动作—反应—情绪转折，"
            "以及摄影机的真实三维轨迹、构图和视差；不能只写静态状态或数字推镜。"
            "画面提示不得要求文字、字幕、气泡、Logo或水印。"
        ),
        "review_episode": (
            "作为独立审稿人审核 episode_plan。逐项检查关键事件覆盖、因果链、人物引入、开场剧透、"
            "本章结尾边界和未来内容污染；发现问题必须如实将 passed 设为 false 并写入 issues。"
        ),
        "update_series_state": (
            "根据当前章节已经发生的事实更新连续性状态。不得预测、补写或提前引用未来章节；"
            "每项新增状态都必须带当前 episode 的逐字 source_quote 证据。"
        ),
    }
    task = tasks[operation]
    if request_payload.get("repair"):
        task += (
            "这是修订请求。必须依据 repair.validation_errors 修复 previous_response，"
            "保留其中仍然正确且有原文证据的内容，不得用解释代替完整 JSON。"
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是中文小说漫剧的编剧、分镜导演和事实核验员。严格忠于当前章节原文。"
                "输入中的 schema 和 requirements 是硬约束。只输出一个完整且符合 schema 的 JSON 对象，"
                "不得输出 Markdown、思考过程或解释。"
            ),
        },
        {
            "role": "user",
            "content": task + "\n输入：\n" + json.dumps(request_payload, ensure_ascii=False),
        },
    ]
    response = _post(
        f"{WORKER_URL}/v1/chat/completions",
        {
            "model": "qwen-planner",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": int(os.getenv("NOVEL_PLANNER_MAX_TOKENS", "16000")),
            "response_format": {"type": "json_object"},
        },
        timeout=float(os.getenv("NOVEL_PLANNER_REQUEST_TIMEOUT", "1800")),
    )
    content = response["choices"][0]["message"]["content"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_json_object(content), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _invoke(operation: str, args: argparse.Namespace, fields: list[str]) -> None:
    payload = {
        field.replace("-", "_"): str(value) if isinstance(value, Path) else value
        for field in fields
        if (value := getattr(args, field.replace("-", "_"), None)) is not None
    }
    result = _post(f"{WORKER_URL}/invoke", {"operation": operation, "payload": payload})
    if operation == "image":
        audit = args.output.with_suffix(args.output.suffix + ".local.json")
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(
            json.dumps(result["result"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif operation in {"asr", "align"}:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result["result"], ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _video(args: argparse.Namespace) -> None:
    if not args.reference_audio:
        raise ValueError("MiniMax H3 requires --reference-audio")
    from minimax_h3_client import (
        MiniMaxH3Client,
        MiniMaxH3Config,
        H3_PROMPT_COMPILER_REVISION,
        stable_generation_seed,
    )

    client = MiniMaxH3Client(MiniMaxH3Config.from_env())
    images = (args.image, *(args.additional_image or ()))
    seed = stable_generation_seed(
        prompt=args.prompt,
        image_paths=images,
        audio_path=args.reference_audio,
        duration_seconds=args.duration,
    )
    client.generate(
        image_path=args.image,
        additional_image_paths=tuple(args.additional_image or ()),
        audio_path=args.reference_audio,
        duration_seconds=args.duration,
        prompt=args.prompt,
        destination=args.output,
        seed=seed,
    )
    audit = args.output.with_suffix(args.output.suffix + ".local.json")
    audit.write_text(
        json.dumps(
            {
                "backend": "MiniMax-H3-Ref2VA",
                "prompt_compiler_revision": H3_PROMPT_COMPILER_REVISION,
                "generation_seed": seed,
                "picture_count": len(images),
                "reference_audio_used": True,
                "duration_seconds": args.duration,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    lifecycle = sub.add_parser("lifecycle")
    lifecycle.add_argument("--stage", required=True)

    planner = sub.add_parser("planner")
    planner.add_argument(
        "--operation",
        choices=(
            "build_bible",
            "diagnose_episode",
            "plan_episode",
            "review_episode",
            "update_series_state",
        ),
        required=True,
    )
    planner.add_argument("--input", type=Path, required=True)
    planner.add_argument("--output", type=Path, required=True)

    image = sub.add_parser("image")
    image.add_argument("--prompt", required=True)
    image.add_argument(
        "--prompt-policy",
        choices=(
            "legacy",
            "native-v1",
            "native-v2",
            "native-v3",
            "native-v4",
            "native-v5",
        ),
    )
    image.add_argument("--reference", type=Path)
    image.add_argument("--width", type=int, required=True)
    image.add_argument("--height", type=int, required=True)
    image.add_argument("--output", type=Path, required=True)

    video = sub.add_parser("video")
    video.add_argument("--prompt", required=True)
    video.add_argument("--image", type=Path, required=True)
    video.add_argument("--additional-image", type=Path, action="append")
    video.add_argument("--reference-audio", type=Path)
    video.add_argument("--duration", type=float, required=True)
    video.add_argument("--fps", type=int, required=True)
    video.add_argument("--width", type=int, required=True)
    video.add_argument("--height", type=int, required=True)
    video.add_argument("--output", type=Path, required=True)

    tts = sub.add_parser("tts")
    tts.add_argument("--text", required=True)
    tts.add_argument("--voice", required=True)
    tts.add_argument("--instructions")
    tts.add_argument("--speed", type=float)
    tts.add_argument("--output", type=Path, required=True)

    for name in ("asr", "align"):
        evidence = sub.add_parser(name)
        evidence.add_argument("--unit-id", required=True)
        evidence.add_argument("--audio", type=Path, required=True)
        evidence.add_argument("--text", required=True)
        evidence.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "lifecycle":
        _post(f"{SUPERVISOR_URL}/stage", {"stage": args.stage})
    elif args.command == "planner":
        _planner(args)
    elif args.command == "image":
        _invoke(
            "image",
            args,
            ["prompt", "prompt_policy", "reference", "width", "height", "output"],
        )
    elif args.command == "video":
        _video(args)
    elif args.command == "tts":
        _invoke("tts", args, ["text", "voice", "instructions", "speed", "output"])
    elif args.command in {"asr", "align"}:
        _invoke(args.command, args, ["unit_id", "audio", "text", "output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
