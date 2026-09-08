#!/usr/bin/env bash
# Run the conductor with the repo env loaded: scripts/conductor.sh --config configs/conductor.zhutian.json [--dry-run] [--once]
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; . ./.env; set +a
exec .venv/bin/python scripts/conductor_thin.py "$@"
