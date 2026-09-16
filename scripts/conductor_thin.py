"""conductor_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import conductor_common_thin as conductor_common
import conductor_flow_thin as conductor_flow

def main() -> int:
    conductor_common.load_dotenv(conductor_common.REPO / ".env")  # before any worker inherits os.environ
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="one novel's conductor config (the older form)")
    parser.add_argument("--pipeline", help="the shared pipeline file; use with --novel")
    parser.add_argument("--novel", help="which novel of the pipeline file to run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="reading, planning and cards only; no rendering lanes")
    args = parser.parse_args()
    if args.pipeline:
        if not args.novel:
            raise SystemExit("--pipeline needs --novel")
        pipeline = json.loads(Path(args.pipeline).read_text(encoding="utf-8"))
        config = conductor_flow.config_for_novel(pipeline, args.novel)
        entry = next(n for n in pipeline["novels"] if n["id"] == args.novel)
        plan_only = args.plan_only or bool(entry.get("plan_only"))
    elif args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        plan_only = args.plan_only
    else:
        raise SystemExit("give either --config, or --pipeline with --novel")
    conductor_flow.Conductor(config, args.dry_run, plan_only).run(args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
