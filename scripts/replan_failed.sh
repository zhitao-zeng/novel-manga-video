#!/usr/bin/env bash
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
SRC="outputs/ftj-anime-api10-v1/input/焚天纪-前10章-clean.md"
BIBLE=outputs/fentian-thin-v4/story_bible.json
for ch in 8 7 10; do
  DIR=outputs/fentian-thin-v4/fentian-thin-v4_$ch
  echo "[$(date +%H:%M:%S)] REPLAN ch$ch start"
  rm -f "$DIR/planning_failed.json"
  if .venv/bin/python scripts/plan_chapter_thin.py "$SRC" --novel-id fentian-thin-v4 --title 焚天记 \
       --episode-index "$ch" --bible "$BIBLE" --output-root outputs --max-redo 2 \
       > "outputs/fentian-thin-v4/plan_ep${ch}_retry.log" 2>&1 \
     && .venv/bin/python scripts/build_clip_plan_thin.py --episode-dir "$DIR" --bible "$BIBLE" \
       >> "outputs/fentian-thin-v4/plan_ep${ch}_retry.log" 2>&1; then
    echo ok > "outputs/fentian-thin-v4/.plan_status_$ch"
    echo "[$(date +%H:%M:%S)] REPLAN ch$ch ok: $(.venv/bin/python -c "import json;t=json.load(open('$DIR/clip_plan.json'))['totals'];print(t['video_clip_count'],'clips',t['estimated_seconds'],'s')")"
  else
    echo "[$(date +%H:%M:%S)] REPLAN ch$ch STILL FAILED: $(.venv/bin/python -c "import json;print(' | '.join(json.load(open('$DIR/planning_failed.json'))['errors'])[:220])" 2>/dev/null)"
  fi
done
echo "[$(date +%H:%M:%S)] REPLAN DONE"
