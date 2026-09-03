#!/usr/bin/env bash
# After the planning driver ends: re-plan failed chapters; after the render
# driver ends: render every chapter that has an ok plan but no report.
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src:scripts NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1 PHANROUTER_INLINE_REFERENCE_IMAGES=1
N=outputs/fentian-3d-h-v1; SRC="outputs/ftj-anime-api10-v1/input/焚天纪-前10章-clean.md"
until grep -q 'PLAN ALL DONE' $N/plan_all.log 2>/dev/null; do sleep 60; done
for ch in 2 3 4 5 6 7 8 9 10; do
  [ "$(cat $N/.plan_status_$ch 2>/dev/null)" = ok ] && continue
  E=$N/fentian-3d-h-v1_$ch
  echo "[$(date +%H:%M:%S)] REPLAN ch$ch start"
  rm -f "$E/planning_failed.json"
  if .venv/bin/python scripts/plan_chapter_thin.py "$SRC" --novel-id fentian-3d-h-v1 --title 焚天记 --episode-index "$ch" --bible $N/story_bible.json --output-root outputs --max-redo 2 > "$N/plan_ep${ch}_retry.log" 2>&1 \
     && .venv/bin/python scripts/build_clip_plan_thin.py --episode-dir "$E" --bible $N/story_bible.json >> "$N/plan_ep${ch}_retry.log" 2>&1; then
    echo ok > "$N/.plan_status_$ch"
    echo "[$(date +%H:%M:%S)] REPLAN ch$ch ok: $(.venv/bin/python -c "import json;t=json.load(open('$E/clip_plan.json'))['totals'];print(t['video_clip_count'],'clips',t['estimated_seconds'],'s')")"
  else
    echo "[$(date +%H:%M:%S)] REPLAN ch$ch STILL FAILED: $(.venv/bin/python -c "import json;print(' | '.join(json.load(open('$E/planning_failed.json'))['errors'])[:220])" 2>/dev/null)"
  fi
done
echo "[$(date +%H:%M:%S)] REPLAN DONE"
until grep -q 'RENDER ALL DONE' $N/render_all.log 2>/dev/null; do sleep 60; done
for ch in 2 3 4 5 6 7 8 9 10; do
  E=$N/fentian-3d-h-v1_$ch
  [ "$(cat $N/.plan_status_$ch 2>/dev/null)" = ok ] || { echo "[$(date +%H:%M:%S)] RETRY ch$ch skipped (no plan)"; continue; }
  [ -f "$E/thin_media_report.json" ] && continue
  echo "[$(date +%H:%M:%S)] RETRY RENDER ch$ch start"
  if .venv/bin/python scripts/render_clips_thin.py --novel-dir $N --episode fentian-3d-h-v1_$ch --workers 5 > "$N/render_ep${ch}_retry.log" 2>&1; then
    echo "[$(date +%H:%M:%S)] RETRY RENDER ch$ch ok: $(.venv/bin/python -c "import json;a=json.load(open('$E/thin_media_report.json'))['assembly'];print(round(a['duration'],1),'s subs',a['subtitle_events'],'hold',a['max_hold_seconds'])")"
  else
    echo "[$(date +%H:%M:%S)] RETRY RENDER ch$ch PROBLEM: $(grep -E 'RuntimeError|Error' "$N/render_ep${ch}_retry.log" | tail -1 | cut -c1-160)"
  fi
done
echo "[$(date +%H:%M:%S)] FINISH ALL DONE"
