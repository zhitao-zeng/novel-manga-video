#!/usr/bin/env bash
# Render the 3D landscape chapters the main driver skipped or failed, in
# parallel with it; chapter 2 last, once its re-plan has landed.
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src:scripts NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1 PHANROUTER_INLINE_REFERENCE_IMAGES=1
N=outputs/fentian-3d-h-v1
render_one() {
  local ch=$1 E=$N/fentian-3d-h-v1_$1
  [ "$(cat $N/.plan_status_$ch 2>/dev/null)" = ok ] || { echo "[$(date +%H:%M:%S)] LEFT ch$ch skipped (no plan)"; return; }
  [ -f "$E/thin_media_report.json" ] && { echo "[$(date +%H:%M:%S)] LEFT ch$ch already done"; return; }
  echo "[$(date +%H:%M:%S)] LEFT RENDER ch$ch start"
  if .venv/bin/python scripts/render_clips_thin.py --novel-dir $N --episode fentian-3d-h-v1_$ch --workers 4 > "$N/render_ep${ch}_left.log" 2>&1; then
    echo "[$(date +%H:%M:%S)] LEFT RENDER ch$ch ok: $(.venv/bin/python -c "import json;a=json.load(open('$E/thin_media_report.json'))['assembly'];print(round(a['duration'],1),'s subs',a['subtitle_events'],'hold',a['max_hold_seconds'])")"
  else
    echo "[$(date +%H:%M:%S)] LEFT RENDER ch$ch PROBLEM: $(grep -E 'RuntimeError|Error' "$N/render_ep${ch}_left.log" | tail -1 | cut -c1-160)"
  fi
}
for ch in 4 6 7 5; do render_one $ch; done
until [ "$(cat $N/.plan_status_2 2>/dev/null)" = ok ] || grep -q 'STILL FAILED' $N/ch2_followup.log 2>/dev/null; do sleep 30; done
render_one 2
echo "FINISH ALL DONE" >> $N/finish.log   # releases the chapter-2 follow-up's wait
echo "[$(date +%H:%M:%S)] LEFTOVERS DONE"
