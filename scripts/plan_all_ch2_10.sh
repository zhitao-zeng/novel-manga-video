#!/usr/bin/env bash
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
SRC="outputs/ftj-anime-api10-v1/input/焚天纪-前10章-clean.md"
BIBLE=outputs/fentian-thin-v4/story_bible.json
for ch in 2 3 4 5 6 7 8 9 10; do
  DIR=outputs/fentian-thin-v4/fentian-thin-v4_$ch
  STATUS=outputs/fentian-thin-v4/.plan_status_$ch
  echo "[$(date +%H:%M:%S)] PLAN ch$ch start"
  rm -f "$DIR/planning_failed.json"
  if .venv/bin/python scripts/plan_chapter_thin.py "$SRC" --novel-id fentian-thin-v4 --title 焚天记 \
       --episode-index "$ch" --bible "$BIBLE" --output-root outputs > "outputs/fentian-thin-v4/plan_ep$ch.log" 2>&1 \
     && .venv/bin/python scripts/build_clip_plan_thin.py --episode-dir "$DIR" --bible "$BIBLE" >> "outputs/fentian-thin-v4/plan_ep$ch.log" 2>&1; then
    CLIPS=$(.venv/bin/python -c "import json;d=json.load(open('$DIR/clip_plan.json'));t=d['totals'];print(t['video_clip_count'],t['estimated_seconds'])" 2>/dev/null)
    echo ok > "$STATUS"
    echo "[$(date +%H:%M:%S)] PLAN ch$ch ok: $CLIPS clips/seconds"
  else
    echo failed > "$STATUS"
    echo "[$(date +%H:%M:%S)] PLAN ch$ch FAILED: $(grep -o '\"[^\"]*超过[^\"]*\"\|\"[^\"]*not verbatim[^\"]*\"\|\"[^\"]*neither cited[^\"]*\"' "$DIR/planning_failed.json" 2>/dev/null | head -2 | tr '\n' ' ')"
  fi
done
echo "[$(date +%H:%M:%S)] PLAN ALL DONE"
