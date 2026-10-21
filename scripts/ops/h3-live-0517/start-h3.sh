#!/usr/bin/env bash
set -euo pipefail
if [[ ! -f "$H3_MODEL_PATH/model_index.json" ]]; then
  echo "Mount the MiniMax-H3 model root at $H3_MODEL_PATH" >&2
  exit 1
fi
if [[ ! -f "$H3_LORA" ]]; then
  echo "Mount the current Turbo LoRA at $H3_LORA" >&2
  exit 1
fi
mkdir -p "$H3_OUTPUT" "$TRITON_CACHE_DIR" "$TORCHINDUCTOR_CACHE_DIR"
exec python -u /opt/h3/app/entry.py serve \
  --backend sglang --model-type diffusion \
  --model-path "$H3_MODEL_PATH" --model-variant ref2va \
  --num-gpus "${H3_NUM_GPUS:-4}" --tp-size 2 \
  --sp-degree "${H3_SP:-2}" --ulysses-degree "${H3_SP:-2}" --ring-degree 1 \
  --encoder-parallel auto --performance-mode speed --attention-backend "${H3_ATTENTION:-fa}" \
  --lora-path "$H3_LORA" --lora-nickname h3-r2v-turbo --lora-alpha 8 --lora-scale 1 --lora-merge-mode auto \
  --warmup-mode off --host 0.0.0.0 --port "$H3_PORT" \
  --master-port "${H3_MASTER_PORT:-$((H3_PORT+100))}" \
  --scheduler-port "${H3_SCHEDULER_PORT:-$((H3_PORT+200))}" \
  --served-model-name minimax-h3-ref2va-turbo \
  --output-path "$H3_OUTPUT" --uvicorn-access-log-exclude-prefixes /v1/videos "$@"
