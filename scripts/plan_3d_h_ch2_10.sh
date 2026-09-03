#!/usr/bin/env bash
# Plan chapters 2-10 for the 3D landscape set (GPU only, no paid calls).
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src:scripts NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
N=outputs/fentian-3d-h-v1; SRC="outputs/ftj-anime-api10-v1/input/焚天纪-前10章-clean.md"
for ch in 2 3 4 5 6 7 8 9 10; do
  E=$N/fentian-3d-h-v1_$ch
  echo "[$(date +%H:%M:%S)] PLAN ch$ch start"
  rm -f "$E/planning_failed.json"
  if .venv/bin/python scripts/plan_chapter_thin.py "$SRC" --novel-id fentian-3d-h-v1 --title 焚天记 --episode-index "$ch" --bible $N/story_bible.json --output-root outputs --max-redo 2 > "$N/plan_ep${ch}.log" 2>&1 \
     && .venv/bin/python scripts/build_clip_plan_thin.py --episode-dir "$E" --bible $N/story_bible.json >> "$N/plan_ep${ch}.log" 2>&1; then
    echo ok > "$N/.plan_status_$ch"
    echo "[$(date +%H:%M:%S)] PLAN ch$ch ok: $(.venv/bin/python -c "import json;t=json.load(open('$E/clip_plan.json'))['totals'];print(t['video_clip_count'],'clips',t['estimated_seconds'],'s')")"
  else
    echo failed > "$N/.plan_status_$ch"
    echo "[$(date +%H:%M:%S)] PLAN ch$ch FAILED: $(.venv/bin/python -c "import json;print(' | '.join(json.load(open('$E/planning_failed.json'))['errors'])[:200])" 2>/dev/null)"
  fi
done
echo "[$(date +%H:%M:%S)] PLAN ALL DONE"
