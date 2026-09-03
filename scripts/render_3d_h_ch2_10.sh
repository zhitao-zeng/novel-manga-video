#!/usr/bin/env bash
# Render chapters 2-10 of the 3D landscape set; each waits for its plan status.
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src:scripts NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1 PHANROUTER_INLINE_REFERENCE_IMAGES=1
N=outputs/fentian-3d-h-v1
for ch in 2 3 4 5 6 7 8 9 10; do
  until [ -f "$N/.plan_status_$ch" ]; do sleep 30; done
  if [ "$(cat "$N/.plan_status_$ch")" != ok ]; then echo "[$(date +%H:%M:%S)] RENDER ch$ch skipped (plan failed)"; continue; fi
  echo "[$(date +%H:%M:%S)] RENDER ch$ch start"
  if .venv/bin/python scripts/render_clips_thin.py --novel-dir $N --episode fentian-3d-h-v1_$ch --workers 5 > "$N/render_ep${ch}.log" 2>&1; then
    echo "[$(date +%H:%M:%S)] RENDER ch$ch ok: $(.venv/bin/python -c "import json;a=json.load(open('$N/fentian-3d-h-v1_$ch/thin_media_report.json'))['assembly'];print(round(a['duration'],1),'s subs',a['subtitle_events'],'hold',a['max_hold_seconds'])")"
  else
    echo "[$(date +%H:%M:%S)] RENDER ch$ch PROBLEM: $(grep -E 'RuntimeError|Error' "$N/render_ep${ch}.log" | tail -1 | cut -c1-160)"
  fi
done
echo "[$(date +%H:%M:%S)] RENDER ALL DONE"
