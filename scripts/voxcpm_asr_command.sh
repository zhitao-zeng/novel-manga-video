#!/bin/bash
set -euo pipefail

SHARED_HOST="/mnt/disk1/zengzhitao/novel-manga-video/.codex/research-loop/evidence/v53/output"
SHARED_CONTAINER="/output"
CONTAINER="${NOVEL_VOXCPM_CONTAINER:-novel-manga-v53-probes}"

UNIT_ID=""
AUDIO_HOST=""
TEXT=""
OUTPUT_HOST=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --unit-id) UNIT_ID="$2"; shift 2 ;;
        --audio) AUDIO_HOST="$2"; shift 2 ;;
        --text) TEXT="$2"; shift 2 ;;
        --output) OUTPUT_HOST="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [[ -z "$UNIT_ID" || -z "$AUDIO_HOST" || -z "$OUTPUT_HOST" ]]; then
    echo "Usage: --unit-id ID --audio AUDIO --text TEXT --output RESULT.json" >&2
    exit 1
fi

TMP_NAME="asr_$(date +%s%N)"
TMP_DIR_HOST="${SHARED_HOST}/tmp_asr"
TMP_AUDIO_HOST="${TMP_DIR_HOST}/${TMP_NAME}.wav"
TMP_JSON_HOST="${TMP_DIR_HOST}/${TMP_NAME}.json"
TMP_AUDIO_CONTAINER="${SHARED_CONTAINER}/tmp_asr/${TMP_NAME}.wav"
TMP_JSON_CONTAINER="${SHARED_CONTAINER}/tmp_asr/${TMP_NAME}.json"
mkdir -p "$TMP_DIR_HOST" "$(dirname "$OUTPUT_HOST")"
cp "$AUDIO_HOST" "$TMP_AUDIO_HOST"

cleanup() {
    rm -f "$TMP_AUDIO_HOST" "$TMP_JSON_HOST"
}
trap cleanup EXIT

docker exec "$CONTAINER" /opt/venvs/controller/bin/python \
    /app/runtime/local_model_cli.py asr \
    --unit-id "$UNIT_ID" --audio "$TMP_AUDIO_CONTAINER" --text "$TEXT" \
    --output "$TMP_JSON_CONTAINER" >&2
cp "$TMP_JSON_HOST" "$OUTPUT_HOST"
