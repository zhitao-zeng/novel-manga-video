from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .ingest import read_novel
from .models import (
    ChapterDiagnosis,
    EpisodePlan,
    ScriptQualityReport,
    SeriesState,
    ShowrunnerPlan,
    StoryBible,
)
from .pipeline import NovelPipeline
from .planning_export import (
    compile_planning_bundle,
    export_planning_bundle,
    repair_planning_bundle,
)
from .planning_validation import validate_planning_bundle
from .production_models import ProductionPlan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-manga", description="小说文档转 9:16 国漫画风漫剧")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect", help="只解析文档和检查分集，不调用生成 API")
    inspect.add_argument("input")
    inspect.add_argument("--novel-id", required=True)
    inspect.add_argument("--title")

    subparsers.add_parser("contract", help="输出与具体大模型无关的规划和生产 JSON Schema")

    plan = subparsers.add_parser("plan", help="只调用规划模型并输出校验后的故事圣经和分镜，不生成媒体")
    plan.add_argument("input")
    plan.add_argument("--novel-id", required=True)
    plan.add_argument("--title")
    plan.add_argument("--output", default="outputs/plans")

    validate_plan = subparsers.add_parser(
        "validate-plan", help="校验已生成的故事圣经、分镜、原文引用和可见说话人"
    )
    validate_plan.add_argument("input")
    validate_plan.add_argument("--bundle", required=True)
    validate_plan.add_argument("--novel-id", required=True)
    validate_plan.add_argument("--title")
    validate_plan.add_argument("--output")

    repair_plan = subparsers.add_parser(
        "repair-plan", help="把规划模型输出的角色别名归一到故事圣经资产名"
    )
    repair_plan.add_argument("--bundle", required=True)

    compile_plan = subparsers.add_parser(
        "compile-plan", help="把规划 JSON 编译成下游生产任务，不调用媒体生成 API"
    )
    compile_plan.add_argument("input")
    compile_plan.add_argument("--bundle", required=True)
    compile_plan.add_argument("--novel-id", required=True)
    compile_plan.add_argument("--title")

    generate = subparsers.add_parser("generate", help="生成完整提交目录")
    generate.add_argument("input")
    generate.add_argument("--novel-id", required=True)
    generate.add_argument("--title")
    generate.add_argument("--provider", choices=("mock", "phanrouter", "command"), default="mock")
    generate.add_argument("--output", default="outputs")
    generate.add_argument("--bgm", help="可选的全书统一背景音乐")
    generate.add_argument(
        "--admission-mode",
        choices=("preview", "production"),
        help="preview 仅工程验收；production 强制 ASR 证据后端",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "contract":
            print(json.dumps({
                "contract": "novel-manga-production/v1",
                "planner_contract": "novel-manga-planner/v4",
                "codex_required": False,
                "planner_operations": [
                    "build_bible",
                    "diagnose_episode",
                    "plan_showrunner",
                    "plan_episode",
                    "review_episode",
                    "update_series_state",
                ],
                "planner_repair_protocol": {
                    "bounded": True,
                    "max_revisions_environment": "NOVEL_PLANNER_MAX_REVISIONS",
                    "command_request_field": "repair",
                },
                "story_bible_schema": StoryBible.model_json_schema(),
                "chapter_diagnosis_schema": ChapterDiagnosis.model_json_schema(),
                "showrunner_plan_schema": ShowrunnerPlan.model_json_schema(),
                "episode_plan_schema": EpisodePlan.model_json_schema(),
                "script_quality_schema": ScriptQualityReport.model_json_schema(),
                "series_state_schema": SeriesState.model_json_schema(),
                "production_plan_schema": ProductionPlan.model_json_schema(),
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "inspect":
            novel = read_novel(args.input, novel_id=args.novel_id, title=args.title)
            result = {
                "novel_id": novel.novel_id,
                "title": novel.title,
                "chaptered": novel.chaptered,
                "video_count": len(novel.episodes),
                "episodes": [
                    {"index": item.index, "title": item.source_title, "text_count": item.text_count}
                    for item in novel.episodes
                ],
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "plan":
            settings = Settings.from_env(
                provider="mock",
                output_root=args.output,
                admission_mode="preview",
            )
            result = export_planning_bundle(
                settings,
                args.input,
                novel_id=args.novel_id,
                title=args.title,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-plan":
            result = validate_planning_bundle(
                args.input,
                args.bundle,
                novel_id=args.novel_id,
                title=args.title,
                output=args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 2

        if args.command == "repair-plan":
            result = repair_planning_bundle(args.bundle)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "compile-plan":
            result = compile_planning_bundle(
                args.input,
                args.bundle,
                novel_id=args.novel_id,
                title=args.title,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        settings = Settings.from_env(
            provider=args.provider,
            output_root=args.output,
            bgm_path=args.bgm,
            admission_mode=args.admission_mode,
        )
        manifest = NovelPipeline(settings).generate(args.input, novel_id=args.novel_id, title=args.title)
        print(manifest.model_dump_json(indent=2))
        return 0 if all(video.status == "succeeded" for video in manifest.videos) else 2
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
