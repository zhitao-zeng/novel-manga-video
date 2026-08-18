#!/bin/bash
# Host-side wrapper to call VoxCPM TTS inside the novel-manga-v53-probes container
# Handles path mapping between host and container

SHARED_HOST="/mnt/disk1/zengzhitao/novel-manga-video/.codex/research-loop/evidence/v53/output"
SHARED_CONTAINER="/output"
CONTAINER="novel-manga-v53-probes"

OUTPUT_HOST=""
TEXT=""
VOICE=""
INSTRUCTIONS=""
SPEED=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) OUTPUT_HOST="$2"; shift 2 ;;
        --text) TEXT="$2"; shift 2 ;;
        --voice) VOICE="$2"; shift 2 ;;
        --instructions) INSTRUCTIONS="$2"; shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$OUTPUT_HOST" ] || [ -z "$TEXT" ]; then
    echo "Usage: --text TEXT --voice VOICE --output AUDIO.wav" >&2
    exit 1
fi

# Create a unique temp file in the shared directory
TMP_NAME="tts_$(date +%s%N).wav"
TMP_HOST="${SHARED_HOST}/tmp_tts/${TMP_NAME}"
TMP_CONTAINER="${SHARED_CONTAINER}/tmp_tts/${TMP_NAME}"
mkdir -p "${SHARED_HOST}/tmp_tts"

# Build args
ARGS=(--text "$TEXT" --voice "${VOICE:-alloy}" --output "$TMP_CONTAINER")
[ -n "$INSTRUCTIONS" ] && ARGS+=(--instructions "$INSTRUCTIONS")
[ -n "$SPEED" ] && ARGS+=(--speed "$SPEED")

# Call VoxCPM inside the container
docker exec "$CONTAINER" /opt/venvs/controller/bin/python /app/runtime/local_model_cli.py tts "${ARGS[@]}" >&2
RESULT=$?

if [ $RESULT -ne 0 ] || [ ! -f "$TMP_HOST" ]; then
    echo "VoxCPM TTS failed" >&2
    exit 1
fi

# Copy result to the requested host path
cp "$TMP_HOST" "$OUTPUT_HOST"
rm -f "$TMP_HOST"
echo "TTS output written to $OUTPUT_HOST" >&2
