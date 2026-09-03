#!/usr/bin/env bash
cd /mnt/disk1/zengzhitao/novel-manga-video || exit 1
set -a; source .env; set +a
export PYTHONPATH=src NOVEL_PLANNER_BACKEND=deterministic NOVEL_CREATIVE_PROFILE=short-drama-adaptive-v1
export PHANROUTER_INLINE_REFERENCE_IMAGES=1
for ch in 3 4 5 6 7; do
  STATUS=outputs/fentian-thin-v4/.plan_status_$ch
  while [ ! -f "$STATUS" ]; do sleep 20; done
  if [ "$(cat "$STATUS")" != "ok" ]; then
    echo "[$(date +%H:%M:%S)] RENDER ch$ch skipped (planning failed)"
    continue
  fi
  echo "[$(date +%H:%M:%S)] RENDER ch$ch start"
  if .venv/bin/python scripts/render_clips_thin.py --novel-dir outputs/fentian-thin-v4 \
       --episode "fentian-thin-v4_$ch" --workers 4 > "outputs/fentian-thin-v4/render_ep$ch.log" 2>&1; then
    echo "[$(date +%H:%M:%S)] RENDER ch$ch ok: $(.venv/bin/python -c "import json;d=json.load(open('outputs/fentian-thin-v4/fentian-thin-v4_$ch/thin_media_report.json'))['assembly'];print(d['duration'],'s subs',d['subtitle_events'],'hold',d['max_hold_seconds'])" 2>/dev/null)"
  else
    echo "[$(date +%H:%M:%S)] RENDER ch$ch PROBLEM: $(tail -3 "outputs/fentian-thin-v4/render_ep$ch.log" | tr '\n' ' ' | cut -c1-200)"
  fi
done
echo "[$(date +%H:%M:%S)] RENDER ALL DONE"
